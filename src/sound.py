"""Music selection, music envelopes and SFX (user files or built-in synthesized fallbacks)."""

import random
import wave
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from src.utils import ASSETS_DIR, CACHE_DIR, MUSIC_DIR, logger

SR = 44100
SFX_DIR = ASSETS_DIR / "sfx"
SYNTH_DIR = CACHE_DIR / "sfx_synth"
_AUDIO_EXT = (".wav", ".mp3", ".ogg", ".m4a")


# ── Music ─────────────────────────────────────────────────────────────────────

def pick_music(moods: Sequence[str] = ()) -> Optional[Path]:
    """Picks a track from assets/music/, preferring a <mood>/ subfolder or a filename containing the mood."""
    if not MUSIC_DIR.exists():
        return None
    tracks = [p for p in MUSIC_DIR.rglob("*") if p.suffix.lower() in _AUDIO_EXT]
    if not tracks:
        return None
    for mood in moods:
        m = mood.lower()
        hits = [p for p in tracks if m in p.relative_to(MUSIC_DIR).as_posix().lower()]
        if hits:
            return random.choice(hits)
    if moods:
        logger.info(f"No music matching moods {list(moods)} in {MUSIC_DIR}; using a random track")
    return random.choice(tracks)


def envelope(
    breakpoints: List[Tuple[float, float]],
    silences: Sequence[Tuple[float, float]] = (),
    fade: float = 0.05,
) -> Callable[[np.ndarray], np.ndarray]:
    """Vectorised gain(t): linear interpolation over breakpoints, multiplied by silence windows."""
    xs = np.array([b[0] for b in breakpoints] or [0.0])
    ys = np.array([b[1] for b in breakpoints] or [1.0])

    def gain(t):
        t = np.asarray(t, dtype=float)
        g = np.interp(t, xs, ys)
        for a, b in silences:
            inside = np.clip(np.minimum((t - a) / fade, (b - t) / fade), 0.0, 1.0)
            g = g * (1.0 - inside)
        return g

    return gain


def apply_gain(clip, gain: Callable[[np.ndarray], np.ndarray], offset: float = 0.0):
    """Applies a time-varying gain to a MoviePy audio clip (offset = clip start in the timeline)."""
    def fl(gf, t):
        frame = gf(t)
        g = gain(np.asarray(t) + offset)
        return frame * (g[:, None] if np.ndim(g) and np.ndim(frame) == 2 else g)
    return clip.fl(fl, keep_duration=True)


# ── SFX lookup ────────────────────────────────────────────────────────────────

def sfx_path(sound: str) -> Optional[Path]:
    """User file assets/sfx/<sound>.<ext> wins; otherwise a synthesized fallback is generated once."""
    for ext in _AUDIO_EXT:
        p = SFX_DIR / f"{sound}{ext}"
        if p.exists():
            return p
    synth = SYNTHS.get(sound)
    if synth is None:
        return None
    SYNTH_DIR.mkdir(parents=True, exist_ok=True)
    out = SYNTH_DIR / f"{sound}.wav"
    if not out.exists():
        _write_wav(out, synth())
    return out


def _write_wav(path: Path, mono: np.ndarray, peak: float = 0.5) -> None:
    mono = mono / (np.max(np.abs(mono)) + 1e-9) * peak
    stereo = np.repeat((mono * 32767).astype(np.int16)[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())


# ── Synthesizers (deterministic, no downloads) ────────────────────────────────

_rng = np.random.default_rng(1518)


def _t(dur: float) -> np.ndarray:
    return np.arange(int(SR * dur)) / SR


def _noise(dur: float) -> np.ndarray:
    return _rng.standard_normal(int(SR * dur))


def _band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    spec[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(spec, len(x))


def _thump(freq: float, dur: float, decay: float) -> np.ndarray:
    t = _t(dur)
    sweep = freq * (1 + 1.5 * np.exp(-t * 40))
    return np.sin(2 * np.pi * np.cumsum(sweep) / SR) * np.exp(-t / decay)


def _place(buf: np.ndarray, sig: np.ndarray, at: float, gain: float = 1.0) -> None:
    i = int(at * SR)
    n = min(len(sig), len(buf) - i)
    if n > 0:
        buf[i:i + n] += sig[:n] * gain


def _heartbeat() -> np.ndarray:
    buf = np.zeros(int(SR * 1.0))
    _place(buf, _thump(48, 0.35, 0.09), 0.0)
    _place(buf, _thump(44, 0.35, 0.08), 0.24, 0.75)
    return buf


def _scratch() -> np.ndarray:
    t = _t(0.45)
    f = 900 - 750 * np.sin(np.pi * t / 0.45) ** 2
    saw = 2 * ((np.cumsum(f) / SR) % 1.0) - 1
    sig = 0.6 * saw + 0.4 * _band(_noise(0.45), 800, 5000)
    return sig * np.minimum(1, t / 0.01) * np.exp(-t / 0.3)


def _stomp_hit() -> np.ndarray:
    t = _t(0.3)
    return _thump(70, 0.3, 0.07) + 0.5 * _band(_noise(0.3), 60, 600) * np.exp(-t / 0.04)


def _stomp_loop() -> np.ndarray:
    buf = np.zeros(int(SR * 2.4))
    for i, at in enumerate(np.arange(0, 2.4, 0.6)):
        _place(buf, _stomp_hit(), at + _rng.uniform(0, 0.02), 1.0 if i % 2 == 0 else 0.8)
    return buf


def _drum_loop() -> np.ndarray:
    buf = np.zeros(int(SR * 2.4))
    for i, at in enumerate(np.arange(0, 2.4, 0.3)):
        accent = 1.0 if i % 4 == 0 else 0.45
        _place(buf, _thump(110 if i % 4 == 0 else 160, 0.25, 0.06), at, accent)
    return buf


def _crowd_loop() -> np.ndarray:
    dur = 4.0
    t = _t(dur)
    sig = _band(_noise(dur), 200, 1400)
    mod = 0.7 + 0.3 * np.sin(2 * np.pi * 0.5 * t) * np.sin(2 * np.pi * 1.3 * t + 1)
    return sig * mod


def _whoosh() -> np.ndarray:
    t = _t(0.5)
    sig = _band(_noise(0.5), 300, 4000)
    return sig * np.sin(np.pi * t / 0.5) ** 2


def _hit() -> np.ndarray:
    t = _t(1.0)
    return _thump(45, 1.0, 0.25) + 0.3 * _band(_noise(1.0), 100, 3000) * np.exp(-t / 0.05)


def _riser() -> np.ndarray:
    t = _t(1.5)
    tone = np.sin(2 * np.pi * np.cumsum(200 + 600 * (t / 1.5) ** 2) / SR)
    return (0.5 * tone + 0.5 * _band(_noise(1.5), 500, 6000)) * (t / 1.5) ** 2


def _ding() -> np.ndarray:
    t = _t(1.2)
    return sum(np.sin(2 * np.pi * f * t) * a for f, a in ((1320, 1), (2640, 0.4), (3960, 0.2))) * np.exp(-t / 0.35)


def _pop() -> np.ndarray:
    t = _t(0.08)
    return np.sin(2 * np.pi * (500 + 400 * t / 0.08) * t) * np.exp(-t / 0.02)


def _wind() -> np.ndarray:
    t = _t(4.0)
    return _band(_noise(4.0), 150, 900) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.25 * t))


def _thunder() -> np.ndarray:
    t = _t(2.5)
    return _band(_noise(2.5), 30, 400) * np.exp(-t / 0.7) * np.minimum(1, t / 0.05)


def _camera() -> np.ndarray:
    buf = np.zeros(int(SR * 0.25))
    click = _band(_noise(0.02), 1500, 8000)
    _place(buf, click, 0.0)
    _place(buf, click, 0.09, 0.7)
    return buf


def _typing() -> np.ndarray:
    buf = np.zeros(int(SR * 2.0))
    for at in np.cumsum(_rng.uniform(0.07, 0.18, 16)):
        _place(buf, _band(_noise(0.015), 1000, 7000), float(at) % 1.95, _rng.uniform(0.5, 1))
    return buf


SYNTHS: Dict[str, Callable[[], np.ndarray]] = {
    "heartbeat": _heartbeat, "scratch": _scratch, "stomp": _stomp_loop, "drum": _drum_loop,
    "crowd": _crowd_loop, "whoosh": _whoosh, "hit": _hit, "riser": _riser, "ding": _ding,
    "pop": _pop, "wind": _wind, "thunder": _thunder, "camera": _camera, "typing": _typing,
}

"""Fit a measured voice track without rewriting narration or changing its pitch."""
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg

MIN_SECONDS, MAX_SECONDS = 40, 60
MIN_TEMPO, MAX_TEMPO = 0.85, 1.20


def choose_tempo(seconds: float, target: int) -> float:
    if not MIN_SECONDS <= target <= MAX_SECONDS:
        raise ValueError("Choose a target between 40 and 60 seconds, or use natural duration.")
    # Leave room for audio encoder and frame rounding at either endpoint.
    lower = max(MIN_SECONDS + 0.12, seconds / MAX_TEMPO)
    upper = min(MAX_SECONDS - 0.12, seconds / MIN_TEMPO)
    if lower > upper:
        action = "Add more spoken detail" if seconds < MIN_SECONDS else "Shorten the narration"
        raise ValueError(f"The voiceover is {seconds:.1f}s. {action} to fit 40–60s at a natural "
                         "pace, or choose natural duration to keep this recording.")
    return seconds / min(upper, max(lower, target))


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as track:
        return track.getnframes() / track.getframerate()


def fit_voiceover(path, words, durations, target):
    measured = wav_seconds(path)
    tempo = choose_tempo(measured, target)
    if abs(tempo - 1) > 0.0001:
        fitted = path.with_name("voiceover_fitted.wav")
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-y",
                        "-i", str(path), "-af", f"atempo={tempo:.9f}", "-c:a", "pcm_s16le",
                        str(fitted)], check=True)
        path = fitted
    seconds = wav_seconds(path)
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise ValueError(f"The paced voiceover measured {seconds:.2f}s; revise the narration or "
                         "use natural duration.")
    scale = seconds / sum(durations)
    words = [dict(w, start=min(seconds, w["start"] * scale), end=min(seconds, w["end"] * scale))
             for w in words]
    return path, words, [d * scale for d in durations]

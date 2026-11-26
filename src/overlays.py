"""Timed on-screen text overlays (with emoji) for production scripts."""

import re
from typing import List, Optional, Tuple

import numpy as np
import requests
from PIL import Image, ImageDraw
from moviepy.editor import ImageClip

from src.animate import _ease_out_back, _font_for_size
from src.utils import CACHE_DIR, logger

EMOJI_DIR = CACHE_DIR / "emoji"
TWEMOJI_URL = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/{code}.png"
_EMOJI_RE = re.compile(
    "(?:[\U0001F1E6-\U0001F1FF]{2}"
    "|[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF]"
    "[\uFE0F\U0001F3FB-\U0001F3FF]*(?:\u200D[\U0001F300-\U0001FAFF\u2600-\u27BF][\uFE0F\U0001F3FB-\U0001F3FF]*)*)"
)

LOOKS = {
    "default": {"box": (0, 0, 0, 175), "text": "#FFFFFF", "accent": "#FFE600"},
    "sepia": {"box": (28, 18, 8, 200), "text": "#F6E7C8", "accent": "#E0402A"},
    "mono": {"box": (0, 0, 0, 190), "text": "#FFFFFF", "accent": "#E53935"},
}


def _emoji_image(emoji: str, size: int) -> Optional[Image.Image]:
    code = "-".join(f"{ord(c):x}" for c in emoji if ord(c) != 0xFE0F)
    path = EMOJI_DIR / f"{code}.png"
    if not path.exists():
        try:
            r = requests.get(TWEMOJI_URL.format(code=code), timeout=15)
            r.raise_for_status()
            EMOJI_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
        except Exception as e:
            logger.warning(f"Emoji {emoji!r} unavailable ({e}); dropping it from the overlay")
            return None
    return Image.open(path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def _runs(text: str) -> List[Tuple[str, bool]]:
    out, pos = [], 0
    for m in _EMOJI_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(0), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return out


def render_overlay(text: str, width: int = 1080, font_size: int = 78, look: str = "default") -> np.ndarray:
    """Renders a boxed, wrapped, emoji-aware text overlay as an RGBA array."""
    colors = LOOKS.get(look, LOOKS["default"])
    max_w = width - 140
    pad_x, pad_y, gap = 34, 22, 14
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    size = font_size
    while True:
        font = _font_for_size(size)
        emoji_px = int(size * 1.05)

        def run_w(run: Tuple[str, bool]) -> float:
            return emoji_px if run[1] else measure.textlength(run[0], font=font)

        words = text.split()
        lines: List[str] = []
        cur = ""
        for w in words:
            cand = f"{cur} {w}".strip()
            if cur and sum(run_w(r) for r in _runs(cand)) > max_w:
                lines.append(cur)
                cur = w
            else:
                cur = cand
        lines.append(cur)
        widths = [sum(run_w(r) for r in _runs(l)) for l in lines]
        if (len(lines) <= 2 and max(widths) <= max_w) or size <= 48:
            break
        size -= 4

    line_h = int(size * 1.18)
    box_w = int(max(widths) + 2 * pad_x)
    box_h = int(len(lines) * line_h + (len(lines) - 1) * gap * 0 + 2 * pad_y)
    img = Image.new("RGBA", (width, box_h + 12), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0 = (width - box_w) // 2
    d.rounded_rectangle((x0, 0, x0 + box_w, box_h), radius=22, fill=colors["box"])
    d.rectangle((x0 + 22, box_h, x0 + box_w - 22, box_h + 6), fill=colors["accent"])

    y = pad_y
    for line, lw in zip(lines, widths):
        x = (width - lw) / 2
        for run, is_emoji in _runs(line):
            if is_emoji:
                em = _emoji_image(run, emoji_px)
                if em is not None:
                    img.alpha_composite(em, (int(x), int(y + (line_h - emoji_px) / 2 - size * 0.05)))
                x += emoji_px
            else:
                d.text((x, y), run, font=font, fill=colors["text"], stroke_width=3, stroke_fill="#000000")
                x += d.textlength(run, font=font)
        y += line_h
    return np.array(img)


def create_overlay_clips(overlays, width: int = 1080, height: int = 1920, look: str = "default",
                         top_frac: float = 0.10) -> List[ImageClip]:
    """One clip per resolved Overlay; stacked by slot, popping in and fading out."""
    clips = []
    slot_y: dict = {}
    for ov in sorted(overlays, key=lambda o: (o.slot, o.start)):
        dur = ov.end - ov.start
        if dur <= 0.2:
            continue
        arr = render_overlay(ov.text, width, look=look)
        y = slot_y.get(ov.slot)
        if y is None:
            y = int(height * top_frac) + sum(v for k, v in slot_y.items() if k < ov.slot)
        slot_y.setdefault(ov.slot, arr.shape[0] + 18)
        top = int(height * top_frac) + sum(slot_y[k] for k in sorted(slot_y) if k < ov.slot)
        clip = (
            ImageClip(arr)
            .set_start(ov.start)
            .set_duration(dur)
            .resize(lambda t: max(0.05, _ease_out_back(min(1.0, t / 0.25))))
            .crossfadeout(min(0.25, dur / 4))
            .set_position(("center", top))
        )
        clips.append(clip)
    return clips

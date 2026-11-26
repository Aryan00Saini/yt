from typing import Optional
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter, ImageEnhance
from moviepy.editor import VideoFileClip, VideoClip, ImageClip, CompositeVideoClip, vfx

from src.utils import logger, FONTS_DIR

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
MOTION_PRESETS = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up"]

# ── Font cache (shared with assemble.py which imports _font_for_size) ─────────
_FONTS: dict = {}


def _font_for_size(size: int) -> ImageFont.FreeTypeFont:
    """Loads Montserrat ExtraBold with graceful fallbacks; cached by size."""
    if size not in _FONTS:
        for p in (FONTS_DIR / "Montserrat-ExtraBold.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                _FONTS[size] = ImageFont.truetype(str(p), size)
                break
            except OSError:
                continue
        else:
            _FONTS[size] = ImageFont.load_default()
    return _FONTS[size]


# ── Easing ────────────────────────────────────────────────────────────────────

def _ease_out_back(x: float) -> float:
    """Overshoot-and-settle easing (used for pop-in animations)."""
    c1, c3 = 1.70158, 1.70158 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


# ── Per-pixel effects ─────────────────────────────────────────────────────────

def _apply_bottom_vignette(frame: np.ndarray) -> np.ndarray:
    """Subtle bottom-gradient vignette so captions always pop."""
    h = frame.shape[0]
    y_grad = np.ones((h, 1, 1), dtype=np.float32)
    start_y = int(h * 0.55)
    y_grad[start_y:] = np.linspace(1.0, 0.52, h - start_y, dtype=np.float32).reshape(-1, 1, 1)
    return (frame.astype(np.float32) * y_grad).astype(np.uint8)


def grade(frame: np.ndarray) -> np.ndarray:
    """Mild contrast + saturation boost so all clips share one look."""
    f = frame.astype(np.float32)
    f = (f - 128.0) * 1.08 + 128.0          # contrast
    gray = f.mean(axis=2, keepdims=True)
    f = gray + (f - gray) * 1.12            # saturation
    return np.clip(f, 0, 255).astype(np.uint8)


def sepia(frame: np.ndarray) -> np.ndarray:
    """Aged-paper tone for woodcut / engraving / vintage styles."""
    f = frame.astype(np.float32)
    gray = f @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    gray = (gray - 128.0) * 1.15 + 128.0
    tone = np.stack([gray * 1.07 + 18, gray * 0.95 + 8, gray * 0.78 - 4], axis=-1)
    return np.clip(tone, 0, 255).astype(np.uint8)


def mono(frame: np.ndarray) -> np.ndarray:
    gray = frame.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    gray = (gray - 128.0) * 1.2 + 128.0
    return np.repeat(np.clip(gray, 0, 255).astype(np.uint8)[..., None], 3, axis=-1)


def look_filter(look: str = "default", apply_grade: bool = True):
    """Returns the per-frame colour treatment for a visual look."""
    if look == "sepia":
        return sepia
    if look == "mono":
        return mono
    return grade if apply_grade else (lambda f: f)


# ── Punch-in helper (stock video) ─────────────────────────────────────────────

def punch_frame(frame: np.ndarray, t: float, punch: float = 0.07, dur: float = 0.25) -> np.ndarray:
    """Zooms in slightly at t=0 and settles to 1× by t=dur (CPU-friendly crop)."""
    k = 1.0 + punch * max(0.0, 1.0 - t / dur) ** 2
    if k <= 1.001:
        return frame
    h, w = frame.shape[:2]
    vw, vh = w / k, h / k
    box = ((w - vw) / 2, (h - vh) / 2, (w + vw) / 2, (h + vh) / 2)
    return np.asarray(
        Image.fromarray(frame).resize((w, h), Image.Resampling.BILINEAR, box=box)
    )


# ── Image helpers ─────────────────────────────────────────────────────────────

def crop_and_resize_to_vertical(clip, target_w: int = TARGET_WIDTH, target_h: int = TARGET_HEIGHT):
    """Center-crops and resizes a clip to exact vertical 9:16 (1080×1920)."""
    clip_w, clip_h = clip.size
    target_ratio = target_w / target_h
    current_ratio = clip_w / clip_h

    if current_ratio > target_ratio:
        new_w = int(target_h * current_ratio)
        scaled = clip.resize((new_w, target_h))
        x_center = scaled.w / 2
        return scaled.crop(
            x1=int(x_center - target_w / 2), y1=0,
            x2=int(x_center + target_w / 2), y2=target_h,
        )
    else:
        new_h = int(target_w / current_ratio)
        scaled = clip.resize((target_w, new_h))
        y_center = scaled.h / 2
        return scaled.crop(
            x1=0, y1=int(y_center - target_h / 2),
            x2=target_w, y2=int(y_center + target_h / 2),
        )


def fit_blur_bg(img: Image.Image, w: int, h: int) -> Image.Image:
    """Landscape/square source: sharp image on a blurred darkened background."""
    bg = ImageOps.fit(img, (w, h), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(40))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    fg = ImageOps.contain(img, (w, h), Image.Resampling.LANCZOS)
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return bg


# ── Ken Burns (images) ────────────────────────────────────────────────────────

def apply_ken_burns_effect(
    image_path: Path,
    duration: float,
    zoom_ratio: float = 1.15,
    motion_type: str = "zoom_in",
    punch: float = 0.07,
    apply_grade: bool = True,
    look: str = "default",
) -> VideoClip:
    """
    Single float-box resize per frame: fast, sharp, no rounding jitter.
    Punch-in: each scene starts slightly zoomed and settles in 0.25 s.
    Grade: mild contrast/saturation boost for a consistent look.
    """
    img = Image.open(image_path).convert("RGB")
    BW = int(TARGET_WIDTH * zoom_ratio)
    BH = int(TARGET_HEIGHT * zoom_ratio)
    base = (
        fit_blur_bg(img, BW, BH)
        if img.width / img.height > 0.75
        else ImageOps.fit(img, (BW, BH), Image.Resampling.LANCZOS)
    )
    base_arr = look_filter(look, apply_grade)(_apply_bottom_vignette(np.asarray(base)))
    base = Image.fromarray(base_arr)

    def make_frame(t: float) -> np.ndarray:
        p = min(1.0, max(0.0, t / duration)) if duration > 0 else 0.0
        e = 0.5 - 0.5 * float(np.cos(np.pi * p))  # ease in-out
        s, fx, fy = zoom_ratio, 0.5, 0.5
        if motion_type == "zoom_in":
            s = 1.0 + (zoom_ratio - 1.0) * e
        elif motion_type == "zoom_out":
            s = zoom_ratio - (zoom_ratio - 1.0) * e
        elif motion_type == "pan_right":
            fx = 0.1 + 0.8 * e
        elif motion_type == "pan_left":
            fx = 0.9 - 0.8 * e
        elif motion_type == "pan_up":
            fy = 0.9 - 0.8 * e
        # punch-in: extra zoom at the start of every scene
        s *= 1.0 + punch * max(0.0, 1.0 - t / 0.25) ** 2
        vw, vh = BW / s, BH / s
        left = (BW - vw) * fx
        top = (BH - vh) * fy
        return np.asarray(
            base.resize(
                (TARGET_WIDTH, TARGET_HEIGHT),
                Image.Resampling.BILINEAR,
                box=(left, top, left + vw, top + vh),
            )
        )

    return VideoClip(make_frame, duration=duration)


# ── Full-screen text card (card_text scenes) ──────────────────────────────────

def _gradient_bg(w: int, h: int, c1=(15, 12, 40), c2=(70, 30, 110)) -> np.ndarray:
    """Vertical gradient from c1 (top) to c2 (bottom), shape (h, w, 3)."""
    t = np.linspace(0, 1, h, dtype=np.float32).reshape(h, 1, 1)
    arr = np.array(c1, dtype=np.float32) * (1 - t) + np.array(c2, dtype=np.float32) * t
    return np.repeat(arr.astype(np.uint8), w, axis=1)


def make_text_card(text: str, duration: float, w: int = TARGET_WIDTH, h: int = TARGET_HEIGHT) -> CompositeVideoClip:
    """
    Full-screen animated text card for numeric/key-phrase scenes.
    Text pops in with overshoot easing in the first 0.3 s.
    """
    card_h = 700
    img = Image.new("RGBA", (w, card_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    upper = text.upper()
    size = 220
    while d.textlength(upper, font=_font_for_size(size)) > w - 120 and size > 70:
        size -= 6
    f = _font_for_size(size)
    tw = d.textlength(upper, font=f)
    d.text(
        ((w - tw) / 2, (card_h - size) // 2),
        upper,
        font=f,
        fill="#FFE600",
        stroke_width=10,
        stroke_fill="#000000",
    )

    bg = ImageClip(_gradient_bg(w, h)).set_duration(duration)
    fg = (
        ImageClip(np.array(img))
        .set_duration(duration)
        .resize(lambda t: max(0.05, _ease_out_back(min(1.0, t / 0.3))))
        .set_position("center")
    )
    return CompositeVideoClip([bg, fg], size=(w, h))


# ── Scene visual dispatcher ───────────────────────────────────────────────────

def prepare_scene_visual(
    visual_path: Path,
    duration: float,
    scene_index: int = 0,
    zoom_ratio: float = 1.15,
    punch: float = 0.07,
    apply_grade: bool = True,
    look: str = "default",
    motion: Optional[str] = None,
):
    """
    Returns a processed 1080×1920 MoviePy clip.
    - Images  → Ken Burns with punch-in and optional grade.
    - Videos  → FFmpeg-decoded, cropped to vertical, punch-in + grade applied.
    - None    → should not reach here (card_text scenes are handled upstream).
    """
    if visual_path is None:
        raise ValueError("prepare_scene_visual received None; card_text scenes must be handled before calling this.")

    if visual_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        motion = motion or MOTION_PRESETS[scene_index % len(MOTION_PRESETS)]
        return apply_ken_burns_effect(
            visual_path, duration,
            zoom_ratio=zoom_ratio,
            motion_type=motion,
            punch=punch,
            apply_grade=apply_grade,
            look=look,
        )

    # Stock video path
    clip = VideoFileClip(str(visual_path), target_resolution=(TARGET_HEIGHT, None))
    if clip.duration < duration:
        loop_factor = int(np.ceil(duration / clip.duration))
        clip = vfx.loop(clip, n=loop_factor)
    clip = clip.subclip(0, duration)
    clip = crop_and_resize_to_vertical(clip)

    treat = look_filter(look, apply_grade)
    clip = clip.fl_image(lambda f: treat(_apply_bottom_vignette(f)))

    # punch-in for video: use fl() so t is available
    if punch > 0:
        clip = clip.fl(lambda gf, t: punch_frame(gf(t), t, punch=punch))

    return clip

import numpy as np
from pathlib import Path
from moviepy.editor import (
    VideoFileClip,
    ImageClip,
    CompositeVideoClip,
    vfx
)
from src.utils import logger

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920

def crop_and_resize_to_vertical(clip, target_w: int = TARGET_WIDTH, target_h: int = TARGET_HEIGHT):
    """
    Crops and resizes an arbitrary aspect ratio clip/image to exact vertical 9:16 (1080x1920)
    without black bars or distortion (center-crop).
    """
    clip_w, clip_h = clip.size
    target_ratio = target_w / target_h
    current_ratio = clip_w / clip_h

    if current_ratio > target_ratio:
        # Wider than target -> scale by height, crop width
        new_w = int(target_h * current_ratio)
        scaled = clip.resize((new_w, target_h))
        x_center = scaled.w / 2
        final_clip = scaled.crop(
            x1=int(x_center - target_w / 2),
            y1=0,
            x2=int(x_center + target_w / 2),
            y2=target_h
        )
    else:
        # Taller than target -> scale by width, crop height
        new_h = int(target_w / current_ratio)
        scaled = clip.resize((target_w, new_h))
        y_center = scaled.h / 2
        final_clip = scaled.crop(
            x1=0,
            y1=int(y_center - target_h / 2),
            x2=target_w,
            y2=int(y_center + target_h / 2)
        )
    return final_clip

MOTION_PRESETS = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up"]


def _apply_bottom_vignette(frame: np.ndarray) -> np.ndarray:
    """Applies a subtle bottom gradient vignette so captions always pop."""
    h, w = frame.shape[:2]
    # Fade from 1.0 at 55% height down to 0.55 at bottom
    y_grad = np.ones((h, 1, 1), dtype=np.float32)
    start_y = int(h * 0.55)
    ramp = np.linspace(1.0, 0.52, h - start_y, dtype=np.float32).reshape(-1, 1, 1)
    y_grad[start_y:] = ramp
    return (frame.astype(np.float32) * y_grad).astype(np.uint8)


def apply_ken_burns_effect(
    image_path: Path,
    duration: float,
    zoom_ratio: float = 1.18,
    motion_type: str = "zoom_in"
):
    """
    Applies a smooth Ken Burns pan & zoom effect on a still image over the given duration.
    Supports 'zoom_in', 'zoom_out', 'pan_left', 'pan_right', and 'pan_up'.
    """
    from PIL import Image

    base_clip = ImageClip(str(image_path)).set_duration(duration)
    framed = crop_and_resize_to_vertical(base_clip)

    # Pre-render the base 1080x1920 frame once and apply vignette for speed
    base_frame = _apply_bottom_vignette(framed.get_frame(0))
    base_pil = Image.fromarray(base_frame)

    def animate_frame(t):
        progress = min(1.0, max(0.0, t / duration)) if duration > 0 else 0.0
        # Smooth ease-in-out
        eased = 0.5 - 0.5 * float(np.cos(np.pi * progress))

        if motion_type == "zoom_in":
            scale = 1.0 + (zoom_ratio - 1.0) * eased
            x_frac, y_frac = 0.5, 0.5
        elif motion_type == "zoom_out":
            scale = zoom_ratio - (zoom_ratio - 1.0) * eased
            x_frac, y_frac = 0.5, 0.5
        elif motion_type == "pan_right":
            scale = zoom_ratio
            x_frac, y_frac = 0.15 + 0.70 * eased, 0.5
        elif motion_type == "pan_left":
            scale = zoom_ratio
            x_frac, y_frac = 0.85 - 0.70 * eased, 0.5
        elif motion_type == "pan_up":
            scale = zoom_ratio
            x_frac, y_frac = 0.5, 0.85 - 0.70 * eased
        else:
            scale = 1.0 + (zoom_ratio - 1.0) * eased
            x_frac, y_frac = 0.5, 0.5

        w = max(TARGET_WIDTH, int(TARGET_WIDTH * scale))
        h = max(TARGET_HEIGHT, int(TARGET_HEIGHT * scale))

        resized = base_pil.resize((w, h), Image.Resampling.BILINEAR)
        left = int((w - TARGET_WIDTH) * x_frac)
        top = int((h - TARGET_HEIGHT) * y_frac)
        cropped = resized.crop((left, top, left + TARGET_WIDTH, top + TARGET_HEIGHT))
        return np.array(cropped)

    return framed.fl(lambda gf, t: animate_frame(t))


def prepare_scene_visual(visual_path: Path, duration: float, scene_index: int = 0):
    """
    Takes an image or video file path and returns a processed MoviePy clip
    fitted to 1080x1920 with exact scene duration and varied camera motion.
    """
    suffix = visual_path.suffix.lower()
    if suffix in [".jpg", ".jpeg", ".png", ".webp"]:
        motion = MOTION_PRESETS[scene_index % len(MOTION_PRESETS)]
        return apply_ken_burns_effect(visual_path, duration, motion_type=motion)
    else:
        clip = VideoFileClip(str(visual_path))
        if clip.duration < duration:
            loop_factor = int(np.ceil(duration / clip.duration))
            clip = vfx.loop(clip, n=loop_factor)

        clip = clip.subclip(0, duration)
        return crop_and_resize_to_vertical(clip)

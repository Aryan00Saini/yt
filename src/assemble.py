import os
import random
from pathlib import Path
from typing import List, Dict, Any, Optional
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from moviepy.editor import (
    AudioFileClip,
    CompositeAudioClip,
    concatenate_videoclips,
    ImageClip,
    CompositeVideoClip
)
from moviepy.audio.fx.all import volumex

from src.utils import logger, MUSIC_DIR
from src.animate import prepare_scene_visual

def render_caption_frame(
    words_group: List[Dict[str, Any]],
    current_time: float,
    width: int = 1080,
    height: int = 1920,
    font_size: int = 48,
    highlight_color: str = "#FFE600",
    text_color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 2
) -> Optional[np.ndarray]:
    """
    Renders transparent RGBA overlay image with active word highlight.
    """
    # Create transparent image
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arialbd.ttf", font_size)
    except IOError:
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except IOError:
            font = ImageFont.load_default()

    # Auto-scale font size so the batch always fits comfortably inside width - 160px
    max_line_width = width - 160
    spacing = int(font_size * 0.28)
    while font_size > 32:
        try:
            font = ImageFont.truetype("arialbd.ttf", font_size)
        except IOError:
            break
        spacing = int(font_size * 0.28)
        test_width = sum(
            (draw.textbbox((0, 0), w_info["word"], font=font)[2] -
             draw.textbbox((0, 0), w_info["word"], font=font)[0])
            for w_info in words_group
        ) + spacing * max(0, len(words_group) - 1)
        if test_width <= max_line_width:
            break
        font_size -= 2

    # Calculate bounding boxes for the line of words
    word_boxes = []
    total_text_width = 0
    max_text_height = 0

    for w_info in words_group:
        w_text = w_info["word"]
        bbox = draw.textbbox((0, 0), w_text, font=font)
        w_w = bbox[2] - bbox[0]
        w_h = bbox[3] - bbox[1]
        word_boxes.append((w_text, w_w, w_h, w_info["start"], w_info["end"]))
        total_text_width += w_w
        max_text_height = max(max_text_height, w_h)

    total_width = total_text_width + spacing * max(0, len(words_group) - 1)
    start_x = (width - total_width) // 2
    # Vertical placement: lower third
    start_y = int(height * 0.72)

    # Subtle dark pill behind caption line for readability
    pad_x, pad_y = 20, 10
    draw.rounded_rectangle(
        [start_x - pad_x, start_y - pad_y, start_x + total_width + pad_x, start_y + max_text_height + pad_y + 6],
        radius=14,
        fill=(0, 0, 0, 125)
    )

    curr_x = start_x
    for w_text, w_w, w_h, w_start, w_end in word_boxes:
        # Determine if this word is currently active
        is_active = w_start <= current_time <= w_end
        fill = highlight_color if is_active else text_color

        draw.text(
            (curr_x, start_y),
            w_text,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_color
        )
        curr_x += w_w + spacing

    return np.array(img)

def create_caption_clips(
    word_timestamps: List[Dict[str, Any]],
    total_duration: float,
    words_per_batch: int = 3,
    width: int = 1080,
    height: int = 1920,
    font_size: int = 48,
    highlight_color: str = "#FFE600",
    text_color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 2
) -> List[Any]:
    """
    Creates MoviePy clips for dynamic captions with active word highlighting.
    Groups words into short batches (e.g. 2-3 words) to keep the screen snappy.
    """
    caption_clips = []
    if not word_timestamps:
        return caption_clips

    # Group words into batches
    batches = []
    for i in range(0, len(word_timestamps), words_per_batch):
        batches.append(word_timestamps[i:i + words_per_batch])

    for batch in batches:
        b_start = batch[0]["start"]
        b_end = batch[-1]["end"]
        
        # Slight buffer to prevent flicker between batches
        b_duration = max(0.1, b_end - b_start)

        # For each word in the batch, generate an individual sub-clip with highlighting
        for w_info in batch:
            w_start = max(b_start, w_info["start"])
            w_end = min(b_end, w_info["end"])
            
            # Midpoint time for sampling highlight state
            mid_t = (w_start + w_end) / 2.0
            
            frame_arr = render_caption_frame(
                batch,
                current_time=mid_t,
                width=width,
                height=height,
                font_size=font_size,
                highlight_color=highlight_color,
                text_color=text_color,
                stroke_color=stroke_color,
                stroke_width=stroke_width
            )
            
            word_clip = (
                ImageClip(frame_arr)
                .set_start(w_start)
                .set_duration(max(0.05, w_end - w_start))
            )
            caption_clips.append(word_clip)

    return caption_clips

def pick_background_music() -> Optional[Path]:
    """Finds a royalty-free music track in assets/music/ if available."""
    if not MUSIC_DIR.exists():
        return None
    tracks = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav"))
    if tracks:
        return random.choice(tracks)
    return None

def assemble_short(
    scene_visuals: List[Path],
    scene_durations: List[float],
    voiceover_path: Path,
    word_timestamps: List[Dict[str, Any]],
    output_path: Path,
    config: dict
) -> Path:
    """
    Combines scene visuals, voiceover, dynamic captions, and background music into the final MP4.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    video_cfg = config.get("video", {})
    audio_cfg = config.get("audio", {})
    target_w = video_cfg.get("width", 1080)
    target_h = video_cfg.get("height", 1920)
    fps = video_cfg.get("fps", 30)

    logger.info(f"Assembling video from {len(scene_visuals)} scenes...")

    # 1. Build video sequence with varied camera motion & smooth crossfades
    transition_dur = float(config.get("visuals", {}).get("transition_duration", 0.4))
    video_clips = []
    num_scenes = len(scene_visuals)
    for idx, (vis_path, dur) in enumerate(zip(scene_visuals, scene_durations)):
        # Extend non-final clips slightly by transition_dur so crossfade overlap preserves total duration
        clip_dur = dur + transition_dur if (num_scenes > 1 and idx < num_scenes - 1 and dur > transition_dur) else dur
        logger.info(f"Processing scene clip {idx+1}/{num_scenes}: {vis_path.name} (duration: {dur:.2f}s)")
        clip = prepare_scene_visual(vis_path, clip_dur, scene_index=idx)
        if idx > 0 and transition_dur > 0 and clip_dur > transition_dur:
            clip = clip.crossfadein(transition_dur)
        video_clips.append(clip)

    if num_scenes > 1 and transition_dur > 0:
        main_video = concatenate_videoclips(video_clips, padding=-transition_dur, method="compose")
    else:
        main_video = concatenate_videoclips(video_clips, method="compose")

    # 2. Audio mixing (Voiceover + ducked background music)
    voice_audio = AudioFileClip(str(voiceover_path))
    audio_tracks = [voice_audio]

    bg_music_path = pick_background_music()
    if bg_music_path:
        music_vol = audio_cfg.get("music_volume", 0.12)
        logger.info(f"Adding background music: {bg_music_path.name} (volume: {music_vol})")
        music_clip = AudioFileClip(str(bg_music_path))
        # Loop music if needed
        if music_clip.duration < voice_audio.duration:
            from moviepy.audio.fx.all import audio_loop
            music_clip = audio_loop(music_clip, duration=voice_audio.duration)
        else:
            music_clip = music_clip.subclip(0, voice_audio.duration)
        
        music_clip = volumex(music_clip, music_vol)
        audio_tracks.append(music_clip)

    final_audio = CompositeAudioClip(audio_tracks)
    main_video = main_video.set_audio(final_audio)

    # 3. Dynamic Captions Overlay
    cap_cfg = config.get("captions", {})
    caption_clips = create_caption_clips(
        word_timestamps=word_timestamps,
        total_duration=voice_audio.duration,
        words_per_batch=cap_cfg.get("words_per_batch", 3),
        width=target_w,
        height=target_h,
        font_size=cap_cfg.get("font_size", 48),
        highlight_color=cap_cfg.get("highlight_color", "#FFE600"),
        text_color=cap_cfg.get("color", "#FFFFFF"),
        stroke_color=cap_cfg.get("stroke_color", "#000000"),
        stroke_width=cap_cfg.get("stroke_width", 2)
    )

    if caption_clips:
        logger.info(f"Burning {len(caption_clips)} caption frames into video...")
        final_composite = CompositeVideoClip([main_video] + caption_clips, size=(target_w, target_h))
    else:
        final_composite = main_video

    final_composite = final_composite.set_duration(voice_audio.duration)

    logger.info(f"Rendering final short to {output_path} (preset='ultrafast' for CPU efficiency)...")
    final_composite.write_videofile(
        str(output_path),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        preset="ultrafast",
        threads=4,
        logger=None # MoviePy progress output
    )

    logger.info(f"Short successfully created at: {output_path}")
    return output_path

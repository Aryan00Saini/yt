import os
import random
from pathlib import Path
from typing import FrozenSet, List, Dict, Any, Optional
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import (
    ColorClip,
    AudioFileClip,
    CompositeAudioClip,
    concatenate_videoclips,
    ImageClip,
    CompositeVideoClip,
)
from moviepy.audio.fx.all import volumex, audio_fadein, audio_fadeout

from src.utils import logger, MUSIC_DIR, ASSETS_DIR
from src.overlays import create_overlay_clips
from src.sound import apply_gain, envelope, pick_music, sfx_path
from src.animate import (
    prepare_scene_visual,
    make_text_card,
    _ease_out_back,
    _font_for_size,
)

STRIP_H = 360

# ── Word normaliser (used for emphasis matching) ───────────────────────────────
def _clean(w: str) -> str:
    return w.lower().strip(".,!?;:\"'()[]{}")


# ── Font helper (thin wrapper kept for backward compat) ───────────────────────
def _font(size: int) -> ImageFont.FreeTypeFont:
    return _font_for_size(size)


# ── Caption rendering ──────────────────────────────────────────────────────────

def render_caption_strip(
    words: List[Dict[str, Any]],
    active_idx: int,
    width: int = 1080,
    font_size: int = 92,
    highlight_color: str = "#FFE600",
    text_color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 7,
    emphasis: FrozenSet[str] = frozenset(),
    emphasis_color: str = "#FF3B3B",
) -> np.ndarray:
    """Renders a 1080×360 transparent RGBA caption strip.

    Active word → highlight_color.
    Emphasis words (key facts) → emphasis_color when not active.
    All others → text_color.
    """
    img = Image.new("RGBA", (width, STRIP_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    texts = [w["word"].upper() for w in words]
    size = font_size
    while True:
        f, gap = _font(size), int(size * 0.3)
        widths = [d.textlength(t, font=f) for t in texts]
        total = sum(widths) + gap * max(0, len(texts) - 1)
        if total <= width - 120 or size <= 44:
            break
        size -= 4
    x, y = (width - total) / 2, (STRIP_H - size) // 2
    for i, (t, wd) in enumerate(zip(texts, widths)):
        if i == active_idx:
            fill = highlight_color
        elif _clean(words[i]["word"]) in emphasis:
            fill = emphasis_color
        else:
            fill = text_color
        d.text((x, y), t, font=f, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_color)
        x += wd + gap
    return np.array(img)


def create_caption_clips(
    word_timestamps: List[Dict[str, Any]],
    total_duration: Optional[float] = None,
    words_per_batch: int = 3,
    width: int = 1080,
    height: int = 1920,
    font_size: int = 92,
    highlight_color: str = "#FFE600",
    text_color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 7,
    y_frac: float = 0.62,
    emphasis: FrozenSet[str] = frozenset(),
    emphasis_color: str = "#FF3B3B",
    caption_pop: float = 0.18,
) -> List[Any]:
    """Creates MoviePy ImageClips for dynamic karaoke captions.

    Each word pops in with a quick scale-up (caption_pop) for energy.
    Key facts (emphasis set) stay in emphasis_color when not highlighted.
    """
    clips, n = [], len(word_timestamps)
    for i in range(0, n, words_per_batch):
        batch = word_timestamps[i:i + words_per_batch]
        next_start = word_timestamps[i + words_per_batch]["start"] if i + words_per_batch < n else None
        for k, w in enumerate(batch):
            if k + 1 < len(batch):
                end = batch[k + 1]["start"]
            else:
                end = min(next_start, batch[-1]["end"] + 0.3) if next_start else batch[-1]["end"] + 0.2
            dur = max(0.05, end - w["start"])
            strip = render_caption_strip(
                batch, k, width, font_size,
                highlight_color, text_color, stroke_color, stroke_width,
                emphasis=emphasis, emphasis_color=emphasis_color,
            )
            c = ImageClip(strip).set_duration(dur)
            if caption_pop > 0:
                c = c.resize(lambda t, _pop=caption_pop: 1.0 + _pop * max(0.0, 1.0 - t / 0.12))
            c = (c.set_start(w["start"])
                  .set_position(("center", int(height * y_frac))))
            clips.append(c)
    return clips


# ── Hook text overlay (first 1.8 s) ───────────────────────────────────────────

def make_hook_clip(
    text: str,
    width: int = 1080,
    height: int = 1920,
    dur: float = 1.8,
) -> ImageClip:
    """Large ALL-CAPS hook text that pops in at t=0 with overshoot easing."""
    if not text:
        return None
    img = Image.new("RGBA", (width, 420), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    upper = text.upper()
    size = 130
    while d.textlength(upper, font=_font(size)) > width - 120 and size > 50:
        size -= 4
    f = _font(size)
    tw = d.textlength(upper, font=f)
    d.text(
        ((width - tw) / 2, (420 - size) // 2),
        upper,
        font=f,
        fill="#FFFFFF",
        stroke_width=9,
        stroke_fill="#000000",
    )
    return (
        ImageClip(np.array(img))
        .set_start(0)
        .set_duration(dur)
        .resize(lambda t: max(0.05, _ease_out_back(min(1.0, t / 0.25))))
        .set_position(("center", int(height * 0.24)))
    )


# ── Background music ───────────────────────────────────────────────────────────

def pick_background_music() -> Optional[Path]:
    """Picks a random royalty-free track from assets/music/."""
    if not MUSIC_DIR.exists():
        return None
    tracks = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav"))
    return random.choice(tracks) if tracks else None


# ── Sound effects ──────────────────────────────────────────────────────────────

def build_sfx_clips(
    scene_durations: List[float],
    sfx_volume: float = 0.3,
    sfx_every: int = 2,
) -> List[Any]:
    """
    Returns a list of AudioFileClip objects timed to scene cuts.
    - hit.wav   → plays at t=0 (hook impact).
    - whoosh.wav → plays 0.1 s before every Nth scene cut.
    Files are loaded from assets/sfx/; missing files are silently skipped.
    """
    sfx_dir = ASSETS_DIR / "sfx"
    clips = []
    t = 0.0

    hit_path = sfx_dir / "hit.wav"
    if hit_path.exists():
        try:
            clips.append(volumex(AudioFileClip(str(hit_path)), sfx_volume * 1.6).set_start(0))
        except Exception as e:
            logger.warning(f"Could not load hit.wav: {e}")

    whoosh_path = sfx_dir / "whoosh.wav"
    if whoosh_path.exists():
        for i, d in enumerate(scene_durations[:-1]):
            t += d
            if i % sfx_every == 0:
                try:
                    clips.append(
                        volumex(AudioFileClip(str(whoosh_path)), sfx_volume)
                        .set_start(max(0.0, t - 0.1))
                    )
                except Exception as e:
                    logger.warning(f"Could not load whoosh.wav at t={t:.2f}: {e}")
    return clips


# ── Production-script audio (music envelope + phrase-anchored SFX) ───────────

def _music_breakpoints(plan, total: float) -> List[tuple]:
    pts = [(0.0, 0.0), (0.5, 0.8 if plan.music.build else 1.0)]
    if plan.music.build:
        mains = [s for s in plan.sections if s.name.lower() in ("main", "body", "story", "middle")]
        peak_t = mains[-1].act_end if mains else max(0.6, total - 1.5)
        pts += [(peak_t, 1.35), (min(total, peak_t + 0.01), 1.0)]
    pts += [(max(pts[-1][0] + 0.01, total - 1.5), 1.0), (total, 0.0)]
    return pts


def build_plan_audio(plan, total: float, sfx_master: float = 0.6) -> List[Any]:
    clips: List[Any] = []
    for cue in plan.sfx:
        path = sfx_path(cue.sound)
        if path is None:
            continue
        vol = cue.volume * sfx_master
        for start, end in cue.times:
            try:
                clip = AudioFileClip(str(path))
                if cue.mode == "bed" and end > start:
                    from moviepy.audio.fx.all import audio_loop
                    span = end - start
                    clip = audio_loop(clip, duration=span)
                    ramp = [(start, 0.0), (start + 0.4, 0.6 if plan.music.build else 1.0),
                            (max(start + 0.41, end - 0.4), 1.3 if plan.music.build else 1.0), (end, 0.0)]
                    clip = apply_gain(volumex(clip, vol), envelope(ramp, plan.music.silences), offset=start)
                else:
                    clip = volumex(clip, vol)
                clips.append(clip.set_start(start))
                logger.info(f"SFX {cue.sound} ({cue.mode}) at {start:.2f}s" + (f"-{end:.2f}s" if end else ""))
            except Exception as e:
                logger.warning(f"Could not add SFX {cue.sound}: {e}")
    return clips


# ── Main assembler ─────────────────────────────────────────────────────────────

def assemble_short(
    scene_visuals: List[Optional[Path]],
    scene_durations: List[float],
    voiceover_path: Path,
    word_timestamps: List[Dict[str, Any]],
    output_path: Path,
    config: dict,
    draft: bool = False,
    hook_text: str = "",
    emphasis: FrozenSet[str] = frozenset(),
    scenes: Optional[list] = None,       # list of Scene objects for card_text detection
    plan=None,                           # resolved ProductionPlan (overlays, music, SFX, look)
    music_file: Optional[Path] = None,
) -> Path:
    """Combines scene visuals, voiceover, captions, music, SFX, and overlays into the final MP4."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    video_cfg  = config.get("video", {})
    audio_cfg  = config.get("audio", {})
    cap_cfg    = config.get("captions", {})
    render_cfg = config.get("render", {})
    fx_cfg     = config.get("effects", {})

    target_w = video_cfg.get("width", 1080)
    target_h = video_cfg.get("height", 1920)
    fps      = video_cfg.get("fps", 30)

    punch        = float(fx_cfg.get("punch", 0.07))
    caption_pop  = float(fx_cfg.get("caption_pop", 0.18))
    emph_color   = fx_cfg.get("emphasis_color", "#FF3B3B")
    sfx_volume   = float(fx_cfg.get("sfx_volume", 0.3))
    sfx_every    = int(fx_cfg.get("sfx_every", 2))
    apply_grade  = bool(fx_cfg.get("grade", True))

    look = plan.visuals.look if plan else "default"
    zoom_ratio      = float(config.get("visuals", {}).get("ken_burns_zoom_ratio", 1.15))
    transition_dur  = float(config.get("visuals", {}).get("transition_duration", 0.15))

    logger.info(f"Assembling video from {len(scene_visuals)} scenes...")

    # ── 1. Build video clips ──────────────────────────────────────────────────
    video_clips = []
    num_scenes = len(scene_visuals)
    last_good = None
    for idx, (vis_path, dur) in enumerate(zip(scene_visuals, scene_durations)):
        scene_obj = scenes[idx] if scenes and idx < len(scenes) else None
        card_text = getattr(scene_obj, "card_text", None) if scene_obj else None
        clip_dur = (dur + transition_dur
                    if (num_scenes > 1 and idx < num_scenes - 1 and dur > transition_dur) else dur)

        if card_text:
            logger.info(f"Scene {idx+1}/{num_scenes}: text card [{card_text}]")
            clip = make_text_card(card_text, clip_dur)
        else:
            if vis_path is None:                                   # failed lookup: reuse the last good visual
                logger.warning(f"Scene {idx+1}: no visual found, using fallback")
                vis_path = last_good
            if vis_path is None:                                   # nothing to reuse (e.g. scene 1)
                clip = ColorClip(size=(target_w, target_h), color=(15, 12, 40)).set_duration(clip_dur)
            else:
                last_good = vis_path
                logger.info(f"Scene {idx+1}/{num_scenes}: {vis_path.name} ({dur:.2f}s)")
                motion = "zoom_in" if vis_path.stem.startswith("map_") else None
                clip = prepare_scene_visual(vis_path, clip_dur, scene_index=idx,
                                            zoom_ratio=zoom_ratio, punch=punch, apply_grade=apply_grade,
                                            look=look, motion=motion)

        if idx > 0 and transition_dur > 0 and clip_dur > transition_dur:
            clip = clip.crossfadein(transition_dur)                # now applies to cards too
        video_clips.append(clip)

    if num_scenes > 1 and transition_dur > 0:
        main_video = concatenate_videoclips(video_clips, padding=-transition_dur, method="compose")
    else:
        main_video = concatenate_videoclips(video_clips, method="compose")

    # ── 2. Audio: voice + music + SFX ────────────────────────────────────────
    voice_audio  = AudioFileClip(str(voiceover_path))
    audio_tracks = [voice_audio]

    bg_music_path = None if plan and plan.music.muted else music_file or (
        pick_music(plan.music.moods) if plan else pick_background_music())
    if bg_music_path:
        music_vol = audio_cfg.get("music_volume", 0.12)
        logger.info(f"Background music: {bg_music_path.name} (vol={music_vol})")
        music_clip = AudioFileClip(str(bg_music_path))
        if music_clip.duration < voice_audio.duration:
            from moviepy.audio.fx.all import audio_loop
            music_clip = audio_loop(music_clip, duration=voice_audio.duration)
        else:
            music_clip = music_clip.subclip(0, voice_audio.duration)
        music_clip = volumex(music_clip, music_vol)
        if plan:
            gain = envelope(_music_breakpoints(plan, voice_audio.duration), plan.music.silences)
            music_clip = apply_gain(music_clip, gain)
        else:
            music_clip = audio_fadeout(audio_fadein(music_clip, 0.5), 1.5)
        audio_tracks.append(music_clip)

    if plan:
        sfx = build_plan_audio(plan, voice_audio.duration, float(fx_cfg.get("sfx_master", 0.6)))
    else:
        sfx = build_sfx_clips(scene_durations, sfx_volume=sfx_volume, sfx_every=sfx_every)
    audio_tracks.extend(sfx)

    main_video = main_video.set_audio(CompositeAudioClip(audio_tracks))

    # ── 3. Overlay layers (hook + captions) ───────────────────────────────────
    overlay_clips: List[Any] = []

    if plan and plan.overlays:
        overlay_clips.extend(create_overlay_clips(plan.overlays, target_w, target_h, look=look))
    else:
        hook_clip = make_hook_clip(hook_text, target_w, target_h)
        if hook_clip is not None:
            overlay_clips.append(hook_clip)

    caption_clips = create_caption_clips(
        word_timestamps=word_timestamps,
        total_duration=voice_audio.duration,
        words_per_batch=cap_cfg.get("words_per_batch", 3),
        width=target_w,
        height=target_h,
        font_size=cap_cfg.get("font_size", 92),
        highlight_color=cap_cfg.get("highlight_color", "#FFE600"),
        text_color=cap_cfg.get("color", "#FFFFFF"),
        stroke_color=cap_cfg.get("stroke_color", "#000000"),
        stroke_width=cap_cfg.get("stroke_width", 7),
        y_frac=cap_cfg.get("y_frac", 0.62),
        emphasis=emphasis,
        emphasis_color=emph_color,
        caption_pop=caption_pop,
    )
    overlay_clips.extend(caption_clips)

    if overlay_clips:
        logger.info(f"Compositing {len(overlay_clips)} overlay clip(s)...")
        final_composite = CompositeVideoClip(
            [main_video] + overlay_clips, size=(target_w, target_h)
        )
    else:
        final_composite = main_video

    final_composite = final_composite.set_duration(voice_audio.duration)

    # ── 4. Render ─────────────────────────────────────────────────────────────
    if draft:
        final_composite = final_composite.resize((540, 960))
        preset, crf = "ultrafast", 28
    else:
        preset = render_cfg.get("preset", "veryfast")
        crf    = render_cfg.get("crf", 20)

    logger.info(f"Rendering → {output_path} (preset={preset}, crf={crf})...")
    final_composite.write_videofile(
        str(output_path),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        audio_bitrate="192k",
        preset=preset,
        threads=os.cpu_count(),
        ffmpeg_params=["-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        temp_audiofile=str(output_path.with_suffix(".temp.m4a")),
        logger="bar",
    )

    for c in video_clips:
        c.close()
    voice_audio.close()

    logger.info(f"Short saved: {output_path}")
    return output_path

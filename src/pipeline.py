"""
Central pipeline shared by main.py (CLI) and app.py (Streamlit).

Calling run_pipeline() is the single source of truth for the generation flow;
both entry points delegate here so the logic is never duplicated.
"""

from pathlib import Path
from typing import Callable, List, Optional
from src.assemble import assemble_short, _clean
from src.utils import load_config, slugify, OUTPUT_DIR, CACHE_DIR, logger
from src.script_gen import generate_script, generate_script_from_custom_text
from src.tts import generate_voiceover_with_timing
from src.captions import transcribe_with_whisper
from src.visuals import fetch_all_visuals



def run_pipeline(
    topic: Optional[str] = None,
    custom_body: Optional[str] = None,
    media_files: Optional[List[Path]] = None,
    duration: int = 45,
    voice: Optional[str] = None,
    whisper_model: str = "base",
    draft: bool = False,
    progress: Callable[[str], None] = logger.info,
) -> tuple:
    """
    Full generation pipeline: script → TTS → visuals → assembly.

    Parameters
    ----------
    topic        : Auto-generate a script about this topic.
    custom_body  : Use this verbatim script text instead of generating one.
    media_files  : User-supplied photo/video files (one per scene, extras ignored).
    duration     : Target duration in seconds.
    voice        : Edge-TTS voice name; falls back to config then 'en-US-AndrewNeural'.
    whisper_model: Whisper model size, used only if edge-tts returns no word boundaries.
    draft        : When True, renders at 540x960 ultrafast/crf28 for quick tests.
    progress     : Callable for user-visible status messages.

    Returns
    -------
    (output_path, script) : Path to the final MP4 and the ShortScript object.
    """
    if not topic and not custom_body:
        raise ValueError("Provide a topic or a custom script body.")

    config = load_config()
    v = config.get("voice", {})
    script_cfg = config.get("script", {})
    vis_cfg = config.get("visuals", {})

    voice = voice or v.get("name", "en-US-AndrewNeural")
    rate = v.get("rate", "+8%")
    pitch = v.get("pitch", "+0Hz")
    slug = slugify(topic or custom_body[:40])

    if draft:
        config["render"] = {"preset": "ultrafast", "crf": 28}

    # ── Step 1: Script ────────────────────────────────────────────────────────
    progress("Step 1/4: Preparing script...")
    if custom_body:
        script = generate_script_from_custom_text(custom_body, title=topic)
    else:
        script = generate_script(
            topic,
            duration=duration,
            niche=script_cfg.get("niche", "science facts"),
            model_name=script_cfg.get("model", "gemini-2.5-flash"),
            words_per_second=script_cfg.get("words_per_second", 2.5),
            seconds_per_scene=script_cfg.get("seconds_per_scene", 3.5),
        )

    # ── Step 2: Voiceover + word boundaries ──────────────────────────────────
    progress("Step 2/4: Voiceover + word timings...")
    cache_dir = CACHE_DIR / slug
    audio_path, words, scene_durations = generate_voiceover_with_timing(
        script, cache_dir, voice=voice, rate=rate, pitch=pitch
    )
    if not words:  # edge-tts returned no WordBoundary events → Whisper fallback
        progress("  ⚠ No word boundaries from edge-tts, falling back to Whisper...")
        words = transcribe_with_whisper(
            audio_path,
            model_size=whisper_model,
            compute_type=config.get("captions", {}).get("compute_type", "int8"),
        )

    # ── Step 3: Visuals ───────────────────────────────────────────────────────
    progress("Step 3/4: Fetching visuals...")
    overrides = list(media_files or [])
    # Scenes with card_text don't need a downloaded visual; pass None as override
    card_overrides = [
        None if getattr(s, "card_text", None) else (overrides[i] if i < len(overrides) else None)
        for i, s in enumerate(script.scenes)
    ]
    visuals = fetch_all_visuals(
        script,
        scene_durations,
        overrides=card_overrides,
        max_workers=vis_cfg.get("max_download_workers", 4),
    )

    # ── Step 4: Assembly ──────────────────────────────────────────────────────
    progress("Step 4/4: Assembling video...")
    out = OUTPUT_DIR / f"{slug}.mp4"

    # Build the global emphasis word set from all scenes
    emph = frozenset(_clean(w) for s in script.scenes for w in s.emphasis)
    assemble_short(
        scene_visuals=visuals, scene_durations=scene_durations,
        voiceover_path=audio_path, word_timestamps=words, output_path=out, config=config,
        draft=draft, hook_text=script.hook_text, emphasis=emph, scenes=script.scenes)

    return out, script

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
from src.visuals import fetch_all_visuals, make_map_card, search_reference_image
from src.production import looks_like_production_script, parse_production_script, resolve_timing
from src.script_gen import generate_script_from_plan


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
    plan = None
    if custom_body and looks_like_production_script(custom_body):
        plan = parse_production_script(custom_body)
        topic = topic or plan.topic
    slug = slugify((plan.title if plan else None) or topic or custom_body[:40])

    if draft:
        config["render"] = {"preset": "ultrafast", "crf": 28}

    # ── Step 1: Script ────────────────────────────────────────────────────────
    progress("Step 1/4: Preparing script...")
    if plan:
        progress(f"  Production script: {len(plan.sections)} sections, {len(plan.overlays)} on-screen texts, "
                 f"{len(plan.sfx)} SFX cues, style: {plan.visuals.style or 'default'}")
        script = generate_script_from_plan(plan)
    elif custom_body:
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
    overrides: List[Optional[Path]] = list(media_files or [])
    overrides += [None] * (len(script.scenes) - len(overrides))
    skip = {i for i, s in enumerate(script.scenes) if getattr(s, "card_text", None)}
    if plan:
        if plan.visuals.references and overrides[0] is None:
            overrides[0] = search_reference_image(plan.visuals.references[0], plan.topic)
        if plan.visuals.map_place:
            place = plan.visuals.map_place.lower()
            idx = next((i for i, s in enumerate(script.scenes) if place in s.narration.lower()),
                       plan.scene_sections.index(1) if 1 in plan.scene_sections else None)
            if idx is not None and overrides[idx] is None:
                overrides[idx] = make_map_card(plan.visuals.map_place)
    visuals = fetch_all_visuals(
        script,
        scene_durations,
        overrides=overrides,
        skip=skip,
        max_workers=vis_cfg.get("max_download_workers", 4),
        style_suffix=plan.visuals.style_suffix if plan else None,
        prefer_images=bool(plan and plan.visuals.illustrative),
        context=plan.search_context if plan else "",
    )

    # ── Step 4: Assembly ──────────────────────────────────────────────────────
    progress("Step 4/4: Assembling video...")
    out = OUTPUT_DIR / f"{slug}.mp4"

    # Build the global emphasis word set from all scenes
    emph = frozenset(_clean(w) for s in script.scenes for w in s.emphasis)
    if plan:
        resolve_timing(plan, scene_durations, words)
        for ov in plan.overlays:
            progress(f"  On-screen text {ov.start:5.2f}-{ov.end:5.2f}s: {ov.text}")
    assemble_short(
        scene_visuals=visuals, scene_durations=scene_durations,
        voiceover_path=audio_path, word_timestamps=words, output_path=out, config=config,
        draft=draft, hook_text=script.hook_text, emphasis=emph, scenes=script.scenes, plan=plan)

    return out, script

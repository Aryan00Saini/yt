import asyncio
from contextlib import ExitStack
from pathlib import Path
from typing import List, Dict, Any
import edge_tts
from moviepy.editor import AudioFileClip, concatenate_audioclips
from src.utils import logger
from src.script_gen import ShortScript


async def _synth_scene(text: str, voice: str, rate: str, pitch: str, out_path: Path, sem: asyncio.Semaphore) -> List[Dict[str, Any]]:
    """Synthesizes a single scene and captures exact WordBoundary timings."""
    async with sem:
        for attempt in range(3):
            try:
                comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
                words: List[Dict[str, Any]] = []
                with open(out_path, "wb") as f:
                    async for chunk in comm.stream():
                        if chunk["type"] == "audio":
                            f.write(chunk["data"])
                        elif chunk["type"] == "WordBoundary":
                            start = chunk["offset"] / 1e7  # 100 ns units -> seconds
                            words.append({
                                "word": chunk["text"],
                                "start": start,
                                "end": start + chunk["duration"] / 1e7,
                            })
                return words
            except Exception as e:
                if attempt == 2:
                    raise
                logger.warning(f"edge-tts retry {attempt + 1} for '{out_path.name}': {e}")
                await asyncio.sleep(2 ** attempt)
    return []


async def _synth_all(script: ShortScript, voice: str, rate: str, pitch: str, out_dir: Path) -> List[List[Dict[str, Any]]]:
    sem = asyncio.Semaphore(4)  # be gentle with the free endpoint
    return await asyncio.gather(*[
        _synth_scene(s.narration.strip(), voice, rate, pitch, out_dir / f"scene_{i:02d}.mp3", sem)
        for i, s in enumerate(script.scenes)
    ])


def generate_voiceover_with_timing(
    script: ShortScript,
    out_dir: Path,
    voice: str = "en-US-AndrewNeural",
    rate: str = "+8%",
    pitch: str = "+0Hz",
) -> tuple[Path, List[Dict[str, Any]], List[float]]:
    """Returns (voiceover_path, word_timestamps, scene_durations)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)  # out_dir must be per topic: cache/<slug>/
    logger.info(f"Synthesizing {len(script.scenes)} scenes with voice='{voice}' (rate={rate})...")

    per_scene_words = asyncio.run(_synth_all(script, voice, rate, pitch, out_dir))
    clips, words_out, durations, t0 = [], [], [], 0.0

    final = out_dir / "voiceover.wav"
    with ExitStack() as stack:
        for i, words in enumerate(per_scene_words):
            clip = stack.enter_context(AudioFileClip(str(out_dir / f"scene_{i:02d}.mp3")))
            for w in words:
                words_out.append({
                    "word": w["word"],
                    "start": w["start"] + t0,
                    "end": w["end"] + t0,
                })
            durations.append(clip.duration)
            clips.append(clip)
            t0 += clip.duration
        concatenate_audioclips(clips).write_audiofile(str(final), codec="pcm_s16le", logger=None)

    if not all(per_scene_words):  # Don't silently omit captions for individual scenes.
        words_out = []
    logger.info(f"Voiceover saved to {final} ({len(words_out)} word boundaries, {t0:.2f}s)")
    return final, words_out, durations


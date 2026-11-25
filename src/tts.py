import os
import asyncio
from pathlib import Path
from typing import List, Tuple
import edge_tts
from src.utils import logger, CACHE_DIR
from src.script_gen import ShortScript

async def _synthesize_edge_tts(text: str, voice: str, output_path: str, rate: str = "+0%", pitch: str = "+0Hz"):
    """Internal helper to invoke edge_tts Communicate."""
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(output_path)

def generate_voiceover(script: ShortScript, output_path: Path, voice: str = "en-US-ChristopherNeural", rate: str = "+0%", pitch: str = "+0Hz") -> Path:
    """
    Synthesizes the entire script into a single coherent audio file.
    Also returns scene split information if needed.
    """
    full_text = " ".join([scene.narration.strip() for scene in script.scenes])
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Synthesizing voiceover with voice='{voice}' ({len(full_text.split())} words)...")
    
    asyncio.run(_synthesize_edge_tts(full_text, voice, str(output_path), rate=rate, pitch=pitch))
    logger.info(f"Voiceover saved to {output_path}")
    return output_path

def generate_scene_voiceovers(script: ShortScript, voice: str = "en-US-ChristopherNeural") -> List[Tuple[Path, str]]:
    """Synthesizes individual audio files per scene for precise timing alignment."""
    results = []
    for idx, scene in enumerate(script.scenes):
        scene_audio_path = CACHE_DIR / f"scene_{idx:02d}.mp3"
        asyncio.run(_synthesize_edge_tts(scene.narration.strip(), voice, str(scene_audio_path)))
        results.append((scene_audio_path, scene.narration.strip()))
    return results

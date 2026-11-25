import json
from pathlib import Path
from typing import List, Dict, Any
from src.utils import logger, CACHE_DIR

class WordTimestamp:
    def __init__(self, word: str, start: float, end: float, probability: float = 1.0):
        self.word = word
        self.start = start
        self.end = end
        self.probability = probability

    def to_dict(self) -> Dict[str, Any]:
        return {
            "word": self.word,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "probability": round(self.probability, 3)
        }

def _patch_pyav_metadata_errors():
    """
    PyAV >= 15 removed the deprecated 'metadata_errors' parameter from av.open().
    This monkey patch ensures faster_whisper works smoothly with newer PyAV versions.
    """
    try:
        import av
        original_open = av.open
        def patched_open(*args, **kwargs):
            kwargs.pop("metadata_errors", None)
            return original_open(*args, **kwargs)
        av.open = patched_open
    except Exception:
        pass

def transcribe_with_whisper(audio_path: Path, model_size: str = "base", compute_type: str = "int8") -> List[Dict[str, Any]]:
    """
    Transcribes an audio file using faster-whisper on CPU with word-level timestamps.
    Results are cached in a .json file next to the audio.
    """
    cache_file = audio_path.with_suffix(".timestamps.json")
    if cache_file.exists() and cache_file.stat().st_mtime >= audio_path.stat().st_mtime:
        logger.info(f"Loading cached word timestamps from {cache_file}")
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    logger.info(f"Transcribing audio with faster-whisper (model={model_size}, compute={compute_type}, cpu)...")
    try:
        _patch_pyav_metadata_errors()
        from faster_whisper import WhisperModel
    except ImportError:
        logger.error("faster-whisper is not installed. Run 'pip install faster-whisper'.")
        raise

    model = WhisperModel(model_size, device="cpu", compute_type=compute_type)
    segments, info = model.transcribe(str(audio_path), word_timestamps=True, vad_filter=True)

    words_list: List[Dict[str, Any]] = []
    for segment in segments:
        for word in segment.words:
            # Clean up leading/trailing punctuation/spaces
            clean_word = word.word.strip()
            if clean_word:
                words_list.append({
                    "word": clean_word,
                    "start": round(word.start, 3),
                    "end": round(word.end, 3),
                    "probability": round(word.probability, 3)
                })

    logger.info(f"Generated {len(words_list)} word timestamps.")
    
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(words_list, f, indent=2)

    return words_list

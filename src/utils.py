import os
import sys
import yaml
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"
MUSIC_DIR = ASSETS_DIR / "music"
FONTS_DIR = ASSETS_DIR / "fonts"

# Auto-configure FFmpeg binary path for MoviePy
try:
    import imageio_ffmpeg
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    os.environ["IMAGEIO_FFMPEG_EXE"] = ffmpeg_exe
    ffmpeg_dir = str(Path(ffmpeg_exe).parent)
    if ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{ffmpeg_dir}{os.pathsep}{os.environ.get('PATH', '')}"
except Exception:
    pass

# Patch Pillow ANTIALIAS for MoviePy compatibility with Pillow 10+
try:
    import PIL.Image
    if not hasattr(PIL.Image, "ANTIALIAS"):
        PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS
except Exception:
    pass

for d in [CACHE_DIR, OUTPUT_DIR, MUSIC_DIR, FONTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("ai_shorts")

def load_config() -> dict:
    """Loads configuration from config.yaml, with fallbacks."""
    config_path = BASE_DIR / "config.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {
        "video": {"width": 1080, "height": 1920, "fps": 30, "target_duration_seconds": 45},
        "voice": {"name": "en-US-ChristopherNeural", "rate": "+0%", "pitch": "+0Hz"},
        "captions": {"whisper_model": "base", "compute_type": "int8"},
        "audio": {"music_volume": 0.12, "voice_volume": 1.0}
    }

def slugify(text: str) -> str:
    """Creates a clean filesystem-safe slug from a string."""
    keep_chars = ("-", "_")
    return "".join(c if c.isalnum() or c in keep_chars else "_" for c in text.strip().lower())[:40]

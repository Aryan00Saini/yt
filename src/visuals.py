import os
import time
import hashlib
import threading
from pathlib import Path
from typing import List, Optional, Set, Any
from concurrent.futures import ThreadPoolExecutor
import requests
from src.utils import logger, CACHE_DIR, slugify

CLIPS_CACHE_DIR = CACHE_DIR / "visuals"
CLIPS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_used_lock = threading.Lock()
STYLE_SUFFIX = "cinematic, dramatic lighting, shallow depth of field, photorealistic, no text, no watermark"


def _download_file(url: str, dest_path: Path, retries: int = 3) -> Path:
    """Downloads a file atomically via .part temp file with exponential backoff retries."""
    if dest_path.exists() and dest_path.stat().st_size > 1000:
        return dest_path

    tmp = dest_path.with_suffix(dest_path.suffix + ".part")
    for attempt in range(retries):
        try:
            logger.info(f"Downloading visual from {url[:60]}... -> {dest_path.name}")
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        if chunk:
                            f.write(chunk)
            tmp.replace(dest_path)
            return dest_path
        except requests.RequestException as e:
            logger.warning(f"Download attempt {attempt + 1}/{retries} failed: {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Download failed after {retries} attempts: {url}")


def _best_pexels_file(video: dict) -> Optional[dict]:
    """Selects the video file closest to 1080x1920, never 4K."""
    h = lambda f: f.get("height") or 0
    files = [
        f for f in video.get("video_files", [])
        if f.get("file_type") == "video/mp4" and f.get("link")
    ]
    pool = (
        [f for f in files if (f.get("width") or 0) <= h(f) and 1280 <= h(f) <= 2200]
        or [f for f in files if 720 <= h(f) <= 1920]
    )
    return min(pool, key=lambda f: abs(h(f) - 1920)) if pool else None


def search_pexels_video(query: str, api_key: str, used: Optional[Set[Any]] = None, min_duration: float = 3.0) -> Optional[Path]:
    """Searches Pexels for a portrait video matching query, deduped across scenes."""
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key},
            params={"query": query, "orientation": "portrait", "per_page": 15},
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning(f"Pexels API responded with status {r.status_code}")
            return None
        for v in r.json().get("videos", []):
            if (v.get("duration") or 0) < min_duration:
                continue
            f = _best_pexels_file(v)
            if not f:
                continue
            if used is not None:
                with _used_lock:
                    if v["id"] in used:
                        continue
                    used.add(v["id"])
            return _download_file(f["link"], CLIPS_CACHE_DIR / f"pexels_{v['id']}.mp4")
    except Exception as e:
        logger.error(f"Error searching Pexels for '{query}': {e}")
    return None


def search_pixabay_video(query: str, api_key: str, used: Optional[Set[Any]] = None, min_duration: float = 3.0) -> Optional[Path]:
    """Searches Pixabay video API using params= and dedupes by hit id."""
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": api_key, "q": query, "video_type": "all", "per_page": 15},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        for hit in r.json().get("hits", []):
            if (hit.get("duration") or 0) < min_duration:
                continue
            videos = hit.get("videos", {})
            selected = videos.get("medium") or videos.get("large") or videos.get("small")
            if not selected or not selected.get("url"):
                continue
            if used is not None:
                with _used_lock:
                    if hit["id"] in used:
                        continue
                    used.add(hit["id"])
            return _download_file(selected["url"], CLIPS_CACHE_DIR / f"pixabay_{hit['id']}.mp4")
    except Exception as e:
        logger.error(f"Error searching Pixabay for '{query}': {e}")
    return None


def _strip_corner_watermark(image_path: Path, bottom_crop_pct: float = 0.10, side_crop_pct: float = 0.05) -> Path:
    """Crops out corner/bottom watermarks while preserving framing."""
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            w, h = img.size
            left = int(w * side_crop_pct)
            right = int(w * (1.0 - side_crop_pct))
            top = int(h * 0.02)
            bottom = int(h * (1.0 - bottom_crop_pct))
            if right > left and bottom > top:
                img.crop((left, top, right, bottom)).save(image_path, quality=95)
    except Exception as e:
        logger.warning(f"Could not crop watermark from {image_path.name}: {e}")
    return image_path


def generate_ai_image_pollinations(prompt: str) -> Path:
    """Free AI image generation via Pollinations.ai at 1080x1920 with retries and fixed style suffix."""
    full_prompt = f"{prompt}, {STYLE_SUFFIX}"
    encoded_prompt = requests.utils.quote(full_prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true"
    file_hash = hashlib.md5(full_prompt.encode()).hexdigest()[:10]
    dest = CLIPS_CACHE_DIR / f"ai_img_clean_{slugify(prompt)}_{file_hash}.jpg"
    already_cached = dest.exists() and dest.stat().st_size > 1000
    _download_file(url, dest, retries=3)
    if not already_cached:
        _strip_corner_watermark(dest)
    return dest


def search_wikimedia_image(keywords: List[str]) -> Optional[Path]:
    """Last-resort free stock image search via Wikimedia Commons API (no API key required)."""
    headers = {"User-Agent": "AIShortsMaker/1.0 (https://github.com/ai-shorts-maker)"}
    for query in (keywords[:2] if keywords else []):
        try:
            params = {
                "action": "query",
                "generator": "search",
                "gsrsearch": f"{query} filetype:bitmap",
                "gsrnamespace": 6,
                "gsrlimit": 5,
                "prop": "imageinfo",
                "iiprop": "url|mime|size",
                "iiurlwidth": 1080,
                "format": "json",
            }
            resp = requests.get("https://commons.wikimedia.org/w/api.php", params=params, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            pages = resp.json().get("query", {}).get("pages", {})
            for page in pages.values():
                info_list = page.get("imageinfo", [])
                if not info_list:
                    continue
                info = info_list[0]
                mime = info.get("mime", "")
                if mime not in ("image/jpeg", "image/png"):
                    continue
                img_url = info.get("thumburl") or info.get("url")
                if not img_url:
                    continue
                ext = ".png" if mime == "image/png" else ".jpg"
                file_hash = hashlib.md5(img_url.encode()).hexdigest()[:10]
                dest = CLIPS_CACHE_DIR / f"wiki_{slugify(query)}_{file_hash}{ext}"
                if dest.exists() and dest.stat().st_size > 1000:
                    return dest
                dl = requests.get(img_url, headers=headers, stream=True, timeout=30)
                if dl.status_code == 200:
                    with open(dest, "wb") as f:
                        for chunk in dl.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    if dest.stat().st_size > 1000:
                        return dest
        except Exception as e:
            logger.warning(f"Wikimedia search failed for '{query}': {e}")
    return None


def generate_color_backdrop(query: str) -> Path:
    """Minimal neutral dark gradient fallback when all network visual sources fail."""
    from PIL import Image, ImageDraw
    dest = CLIPS_CACHE_DIR / f"fallback_{slugify(query)}.jpg"
    if dest.exists():
        return dest
    img = Image.new("RGB", (1080, 1920), color=(14, 18, 28))
    draw = ImageDraw.Draw(img)
    for y in range(1920):
        t = y / 1920
        c = int(14 + 20 * t)
        draw.line([(0, y), (1080, y)], fill=(c, c + 4, c + 14))
    img.save(dest, quality=95)
    return dest


def fetch_visual_for_scene(scene, used: Set, min_duration: float) -> Path:
    """Fetches a unique visual for a single scene: Pexels -> Pixabay -> Pollinations -> Wikimedia -> fallback."""
    pexels = os.getenv("PEXELS_API_KEY")
    pixabay = os.getenv("PIXABAY_API_KEY")

    for q in scene.visual_keywords:
        if pexels and (p := search_pexels_video(q, pexels, used, min_duration)):
            return p

    for q in scene.visual_keywords[:2]:
        if pixabay and (p := search_pixabay_video(q, pixabay, used, min_duration)):
            return p

    prompt = getattr(scene, "image_prompt", None) or " ".join(scene.visual_keywords)
    try:
        return generate_ai_image_pollinations(prompt)
    except Exception as e:
        logger.warning(f"Pollinations failed for '{prompt[:40]}': {e}")

    wiki_img = search_wikimedia_image(scene.visual_keywords)
    if wiki_img:
        return wiki_img

    return generate_color_backdrop(prompt)


def fetch_all_visuals(
    script,
    scene_durations: List[float],
    overrides: Optional[List[Path]] = None,
    max_workers: int = 4,
) -> List[Optional[Path]]:
    """Fetches visuals for all scenes in parallel with cross-scene deduplication.

    Parameters
    ----------
    overrides : Index-matched list of paths. ``None`` at position i means the
                scene uses a generated text card and needs no downloaded visual.
    """
    used: Set = set()
    _overrides = list(overrides or [])

    def one(i: int) -> Optional[Path]:
        # Explicit None → card_text scene; skip network fetch
        if i < len(_overrides) and _overrides[i] is None:
            return None
        # Non-None override → user-supplied file
        if i < len(_overrides) and _overrides[i] is not None:
            return _overrides[i]
        # No override → fetch from network
        return fetch_visual_for_scene(script.scenes[i], used, scene_durations[i])

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(one, range(len(script.scenes))))

import os
import requests
import hashlib
from pathlib import Path
from typing import List, Optional
from src.utils import logger, CACHE_DIR, slugify

CLIPS_CACHE_DIR = CACHE_DIR / "visuals"
CLIPS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _download_file(url: str, dest_path: Path) -> Path:
    """Downloads a file from url to dest_path if not already downloaded."""
    if dest_path.exists() and dest_path.stat().st_size > 1000:
        return dest_path
    
    logger.info(f"Downloading visual from {url[:60]}... to {dest_path.name}")
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return dest_path

def search_pexels_video(query: str, api_key: str) -> Optional[Path]:
    """Searches Pexels for a vertical (portrait) video matching query."""
    headers = {"Authorization": api_key}
    url = f"https://api.pexels.com/videos/search?query={query}&orientation=portrait&per_page=5"
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Pexels API responded with status {resp.status_code}")
            return None
        data = resp.json()
        videos = data.get("videos", [])
        if not videos:
            return None
        
        # Pick the best video file (prefer 1080x1920 or 720x1280 HD portrait)
        for vid in videos:
            files = vid.get("video_files", [])
            # Sort by resolution height descending
            files = sorted(files, key=lambda f: f.get("height", 0), reverse=True)
            for f in files:
                if f.get("file_type") == "video/mp4" and f.get("link"):
                    h = f.get("height", 0)
                    w = f.get("width", 0)
                    # Prefer vertical orientation or large size
                    if h >= w and h >= 720:
                        file_url = f.get("link")
                        file_hash = hashlib.md5(file_url.encode()).hexdigest()[:10]
                        dest = CLIPS_CACHE_DIR / f"pexels_{slugify(query)}_{file_hash}.mp4"
                        return _download_file(file_url, dest)
                        
            # If no strict portrait file found, take the best available MP4
            for f in files:
                if f.get("file_type") == "video/mp4" and f.get("link"):
                    file_url = f.get("link")
                    file_hash = hashlib.md5(file_url.encode()).hexdigest()[:10]
                    dest = CLIPS_CACHE_DIR / f"pexels_{slugify(query)}_{file_hash}.mp4"
                    return _download_file(file_url, dest)
    except Exception as e:
        logger.error(f"Error searching Pexels for '{query}': {e}")
    return None

def search_pixabay_video(query: str, api_key: str) -> Optional[Path]:
    """Fallback search on Pixabay video API."""
    url = f"https://pixabay.com/api/videos/?key={api_key}&q={requests.utils.quote(query)}&video_type=all&per_page=5"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        hits = data.get("hits", [])
        if not hits:
            return None
        
        hit = hits[0]
        videos = hit.get("videos", {})
        # Pick medium or large
        selected = videos.get("medium") or videos.get("large") or videos.get("small")
        if selected and selected.get("url"):
            file_url = selected["url"]
            file_hash = hashlib.md5(file_url.encode()).hexdigest()[:10]
            dest = CLIPS_CACHE_DIR / f"pixabay_{slugify(query)}_{file_hash}.mp4"
            return _download_file(file_url, dest)
    except Exception as e:
        logger.error(f"Error searching Pixabay for '{query}': {e}")
    return None

def _strip_corner_watermark(image_path: Path, bottom_crop_pct: float = 0.10, side_crop_pct: float = 0.05) -> Path:
    """Crops out corner/bottom watermarks (e.g. Pollinations free-tier logo) while preserving framing."""
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            w, h = img.size
            left = int(w * side_crop_pct)
            right = int(w * (1.0 - side_crop_pct))
            top = int(h * 0.02)
            bottom = int(h * (1.0 - bottom_crop_pct))
            if right > left and bottom > top:
                cropped = img.crop((left, top, right, bottom))
                cropped.save(image_path, quality=95)
    except Exception as e:
        logger.warning(f"Could not crop watermark from {image_path.name}: {e}")
    return image_path

def generate_ai_image_pollinations(prompt: str) -> Path:
    """Free AI image generation via Pollinations.ai (free, no API key needed)."""
    encoded_prompt = requests.utils.quote(f"{prompt}, 8k, cinematic, hyperrealistic, no text, no watermark, no logo, 9:16 vertical poster")
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=720&height=1280&nologo=true"
    file_hash = hashlib.md5(prompt.encode()).hexdigest()[:10]
    dest = CLIPS_CACHE_DIR / f"ai_img_clean_{slugify(prompt)}_{file_hash}.jpg"
    already_cached = dest.exists() and dest.stat().st_size > 1000
    _download_file(url, dest)
    if not already_cached:
        _strip_corner_watermark(dest)
    return dest

def search_wikimedia_image(keywords: List[str]) -> Optional[Path]:
    """
    Free stock image search via Wikimedia Commons API (no API key required).
    Great for science, space, history, nature, and real-world topics.
    """
    headers = {"User-Agent": "AIShortsMaker/1.0 (https://github.com/ai-shorts-maker)"}
    queries = []
    if keywords:
        queries.append(" ".join(keywords[:2]))
        queries.append(keywords[0])

    for query in queries:
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
                        logger.info(f"Fetched Wikimedia Commons image for '{query}': {dest.name}")
                        return dest
        except Exception as e:
            logger.warning(f"Wikimedia search failed for '{query}': {e}")
    return None

def generate_color_backdrop(query: str) -> Path:
    """Offline procedural nebula/starfield backdrop generator using Pillow."""
    import random
    from PIL import Image, ImageDraw, ImageFilter
    dest = CLIPS_CACHE_DIR / f"fallback_{slugify(query)}.jpg"
    if dest.exists():
        return dest

    rng = random.Random(hashlib.md5(query.encode()).hexdigest())
    img = Image.new("RGB", (1080, 1920), color=(8, 12, 28))
    draw = ImageDraw.Draw(img)

    # Vertical deep gradient
    c1 = (rng.randint(8, 22), rng.randint(10, 25), rng.randint(30, 55))
    c2 = (rng.randint(25, 65), rng.randint(15, 45), rng.randint(65, 115))
    for y in range(1920):
        t = y / 1920
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        draw.line([(0, y), (1080, y)], fill=(r, g, b))

    # Soft glowing nebula orbs
    glow = Image.new("RGB", (1080, 1920), (0, 0, 0))
    g_draw = ImageDraw.Draw(glow)
    for _ in range(4):
        cx = rng.randint(150, 930)
        cy = rng.randint(250, 1650)
        rad = rng.randint(220, 460)
        col = (rng.randint(40, 140), rng.randint(30, 110), rng.randint(90, 200))
        g_draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=140))

    from PIL import ImageChops
    img = ImageChops.add(img, glow)

    # Subtle starlight particles
    s_draw = ImageDraw.Draw(img)
    for _ in range(140):
        sx = rng.randint(0, 1079)
        sy = rng.randint(0, 1919)
        sr = rng.choice([1, 1, 2, 3])
        brightness = rng.randint(160, 255)
        s_draw.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=(brightness, brightness, 255))

    img.save(dest, quality=95)
    return dest

def fetch_visual_for_keywords(keywords: List[str]) -> Path:
    """
    Finds the best visual for a list of scene keywords:
    1. Pexels video (if API key available)
    2. Pixabay video (if API key available)
    3. Pollinations.ai image (free cloud inference)
    4. Wikimedia Commons real stock photo (free, no key needed)
    5. Offline procedural starfield backdrop
    """
    pexels_key = os.getenv("PEXELS_API_KEY")
    pixabay_key = os.getenv("PIXABAY_API_KEY")
    
    search_query = " ".join(keywords) if keywords else "cinematic vertical short"

    # 1. Pexels
    if pexels_key:
        clip = search_pexels_video(search_query, pexels_key)
        if clip:
            return clip

    # 2. Pixabay
    if pixabay_key:
        clip = search_pixabay_video(search_query, pixabay_key)
        if clip:
            return clip

    # 3. AI Image (Pollinations.ai)
    try:
        logger.info(f"Generating AI visual via Pollinations for: '{search_query}'")
        return generate_ai_image_pollinations(search_query)
    except Exception as e:
        logger.warning(f"Pollinations AI image generation failed: {e}. Trying Wikimedia Commons...")

    # 4. Wikimedia Commons (free stock photo, no API key required)
    wiki_img = search_wikimedia_image(keywords)
    if wiki_img:
        return wiki_img

    # 5. Fallback procedural backdrop
    return generate_color_backdrop(search_query)

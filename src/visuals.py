import os
import re
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


_pollinations_lock = threading.Lock()


def generate_ai_image_pollinations(prompt: str, style_suffix: Optional[str] = None) -> Path:
    """AI image via Pollinations.ai at 1080x1920.

    With POLLINATIONS_API_KEY set this uses the keyed API (no rate limit). Without a key the
    free tier allows about 1 image per 45 s per IP, so requests are serialized and fail fast
    when the limit is hit, letting the caller fall back to other sources.
    """
    full_prompt = f"{prompt}, {style_suffix or STYLE_SUFFIX}"
    encoded_prompt = requests.utils.quote(full_prompt)
    file_hash = hashlib.md5(full_prompt.encode()).hexdigest()[:10]
    dest = CLIPS_CACHE_DIR / f"ai_img_clean_{slugify(prompt)}_{file_hash}.jpg"
    if dest.exists() and dest.stat().st_size > 1000:
        return dest

    key = os.getenv("POLLINATIONS_API_KEY")
    if key:
        url = f"https://gen.pollinations.ai/image/{encoded_prompt}?width=1080&height=1920&model=flux&nologo=true"
        headers = {"Authorization": f"Bearer {key}"}
    else:
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true"
        headers = {}

    with _pollinations_lock:
        for attempt in range(3):
            logger.info(f"Generating AI image: {prompt[:60]}...")
            r = requests.get(url, headers=headers, timeout=120)
            if r.ok and r.headers.get("content-type", "").startswith("image/"):
                dest.write_bytes(r.content)
                break
            wait = re.search(r"[Rr]etry after (\d+)", r.text)
            if r.status_code in (429, 503) and wait and int(wait.group(1)) <= 15:
                time.sleep(int(wait.group(1)) + 1)
                continue
            raise RuntimeError(f"Pollinations HTTP {r.status_code}: {r.text[:120]}")
        else:
            raise RuntimeError("Pollinations kept rate-limiting")
    if not key:
        _strip_corner_watermark(dest)
    return dest


def search_wikimedia_image(keywords: List[str], context: str = "", used: Optional[Set] = None) -> Optional[Path]:
    """Free image search via Wikimedia Commons (no API key).

    context (e.g. "Dancing Plague 1518 engraving") is combined with each keyword first, so
    results stay on-topic; used de-duplicates images across scenes.
    """
    headers = {"User-Agent": "AIShortsMaker/1.0 (https://github.com/Aryan00Saini/yt)"}
    queries = [k for k in (keywords[:2] if keywords else [])]
    if context:
        queries = [f"{context} {k}" for k in queries if k.lower() not in context.lower()] + [context] + queries
    for query in queries:
        try:
            params = {
                "action": "query",
                "generator": "search",
                "gsrsearch": f"{query} filetype:bitmap",
                "gsrnamespace": 6,
                "gsrlimit": 12 if context else 5,
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
                if used is not None:
                    with _used_lock:
                        if img_url in used:
                            continue
                        used.add(img_url)
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


def fetch_visual_for_scene(
    scene, used: Set, min_duration: float,
    style_suffix: Optional[str] = None, prefer_images: bool = False, context: str = "",
) -> Path:
    """Fetches a unique visual for a single scene: Pexels -> Pixabay -> Pollinations -> Wikimedia -> fallback.

    prefer_images skips stock video so an illustrated style (woodcut, cartoon...) stays consistent.
    """
    pexels = None if prefer_images else os.getenv("PEXELS_API_KEY")
    pixabay = None if prefer_images else os.getenv("PIXABAY_API_KEY")

    for q in scene.visual_keywords:
        if pexels and (p := search_pexels_video(q, pexels, used, min_duration)):
            return p

    for q in scene.visual_keywords[:2]:
        if pixabay and (p := search_pixabay_video(q, pixabay, used, min_duration)):
            return p

    prompt = getattr(scene, "image_prompt", None) or " ".join(scene.visual_keywords)
    try:
        return generate_ai_image_pollinations(prompt, style_suffix)
    except Exception as e:
        logger.warning(f"Pollinations failed for '{prompt[:40]}': {e}")

    wiki_img = search_wikimedia_image(scene.visual_keywords, context=context, used=used)
    if wiki_img:
        return wiki_img

    return generate_color_backdrop(prompt)


def fetch_all_visuals(
    script,
    scene_durations: List[float],
    overrides: Optional[List[Optional[Path]]] = None,
    max_workers: int = 4,
    skip: Optional[Set[int]] = None,
    style_suffix: Optional[str] = None,
    prefer_images: bool = False,
    context: str = "",
) -> List[Optional[Path]]:
    """Fetches visuals for all scenes in parallel with cross-scene deduplication.

    Parameters
    ----------
    overrides : Index-matched list of paths; a path is used as-is, ``None`` means fetch.
    skip      : Scene indices that need no visual (text-card scenes); they get ``None``.
    """
    used: Set = set()
    _overrides = list(overrides or [])
    _skip = skip or set()

    def one(i: int) -> Optional[Path]:
        if i in _skip:
            return None
        if i < len(_overrides) and _overrides[i] is not None:
            return _overrides[i]
        return fetch_visual_for_scene(
            script.scenes[i], used, scene_durations[i],
            style_suffix=style_suffix, prefer_images=prefer_images, context=context,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(one, range(len(script.scenes))))


# ── Production-script extras: reference artwork + map card ───────────────────

_WIKI_HEADERS = {"User-Agent": "AIShortsMaker/1.0 (https://github.com/Aryan00Saini/yt)"}


def search_reference_image(reference: str, topic: str = "") -> Optional[Path]:
    """Finds a named artwork (e.g. "Hendrik Hondius 1642 engraving of dancing sufferers") on Wikimedia Commons."""
    import re
    words = re.findall(r"[A-Za-z]{4,}", reference)
    caps = [w for w in re.findall(r"\b[A-Z][a-z]{3,}", reference)]
    surname = caps[-1] if caps else (words[0] if words else "")
    subject = re.search(r"\bof\s+(.+)$", reference)
    subj_words = re.findall(r"[a-z]{4,}", subject.group(1)) if subject else []
    topic_words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", f"{topic} {' '.join(subj_words)}")}
    queries = [q for q in (
        f"{surname} {subj_words[0]}" if subj_words else "",
        reference,
        f"{surname} {' '.join(sorted(topic_words)[:2])}",
    ) if q.strip()]
    for q in queries:
        try:
            r = requests.get("https://commons.wikimedia.org/w/api.php", headers=_WIKI_HEADERS, timeout=15, params={
                "action": "query", "list": "search", "srsearch": f"{q} filetype:bitmap",
                "srnamespace": 6, "srlimit": 8, "format": "json",
            })
            titles = [h["title"] for h in r.json().get("query", {}).get("search", [])]
            titles.sort(key=lambda t: -sum(w in t.lower() for w in topic_words | {surname.lower()}))
            for title in titles[:3]:
                info = requests.get("https://commons.wikimedia.org/w/api.php", headers=_WIKI_HEADERS, timeout=15, params={
                    "action": "query", "titles": title, "prop": "imageinfo",
                    "iiprop": "url|mime", "iiurlwidth": 1600, "format": "json",
                }).json()
                for page in info.get("query", {}).get("pages", {}).values():
                    ii = (page.get("imageinfo") or [{}])[0]
                    url = ii.get("thumburl") or ii.get("url")
                    if not url:
                        continue
                    dest = CLIPS_CACHE_DIR / f"ref_{slugify(title)}_{hashlib.md5(url.encode()).hexdigest()[:8]}.jpg"
                    if not (dest.exists() and dest.stat().st_size > 1000):
                        dl = requests.get(url, headers=_WIKI_HEADERS, timeout=60)
                        dl.raise_for_status()
                        dest.write_bytes(dl.content)
                    logger.info(f"Reference image for '{reference}': {title}")
                    return dest
        except Exception as e:
            logger.warning(f"Reference search failed for '{q}': {e}")
    return None


def _geocode(place: str):
    """Most important Nominatim match for a place; returns (lat, lon, zoom) or None."""
    import math
    queries = [place]
    if re.search(r"atlantic|pacific|indian|arctic|southern", place, re.I) and not re.search(r"ocean", place, re.I):
        queries.append(f"{place} Ocean")
    results = []
    for q in queries:
        results += requests.get("https://nominatim.openstreetmap.org/search", headers=_WIKI_HEADERS, timeout=15,
                                params={"q": q, "format": "json", "limit": 3}).json()
    if not results:
        return None
    r = max(results, key=lambda x: float(x.get("importance") or 0))
    if r.get("type") in ("ocean", "sea"):
        zoom = 3
    else:
        b = [float(v) for v in r["boundingbox"]]
        span = max(b[1] - b[0], b[3] - b[2], 1e-3)
        zoom = int(min(6, max(3, round(math.log2(360 / span)) - 2)))
    return float(r["lat"]), float(r["lon"]), zoom


def make_map_card(place: str, zoom: Optional[int] = None) -> Optional[Path]:
    """Renders a 1080x1920 OpenStreetMap card with a pin on `place` (file name starts with map_)."""
    import math
    from io import BytesIO
    from PIL import Image, ImageDraw
    from src.animate import _font_for_size

    try:
        geo = _geocode(place)
        if geo is None:
            raise ValueError("no match")
        lat, lon, auto_zoom = geo
        zoom = zoom or auto_zoom
    except Exception as e:
        logger.warning(f"Could not geocode '{place}': {e}")
        return None
    dest = CLIPS_CACHE_DIR / f"map_{slugify(place)}_z{zoom}.png"
    if dest.exists():
        return dest

    scale = 2                                   # upscale tiles so labels stay readable on a phone
    W, H = 1080 // scale + 2, 1920 // scale + 2
    n = 2 ** zoom
    px = (lon + 180) / 360 * n * 256
    py = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n * 256
    x0, y0 = px - W / 2, py - H / 2
    canvas = Image.new("RGB", (W, H), (230, 225, 210))
    try:
        for tx in range(int(x0 // 256), int((x0 + W) // 256) + 1):
            for ty in range(int(y0 // 256), int((y0 + H) // 256) + 1):
                if not 0 <= ty < n:
                    continue
                r = requests.get(f"https://tile.openstreetmap.org/{zoom}/{tx % n}/{ty}.png", headers=_WIKI_HEADERS, timeout=20)
                r.raise_for_status()
                canvas.paste(Image.open(BytesIO(r.content)).convert("RGB"), (int(tx * 256 - x0), int(ty * 256 - y0)))
                time.sleep(0.05)
    except Exception as e:
        logger.warning(f"Map tiles unavailable for '{place}': {e}")
        return None

    img = canvas.resize((W * scale, H * scale), Image.Resampling.LANCZOS).crop((scale, scale, 1080 + scale, 1920 + scale))
    d = ImageDraw.Draw(img)
    cx, cy = 540, 960
    d.ellipse((cx - 70, cy - 22, cx + 70, cy + 22), fill=(70, 70, 70), outline=None)
    d.polygon([(cx - 34, cy - 70), (cx + 34, cy - 70), (cx, cy)], fill="#D62828", outline="#000000")
    d.ellipse((cx - 44, cy - 150, cx + 44, cy - 62), fill="#D62828", outline="#000000", width=4)
    d.ellipse((cx - 16, cy - 122, cx + 16, cy - 90), fill="#FFFFFF")
    d.text((24, 1920 - 300), "© OpenStreetMap contributors", font=_font_for_size(26), fill="#222222",
           stroke_width=2, stroke_fill="#FFFFFF")
    img.save(dest)
    return dest

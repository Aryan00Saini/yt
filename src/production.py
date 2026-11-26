"""Structured "production script" support.

A production script is a full brief such as:

    Video Title: ...
    [Hook, 0-5s]      <- narration sections (only these lines are spoken)
    On-Screen Text    <- "TEXT" (8-12s) overlays
    Background Music & SFX
    Visual Suggestions
    Sources

This module turns that brief into a ProductionPlan (narration scenes plus
overlay / music / SFX / visual-style instructions) and, once the voiceover
exists, resolves every instruction to real timestamps.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.utils import logger

# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class Section:
    name: str
    text: str = ""
    est_start: Optional[float] = None
    est_end: Optional[float] = None
    act_start: Optional[float] = 0.0
    act_end: Optional[float] = 0.0


@dataclass
class Overlay:
    text: str
    est_start: Optional[float] = None
    est_end: Optional[float] = None
    start: float = 0.0
    end: float = 0.0
    slot: int = 0


@dataclass
class SfxCue:
    sound: str
    mode: str = "oneshot"            # oneshot | bed | cuts
    anchor: str = "start"            # start | phrase | section | last_question
    phrase: str = ""
    section: str = ""
    volume: float = 1.0
    stop_music: bool = False
    times: List[Tuple[float, float]] = field(default_factory=list)   # resolved (start, end)


@dataclass
class MusicPlan:
    moods: List[str] = field(default_factory=list)
    description: str = ""
    build: bool = False
    silence_before: List[str] = field(default_factory=list)          # section names
    silences: List[Tuple[float, float]] = field(default_factory=list)  # resolved windows


@dataclass
class VisualPlan:
    style: str = ""
    illustrative: bool = False
    look: str = "default"            # default | sepia | mono
    references: List[str] = field(default_factory=list)
    map_place: Optional[str] = None
    suggestions: List[str] = field(default_factory=list)

    @property
    def style_suffix(self) -> Optional[str]:
        if not self.style:
            return None
        style = re.sub(r"\banimation\b", "", self.style, flags=re.I).strip(" ,")
        return f"{style}, full-bleed vertical composition, no text, no letters, no watermark"


@dataclass
class ProductionPlan:
    topic: str = ""
    title: str = ""
    sections: List[Section] = field(default_factory=list)
    overlays: List[Overlay] = field(default_factory=list)
    sfx: List[SfxCue] = field(default_factory=list)
    music: MusicPlan = field(default_factory=MusicPlan)
    visuals: VisualPlan = field(default_factory=VisualPlan)
    sources: List[str] = field(default_factory=list)
    scene_sections: List[int] = field(default_factory=list)          # scene index -> section index

    @property
    def narration(self) -> str:
        return "\n\n".join(s.text for s in self.sections if s.text)

    def section_index(self, name: str) -> Optional[int]:
        name = name.lower().strip()
        for i, s in enumerate(self.sections):
            if s.name.lower() == name or s.name.lower().startswith(name) or name.startswith(s.name.lower()):
                return i
        return None

    @property
    def search_context(self) -> str:
        """Core subject for image searches: the topic word the narration repeats most, plus its
        neighbour in the title ("Dancing Plague", "Titanic")."""
        words = re.findall(r"[A-Za-z0-9']+", self.topic or self.title)
        spoken = [_stem(w) for w in self.narration.split()]
        best, best_n = None, 0
        for i, w in enumerate(words):
            n = spoken.count(_stem(w)) if w.lower() not in _STOP and len(w) > 2 else 0
            if n > best_n:
                best, best_n = i, n
        if best is None:
            return " ".join(w for w in words if w.lower() not in _STOP)[:40]
        core = [words[best]]
        if best + 1 < len(words) and words[best + 1].lower() not in _STOP and not words[best + 1].isdigit():
            core.append(words[best + 1])
        return " ".join(core)

    def description(self) -> str:
        first = self.sections[0].text.strip() if self.sections else ""
        parts = [self.title or self.topic, first, "#Shorts"]
        if self.sources:
            parts.append("Sources:\n" + "\n".join(f"- {s}" for s in self.sources))
        return "\n\n".join(p for p in parts if p)


# ── Parsing ───────────────────────────────────────────────────────────────────

_HEADINGS = {
    "script": r"(full\s+)?script|narration|voice\s*-?over|vo",
    "overlays": r"on[\s-]*screen\s+text|text\s+overlays?|overlays?|captions?\s+overlays?|on[\s-]*screen\s+captions",
    "audio": r"(background\s+)?music(\s*(&|and)\s*(sfx|sound\s*effects?))?|sound(\s+design)?|audio|sfx|sound\s+effects?",
    "visuals": r"visuals?(\s+(suggestions?|notes?|direction|style))?|b-?roll|visual\s+style",
    "factcheck": r"fact[\s-]*check(\s+notes?)?|notes?",
    "sources": r"sources?|references?|citations?",
    "tags": r"hash\s*tags|tags",
    "description": r"(video\s+)?description",
}
_SECTION_RE = re.compile(
    r"^\[\s*(?P<name>[^\],\]]+?)\s*(?:[,(]\s*(?P<a>[\d:.]+)\s*(?:s|sec)?\s*[-–—to]+\s*(?P<b>[\d:.]+)\s*(?:s|sec|seconds)?\s*\)?)?\s*\]\s*:?\s*(?P<rest>.*)$",
    re.I,
)
_KNOWN_SECTIONS = (r"hook|intro(?:duction)?|opening|setup|context|main|body|story|middle|build[\s-]*up|reveal|"
                   r"twist|climax|payoff|ending|outro|conclusion|cta|call\s+to\s+action|(?:scene|part|beat)\s*\d+")
_TIMES = r"(?P<a>\d+(?::\d{1,2})?(?:\.\d+)?)\s*(?:s|sec)?\s*(?:[-–—]|to)\s*(?P<b>\d+(?::\d{1,2})?(?:\.\d+)?)\s*(?:s|sec|seconds)?"
# "Hook (0-5s): ...", "HOOK - 0:00-0:05", "Scene 2:" ...
_NAMED_SECTION_RE = re.compile(
    rf"^(?P<name>{_KNOWN_SECTIONS})\s*(?:[(\[,–—-]\s*{_TIMES}\s*[)\]]?)?\s*[:.–—-]?\s*(?P<rest>.*)$", re.I)
# Any short label followed by a time range: "Cold Open (0-4s): ..."
_TIMED_SECTION_RE = re.compile(
    rf"^(?P<name>[A-Za-z][A-Za-z0-9 '&/]{{0,24}}?)\s*[(\[]\s*{_TIMES}\s*[)\]]\s*:?\s*(?P<rest>.*)$", re.I)


def _match_section(line: str, mode: Optional[str]):
    m = _SECTION_RE.match(line)
    if m:
        return m
    if mode in ("overlays", "audio", "visuals", "sources", "factcheck"):
        return None
    m = _NAMED_SECTION_RE.match(line)
    if m and (m.group("a") or not m.group("rest") or re.match(r"^\S+\s*[:.–—-]", line[len(m.group("name")):].lstrip() or ":")):
        return m
    return _TIMED_SECTION_RE.match(line)


_TIME_RANGE_RE = re.compile(
    r"\(?\s*(?P<a>\d+(?::\d{1,2})?(?:\.\d+)?)\s*s?\s*[-–—]\s*(?P<b>\d+(?::\d{1,2})?(?:\.\d+)?)\s*(?:s|sec|seconds)?\s*\)?"
)
_QUOTES = "\"'“”‘’"


def _secs(v: Optional[str]) -> Optional[float]:
    if not v:
        return None
    if ":" in v:
        m, s = v.split(":", 1)
        return int(m) * 60 + float(s)
    return float(v)


def _clean_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = line.replace("**", "").replace("__", "")
    return line.strip()


def _heading_kind(line: str) -> Optional[str]:
    bare = re.sub(r"\s*\(.*?\)\s*$", "", line).strip().rstrip(":").strip()
    for kind, pat in _HEADINGS.items():
        if re.fullmatch(pat, bare, flags=re.I):
            return kind
    return None


def _strip_bullet(line: str) -> str:
    return re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip()


def _strip_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] in _QUOTES and text[-1] in _QUOTES:
        text = text[1:-1]
    return text.strip()


def looks_like_production_script(text: str) -> bool:
    """True when the text is a full brief (sections / overlays / music notes), not plain narration."""
    lines = [_clean_line(l) for l in text.splitlines() if l.strip()]
    if any(_SECTION_RE.match(l) for l in lines):
        return True
    if sum(bool(_match_section(l, None)) for l in lines) >= 2:
        return True
    kinds = {_heading_kind(l) for l in lines}
    return bool(kinds & {"overlays", "audio", "visuals"})


def _spoken(line: str) -> str:
    line = _strip_bullet(line)
    line = re.sub(r"^(narrator|vo|voice\s*-?over|host)\s*:\s*", "", line, flags=re.I)
    line = re.sub(r"\[[^\]]*\]|\((?:pause|beat|sfx|music|cut|visual)[^)]*\)", "", line, flags=re.I)
    return re.sub(r"\s+", " ", line).strip()


def parse_production_script(text: str) -> ProductionPlan:
    plan = ProductionPlan()
    mode: Optional[str] = None
    buckets: Dict[str, List[str]] = {"audio": [], "visuals": [], "sources": [], "overlays": []}
    current: Optional[Section] = None

    for raw in text.splitlines():
        line = _clean_line(raw)
        if not line:
            continue

        m = re.match(r"^(?:video\s+)?title\s*:\s*(.+)$", line, flags=re.I)
        if m:
            plan.title = m.group(1).strip()
            continue

        sm = _match_section(line, mode) if _heading_kind(line) is None else None
        if sm:
            current = Section(
                name=sm.group("name").strip().title(),
                est_start=_secs(sm.group("a")),
                est_end=_secs(sm.group("b")),
            )
            plan.sections.append(current)
            mode = "script"
            if sm.group("rest"):
                current.text = _spoken(sm.group("rest"))
            continue

        kind = _heading_kind(line)
        if kind:
            mode = kind
            continue

        if mode is None:
            if not plan.topic:
                plan.topic = line
            continue

        if mode == "script":
            spoken = _spoken(line)
            if not spoken:
                continue
            if current is None:
                current = Section(name="Main")
                plan.sections.append(current)
            current.text = f"{current.text} {spoken}".strip()
        elif mode in buckets:
            buckets[mode].append(_strip_bullet(line))

    plan.topic = plan.topic or plan.title
    plan.title = plan.title or plan.topic
    plan.sections = [s for s in plan.sections if s.text]
    _fill_estimates(plan.sections)
    plan.overlays = [o for o in (_parse_overlay(l) for l in buckets["overlays"]) if o]
    plan.sources = [_strip_quotes(s) for s in buckets["sources"]]

    music_text, sfx_text = _split_audio(buckets["audio"])
    plan.music = parse_music(music_text)
    plan.sfx = parse_sfx(sfx_text, [s.name for s in plan.sections])
    plan.visuals = parse_visuals(" ".join(buckets["visuals"]))
    return plan


def _fill_estimates(sections: List[Section], wps: float = 2.5) -> None:
    t = 0.0
    for s in sections:
        n = len(s.text.split())
        if s.est_start is None:
            s.est_start = t
        if s.est_end is None or s.est_end <= s.est_start:
            s.est_end = s.est_start + n / wps
        t = s.est_end


def _parse_overlay(line: str) -> Optional[Overlay]:
    line = line.strip()
    if not line:
        return None
    a = b = None
    tm = None
    for cand in _TIME_RANGE_RE.finditer(line):
        tm = cand
    lead = re.match(r"^\(?\s*(\d+(?::\d{1,2})?(?:\.\d+)?)\s*s?\s*[-–—]\s*(\d+(?::\d{1,2})?(?:\.\d+)?)\s*s?\s*\)?\s*[:\-–—]\s*(.+)$", line)
    if lead:
        a, b, body = _secs(lead.group(1)), _secs(lead.group(2)), lead.group(3)
    elif tm and (tm.end() >= len(line.rstrip()) - 1 or line[tm.start():tm.start() + 1] == "("):
        a, b, body = _secs(tm.group("a")), _secs(tm.group("b")), line[:tm.start()] + line[tm.end():]
    else:
        body = line
    body = re.sub(r"^[-–—:]+\s+|\s+[-–—:]+$", "", body.strip()).strip(" :")
    body = _strip_quotes(body)
    if not body:
        return None
    return Overlay(text=body, est_start=a, est_end=b)


def _split_audio(lines: List[str]) -> Tuple[str, str]:
    music, sfx = [], []
    for l in lines:
        m = re.match(r"^(music|soundtrack|score|bgm)\s*:\s*(.*)$", l, flags=re.I)
        s = re.match(r"^(sfx|sound\s*effects?|effects?)\s*:\s*(.*)$", l, flags=re.I)
        if m:
            music.append(m.group(2))
        elif s:
            sfx.append(s.group(2))
        elif re.search(r"\b(music|track|loop|melody|score)\b", l, flags=re.I):
            music.append(l)
        else:
            sfx.append(l)
    return " ".join(music), " ".join(sfx)


# ── Music / SFX / visual interpretation ───────────────────────────────────────

MOOD_SYNONYMS: Dict[str, List[str]] = {
    "eerie": ["eerie", "creepy", "haunting", "spooky", "ominous", "off-kilter", "unsettling", "horror", "mysterious"],
    "suspense": ["suspense", "tense", "tension", "thriller", "dark", "dramatic"],
    "medieval": ["medieval", "folk", "fiddle", "lute", "renaissance", "celtic", "bagpipe"],
    "epic": ["epic", "cinematic", "orchestral", "heroic", "trailer"],
    "upbeat": ["upbeat", "happy", "energetic", "fun", "pop", "bouncy"],
    "calm": ["calm", "chill", "lofi", "lo-fi", "ambient", "relaxing", "soft", "piano"],
    "sad": ["sad", "melancholy", "emotional", "somber"],
    "tech": ["electronic", "synth", "futuristic", "techno", "cyber"],
}

SOUND_SYNONYMS: Dict[str, List[str]] = {
    "scratch": ["record-scratch", "record scratch", "scratch", "vinyl stop", "tape stop"],
    "heartbeat": ["heartbeat", "heart beat", "heart-beat", "pulse"],
    "stomp": ["stomp", "stomping", "footstep", "footsteps", "marching"],
    "drum": ["drumbeat", "drumbeats", "drum", "drums", "percussion"],
    "crowd": ["crowd", "murmur", "chatter", "audience", "people talking"],
    "whoosh": ["whoosh", "swoosh", "swish", "transition"],
    "hit": ["hit", "impact", "boom", "slam", "thud"],
    "riser": ["riser", "rising tension", "build-up", "buildup"],
    "ding": ["ding", "bell", "chime"],
    "pop": ["pop", "click", "blip"],
    "wind": ["wind", "howling"],
    "thunder": ["thunder", "storm"],
    "camera": ["camera shutter", "shutter", "camera click"],
    "typing": ["typing", "keyboard", "typewriter"],
}
_BED_SOUNDS = {"stomp", "drum", "crowd", "wind"}
_SOUND_VOLUME = {"scratch": 0.9, "heartbeat": 1.0, "stomp": 0.35, "drum": 0.35, "crowd": 0.25,
                 "whoosh": 0.5, "hit": 0.8, "riser": 0.6, "ding": 0.6, "pop": 0.5, "wind": 0.3,
                 "thunder": 0.7, "camera": 0.6, "typing": 0.5}


def _split_outside_quotes(text: str, seps=(",", ";")) -> List[str]:
    parts, buf, in_q = [], "", False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "\"“”":
            in_q = not in_q if ch == '"' else (ch == "“")
        if not in_q and ch in seps:
            parts.append(buf)
            buf = ""
        elif not in_q and text[i:i + 5].lower() == " and ":
            parts.append(buf)
            buf = ""
            i += 4
        else:
            buf += ch
        i += 1
    parts.append(buf)
    return [p.strip(" .") for p in parts if p.strip(" .")]


def parse_music(text: str) -> MusicPlan:
    plan = MusicPlan(description=text.strip())
    low = text.lower()
    scores = {m: sum(low.count(w) for w in ws) for m, ws in MOOD_SYNONYMS.items()}
    plan.moods = [m for m, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s > 0]
    plan.build = bool(re.search(r"\b(faster|builds?|building|grows?|growing|intensif|swell|crescendo|louder|escalat|ramps?\s+up)", low))
    for m in re.finditer(r"\b(?:silen|drop|cut|pause|mute|kill)\w*[^.]*?\bbefore\s+(?:the\s+)?([a-z]+)", low):
        plan.silence_before.append(m.group(1))
    return plan


def _detect_sound(item: str) -> Optional[str]:
    low = item.lower()
    best, pos = None, 10 ** 9
    for sound, words in SOUND_SYNONYMS.items():
        for w in words:
            m = re.search(rf"\b{re.escape(w)}\b", low)
            if m and m.start() < pos:
                best, pos = sound, m.start()
    return best


def parse_sfx(text: str, section_names: List[str]) -> List[SfxCue]:
    cues: List[SfxCue] = []
    sections_low = [s.lower() for s in section_names]
    for item in _split_outside_quotes(text):
        sound = _detect_sound(item)
        if not sound:
            continue
        low = item.lower()
        cue = SfxCue(sound=sound, volume=_SOUND_VOLUME.get(sound, 0.6))
        if re.search(r"\b(faint|soft|subtle|quiet|distant)\b", low):
            cue.volume *= 0.6
        cue.stop_music = bool(re.search(r"\bstop\b", low)) and sound == "scratch"

        q = re.search(r"[\"“]([^\"”]+)[\"”]", item)
        sec = re.search(r"\b(?:at|before|during|on|for|in)\s+(?:the\s+)?(?:start\s+of\s+(?:the\s+)?)?([a-z]+)", low)
        if q:
            cue.anchor, cue.phrase = "phrase", q.group(1).strip(" ,.")
        elif re.search(r"\b(ending|final|last|closing)\s+question\b", low):
            cue.anchor = "last_question"
        elif re.search(r"\b(every|each)\s+(cut|transition|scene)\b", low) or sound == "whoosh":
            cue.mode = "cuts"
        elif sec and sec.group(1) in sections_low:
            cue.anchor, cue.section = "section", sec.group(1)
            if sound in _BED_SOUNDS and re.search(r"\b(during|throughout|under)\b", low):
                cue.mode = "bed"
        elif sound in _BED_SOUNDS or re.search(r"\b(rhythmic|background|ambient|throughout|constant|loop)", low):
            cue.mode = "bed"
        cues.append(cue)
    return cues


_ILLUSTRATIVE = r"woodcut|engraving|etching|illustrat|painting|painted|cartoon|anime|sketch|watercolou?r|drawing|comic|pixel|ink|claymation|3d render|storybook|manuscript"


def parse_visuals(text: str) -> VisualPlan:
    vp = VisualPlan()
    text = text.strip()
    if not text:
        return vp
    parens = re.findall(r"\(([^)]*)\)", text)
    flat = re.sub(r"\([^)]*\)", "", text)
    flat = re.sub(r"\bblack\s+and\s+white\b", "black-and-white", flat, flags=re.I)
    items = _split_outside_quotes(flat)
    style_re = rf"{_ILLUSTRATIVE}|style|look|aesthetic|footage|cinematic|photo|black-and-white|vintage|retro|newspaper|film\s+grain"
    styles = []
    while items and re.search(style_re, items[0], flags=re.I) and not re.search(r"\bmap\b", items[0], flags=re.I):
        styles.append(items.pop(0).strip(" ."))
    vp.style = ", ".join(styles)
    vp.illustrative = bool(re.search(_ILLUSTRATIVE, vp.style, flags=re.I))
    if re.search(r"black[- ]and[- ]white|noir|monochrome|grayscale|greyscale", vp.style, flags=re.I):
        vp.look = "mono"
    elif re.search(r"woodcut|engraving|etching|sepia|vintage|parchment|medieval|antique|old[- ]map", vp.style, flags=re.I):
        vp.look = "sepia"

    for chunk in parens + [text]:
        for m in re.finditer(
            r"([A-Z][\w.\-]+(?:\s+[A-Z][\w.\-]+)*)['’]s\s+(\d{3,4})\s+(engraving|painting|woodcut|etching|illustration|drawing|map|photograph|photo|print|fresco)(\s+of\s+[\w\s]+?)?(?=[,.;)]|\s+is\b|\s+as\b|$)",
            chunk,
        ):
            ref = " ".join(p.strip() for p in m.groups() if p).strip()
            if ref not in vp.references:
                vp.references.append(ref)

    mp = re.search(r"\bmap(?:\s+pin)?\s+(?:on|of|showing|marking|pointing\s+(?:to|at))\s+(?:the\s+)?([A-Z][\w\-']+(?:[ ,]+[A-Z][\w\-']+)*)", text)
    if mp:
        vp.map_place = mp.group(1).strip(" ,")
    vp.suggestions = [i for i in items if not re.search(r"\bmap\b", i, flags=re.I)]
    return vp


# ── Scene splitting ───────────────────────────────────────────────────────────

def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.findall(r"[^.!?]*[.!?]+[\"”’']*|[^.!?]+$", text) if s.strip()]


def split_into_scenes(plan: ProductionPlan, min_words: int = 6, max_words: int = 15) -> List[Tuple[int, str]]:
    """Splits each section into ~3-5 second scenes. Returns [(section_index, narration)]."""
    out: List[Tuple[int, str]] = []
    for si, sec in enumerate(plan.sections):
        chunks: List[str] = []
        cur: List[str] = []
        for s in _sentences(sec.text):
            cw = sum(len(c.split()) for c in cur)
            if cur and cw >= min_words and cw + len(s.split()) > max_words:
                chunks.append(" ".join(cur))
                cur = []
            cur.append(s)
        if cur:
            if chunks and sum(len(c.split()) for c in cur) < 4:
                chunks[-1] = f"{chunks[-1]} {' '.join(cur)}"
            else:
                chunks.append(" ".join(cur))
        out.extend((si, c) for c in chunks)
    return out


# ── Timing resolution (after voiceover) ───────────────────────────────────────

_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "is", "are", "was", "were",
         "it", "this", "that", "they", "them", "their", "be", "with", "as", "by", "do", "did", "one", "what", "so"}


def _norm(w: str) -> str:
    return re.sub(r"[^\w%]", "", w.lower().replace("’", "'"))


def _stem(w: str) -> str:
    w = _norm(w)
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def find_phrase(words: List[dict], phrase: str, near: Optional[float] = None) -> Optional[float]:
    """Start time of the best match of `phrase` in the timed words."""
    toks = [_stem(t) for t in phrase.split() if _norm(t)]
    if not toks or not words:
        return None
    stems = [_stem(w["word"]) for w in words]
    best: Optional[Tuple[float, float]] = None
    for i in range(len(stems)):
        score = 0
        for k, t in enumerate(toks):
            if i + k < len(stems) and stems[i + k] == t:
                score += 1
        need = min(2, len(toks)) if len(toks) > 1 else 1
        if (score >= need and stems[i] == toks[0]) or (score > 0 and score >= max(need, len(toks) - 1)):
            dist = abs(words[i]["start"] - near) if near is not None else 0.0
            key = (-score, dist)
            if best is None or key < best[0]:
                best = (key, words[i]["start"])
    return best[1] if best else None


def resolve_timing(plan: ProductionPlan, scene_durations: List[float], words: List[dict]) -> None:
    """Fills actual times for sections, overlays, SFX and music silences."""
    total = sum(scene_durations)
    t = 0.0
    for sec in plan.sections:
        sec.act_start = sec.act_end = None
    for si, dur in zip(plan.scene_sections, scene_durations):
        sec = plan.sections[si]
        if sec.act_start is None:
            sec.act_start = t
        t += dur
        sec.act_end = t
    for sec in plan.sections:
        if sec.act_start is None:
            sec.act_start = sec.act_end = t

    def est_to_actual(x: float) -> Tuple[float, float]:
        for sec in plan.sections:
            if sec.est_start <= x < sec.est_end or sec is plan.sections[-1]:
                est_len = max(0.1, sec.est_end - sec.est_start)
                act_len = sec.act_end - sec.act_start
                frac = min(1.0, max(0.0, (x - sec.est_start) / est_len))
                return sec.act_start + frac * act_len, act_len / est_len
        return x, 1.0

    # Overlays
    prev_end = 0.0
    for ov in plan.overlays:
        keys = [k for k in (_stem(w) for w in ov.text.split()) if k and k not in _STOP and (len(k) >= 3 or k.isdigit())]
        if ov.est_start is not None:
            guess, ratio = (0.0, 1.0) if ov.est_start <= 0.3 else est_to_actual(ov.est_start)
            span = ((ov.est_end or ov.est_start + 3) - ov.est_start) * ratio
        else:
            guess, span = prev_end, 3.0
        start = guess
        if guess > 0.3 and keys:
            sec = next((x for x in plan.sections if x.act_start <= guess < x.act_end), None)
            lo, hi = (sec.act_start - 2.0, sec.act_end + 1.0) if sec else (guess - 4.0, guess + 4.0)
            hits = [w["start"] for w in words if _stem(w["word"]) in keys and lo <= w["start"] <= hi]
            if hits:
                nearest = min(hits, key=lambda h: abs(h - guess))
                earlier = [h for h in hits if nearest - 1.5 <= h <= nearest]
                start = max(0.0, min(earlier) - 0.1)
        ov.start = start
        ov.end = min(total, start + min(5.0, max(1.6, span)))
        prev_end = ov.end

    occupied: List[float] = []
    for ov in sorted(plan.overlays, key=lambda o: o.start):
        slot = next((i for i, e in enumerate(occupied) if e <= ov.start + 0.05), len(occupied))
        if slot == len(occupied):
            occupied.append(0.0)
        occupied[slot] = ov.end
        ov.slot = slot

    # SFX
    last_q = None
    for sent in _sentences(plan.narration.replace("\n", " ")):
        if sent.rstrip("\"”’' ").endswith("?"):
            last_q = sent
    cut_times, acc = [], 0.0
    for d in scene_durations[:-1]:
        acc += d
        cut_times.append(acc)
    for cue in plan.sfx:
        cue.times = []
        if cue.mode == "cuts":
            cue.times = [(max(0.0, c - 0.15), 0.0) for c in cut_times]
            continue
        if cue.mode == "bed":
            secs = [plan.sections[i] for i in ([plan.section_index(cue.section)] if cue.section and plan.section_index(cue.section) is not None else [])]
            if not secs:
                main = [s for s in plan.sections if s.name.lower() in ("main", "body", "story", "middle")]
                secs = main or plan.sections
            cue.times = [(secs[0].act_start, secs[-1].act_end)]
            continue
        at = None
        if cue.anchor == "phrase":
            at = find_phrase(words, cue.phrase)
        elif cue.anchor == "last_question" and last_q:
            at = find_phrase(words, " ".join(last_q.split()[:4]), near=total)
            if at is not None and len(last_q.split()) > 4:
                q_end = find_phrase(words, " ".join(last_q.split()[-2:]), near=at + 2)
                at = q_end if q_end is not None and q_end > at else at
        elif cue.anchor == "section":
            idx = plan.section_index(cue.section)
            at = plan.sections[idx].act_start if idx is not None else None
        elif cue.anchor == "start":
            at = 0.0
        if at is None:
            logger.warning(f"SFX '{cue.sound}': could not place anchor '{cue.phrase or cue.anchor}', skipping")
            continue
        cue.times = [(max(0.0, at - 0.05), 0.0)]

    # Music silences
    plan.music.silences = []
    for name in plan.music.silence_before:
        idx = plan.section_index(name)
        if idx is not None and idx > 0:
            s = plan.sections[idx].act_start
            plan.music.silences.append((max(0.0, s - 0.6), s + 0.05))
    for cue in plan.sfx:
        if cue.stop_music and cue.times:
            s = cue.times[0][0]
            plan.music.silences.append((s + 0.15, s + 0.9))

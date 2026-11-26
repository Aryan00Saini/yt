import os
import json
import re
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator
from src.utils import logger

class Scene(BaseModel):
    narration: str = Field(description="8-14 spoken words")
    visual_keywords: List[str] = Field(description="2-4 literal, filmable search terms, most specific first")
    image_prompt: str = ""                       # falls back to keywords when empty
    emphasis: List[str] = Field(default_factory=list)
    card_text: Optional[str] = None

class ShortScript(BaseModel):
    title: str
    description: str
    tags: List[str]
    scenes: List[Scene]
    hook_text: str = ""                          # max 4 words, ALL CAPS

    @model_validator(mode="after")
    def _fill_hook(self):
        if not self.hook_text and self.scenes:
            words = re.sub(r"[^\w\s%°']", "", self.scenes[0].narration).split()
            self.hook_text = " ".join(words[:4]).upper()
        return self


PROMPT_TEMPLATE = """You are a scriptwriter for a faceless {niche} Shorts channel. Write a {duration}-second script about: {topic}

Structure
- Scene 1 (the hook, max 12 words): a surprising claim, a specific number, or a question that opens a curiosity gap. Never start with "Did you know", "Imagine", "Hey guys" or "Welcome".
- Middle scenes: one new fact or beat per scene. Each should make the viewer want the next one.
- Last scene: a payoff line that echoes the hook so the video loops. No "subscribe", "like" or "follow".

Rules
- Exactly {scene_count} scenes, 8-14 words of narration each, about {words} words in total.
- Spoken style: short sentences, contractions, plain words, specific numbers and names instead of vague claims.
- Include only claims you are highly confident are true. If unsure, leave it out. No invented statistics, studies or quotes.
- visual_keywords: literal things a camera can film (noun + setting), e.g. "astronaut floating", "rocket launch night". Most specific first. Never abstract words like mystery, power, secret, amazing.
- image_prompt: one sentence, one subject, no text in the image.
- emphasis: copy 1-2 words exactly from that scene's narration — the number, name, or most surprising word. These will appear in a different color on screen.
- card_text: a striking number or 1-3 word phrase shown full-screen instead of a visual. Use in at most 2 scenes across the whole script; set to null for all other scenes.
- hook_text: max 4 words ALL CAPS distilling the most shocking part of scene 1 (e.g. "10 BILLION DEGREES"). This overlays the first 1.8 seconds.
"""


def clean_json_response(raw_text: str) -> str:
    """Removes markdown code fences and extracts the raw JSON substring."""
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```$", "", text, flags=re.MULTILINE)
    text = text.strip()
    match = re.search(r"(\{.*\})", text, flags=re.DOTALL)
    return match.group(1).strip() if match else text


def generate_script_gemini(
    topic: str,
    duration: int = 45,
    api_key: Optional[str] = None,
    niche: str = "science facts",
    model_name: str = "gemini-2.5-flash",
    words_per_second: float = 2.5,
    seconds_per_scene: float = 3.5,
) -> ShortScript:
    """Generates structured script using Google Gemini structured output."""
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY is not set in environment or .env file.")

    scene_count = max(4, round(duration / seconds_per_scene))
    words = round(duration * words_per_second)
    prompt = PROMPT_TEMPLATE.format(
        topic=topic,
        duration=duration,
        niche=niche,
        scene_count=scene_count,
        words=words,
    )

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ShortScript,
            temperature=0.8,
        ),
    )
    return resp.parsed


def generate_script_groq(
    topic: str,
    duration: int = 45,
    api_key: Optional[str] = None,
    niche: str = "science facts",
    words_per_second: float = 2.5,
    seconds_per_scene: float = 3.5,
) -> ShortScript:
    """Generates structured script using Groq API (fallback)."""
    import requests

    key = api_key or os.getenv("GROQ_API_KEY")
    if not key:
        raise ValueError("GROQ_API_KEY is not set.")

    scene_count = max(4, round(duration / seconds_per_scene))
    words = round(duration * words_per_second)
    prompt = PROMPT_TEMPLATE.format(
        topic=topic,
        duration=duration,
        niche=niche,
        scene_count=scene_count,
        words=words,
    )

    schema_hint = json.dumps(ShortScript.model_json_schema())
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": f"Output valid JSON matching this schema: {schema_hint}"},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
        },
        timeout=30,
    )
    resp.raise_for_status()
    raw_text = resp.json()["choices"][0]["message"]["content"]
    return ShortScript.model_validate(json.loads(clean_json_response(raw_text)))


def generate_script(
    topic: str,
    duration: int = 45,
    niche: str = "science facts",
    model_name: str = "gemini-2.5-flash",
    words_per_second: float = 2.5,
    seconds_per_scene: float = 3.5,
) -> ShortScript:
    """Main entry for script generation. Fails loudly if no LLM key is set or all providers fail."""
    gemini_key = os.getenv("GEMINI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")

    if not gemini_key and not groq_key:
        raise RuntimeError(
            "No LLM API key found. Set GEMINI_API_KEY or GROQ_API_KEY in .env before running."
        )

    errors = []
    if gemini_key:
        try:
            logger.info(f"Generating script using Gemini ({model_name}) for topic: '{topic}'...")
            return generate_script_gemini(
                topic, duration, gemini_key, niche=niche, model_name=model_name,
                words_per_second=words_per_second, seconds_per_scene=seconds_per_scene
            )
        except Exception as e:
            logger.error(f"Gemini generation error: {e}")
            errors.append(f"Gemini: {e}")

    if groq_key:
        try:
            logger.info(f"Generating script using Groq for topic: '{topic}'...")
            return generate_script_groq(
                topic, duration, groq_key, niche=niche,
                words_per_second=words_per_second, seconds_per_scene=seconds_per_scene
            )
        except Exception as e:
            logger.error(f"Groq generation error: {e}")
            errors.append(f"Groq: {e}")

    raise RuntimeError("All script generation providers failed: " + " | ".join(errors))


CUSTOM_SCRIPT_PROMPT = """You are a YouTube Shorts video director.
Take the user's EXACT script below and split it into visual scenes (roughly 2.5-4 seconds each).

CRITICAL RULES:
1. Do NOT rewrite, summarize, or alter the user's words. Keep the exact narration text across the scenes in order.
2. For each scene, provide 2-4 literal, filmable visual_keywords (most specific first) and a one-sentence image_prompt (no text in image).
3. Generate a catchy title (under 60 chars), description with #Shorts, and 5-8 tags.
4. Return strictly valid JSON.

User Script:
\"\"\"{custom_script}\"\"\"
"""


def _split_custom_script_offline(script_text: str, title: Optional[str] = None) -> ShortScript:
    """Splits a custom script into scenes without an LLM by paragraphs or sentences."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", script_text.strip()) if p.strip()]
    if len(paragraphs) < 2:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script_text.strip()) if s.strip()]
        paragraphs = []
        chunk_size = max(1, (len(sentences) + 4) // 5) if len(sentences) > 5 else 1
        for i in range(0, len(sentences), chunk_size):
            paragraphs.append(" ".join(sentences[i:i + chunk_size]))

    stop_words = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
        "with", "is", "are", "was", "were", "it", "this", "that", "you", "your",
        "they", "their", "what", "how", "why", "when", "if", "can", "will", "just"
    }
    scenes: List[Scene] = []
    for p in paragraphs:
        words = [w.strip(".,!?;:\"'()[]{}").lower() for w in p.split()]
        keywords = [w for w in words if len(w) > 3 and w not in stop_words][:4]
        if not keywords:
            keywords = ["cinematic background", "abstract motion"]
        scenes.append(Scene(narration=p, visual_keywords=keywords, image_prompt=p))

    clean_title = (title or paragraphs[0][:50].rstrip(".,!?")) + " #Shorts"
    return ShortScript(
        title=clean_title[:60],
        description=f"{paragraphs[0]}\n\n#Shorts",
        tags=["shorts", "story", "facts"],
        scenes=scenes,
    )


def generate_script_from_custom_text(script_text: str, title: Optional[str] = None) -> ShortScript:
    """Converts a user-provided custom script into a structured ShortScript while preserving narration."""
    prompt = CUSTOM_SCRIPT_PROMPT.format(custom_script=script_text.strip())
    gemini_key = os.getenv("GEMINI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")

    if gemini_key:
        try:
            logger.info("Structuring custom script into scenes using Gemini...")
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ShortScript,
                    temperature=0.3,
                ),
            )
            parsed = resp.parsed
            if title:
                parsed.title = title[:60]
            return parsed
        except Exception as e:
            logger.warning(f"Gemini custom script structuring failed: {e}")

    if groq_key:
        try:
            import requests
            logger.info("Structuring custom script into scenes using Groq...")
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": "Output valid JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3,
                },
                timeout=30,
            )
            resp.raise_for_status()
            parsed = ShortScript.model_validate(json.loads(clean_json_response(resp.json()["choices"][0]["message"]["content"])))
            if title:
                parsed.title = title[:60]
            return parsed
        except Exception as e:
            logger.warning(f"Groq custom script structuring failed: {e}")

    logger.info("Structuring custom script offline...")
    return _split_custom_script_offline(script_text, title=title)

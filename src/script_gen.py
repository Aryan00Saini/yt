import os
import json
import re
from typing import List, Optional
from pydantic import BaseModel, Field
from src.utils import logger

class Scene(BaseModel):
    narration: str = Field(description="The spoken narration text for this scene (punchy, 1-2 sentences)")
    visual_keywords: List[str] = Field(description="2-4 search keywords for finding relevant stock video clips")

class ShortScript(BaseModel):
    title: str = Field(description="Catchy YouTube Short title under 60 characters with hook")
    description: str = Field(description="Description with relevant tags and summary")
    tags: List[str] = Field(description="5-8 relevant trending tags")
    scenes: List[Scene] = Field(description="Ordered list of scenes composing the short (typically 4 to 7 scenes)")

PROMPT_TEMPLATE = """You are a viral YouTube Shorts scriptwriter. Create an engaging, fast-paced script for a 9:16 vertical short video.

Topic: {topic}
Target Duration: {duration} seconds (roughly 110-140 words total narration).

Rules:
1. Strong Hook: The very first sentence MUST grab attention instantly (curiosity gap, shocking fact, or question).
2. Pacing: Break the content into 4-7 short, punchy scenes.
3. Narration: Keep it natural, energetic, conversational. No generic filler words.
4. Visual Keywords: For each scene, give 2-4 clean, literal keywords suitable for stock video search (e.g., ["deep space nebula", "black hole", "galaxy"]).
5. Return strictly valid JSON with no markdown wrapping (do NOT use ```json code fences).

Expected JSON Structure:
{{
  "title": "Short catchy title with hook",
  "description": "Engaging description #shorts #topic",
  "tags": ["shorts", "tag1", "tag2"],
  "scenes": [
    {{
      "narration": "What if I told you that a single teaspoon of a neutron star weighs as much as Mount Everest?",
      "visual_keywords": ["neutron star space", "glowing celestial body"]
    }},
    ...
  ]
}}
"""

def clean_json_response(raw_text: str) -> str:
    """Removes markdown code fences and extracts the raw JSON substring."""
    text = raw_text.strip()
    # Remove markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```$", "", text, flags=re.MULTILINE)
    text = text.strip()
    
    # In case there is text before or after the JSON object
    match = re.search(r"(\{.*\})", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return text

def generate_script_gemini(topic: str, duration: int = 45, api_key: Optional[str] = None) -> ShortScript:
    """Generates structured script using Google Gemini API."""
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY is not set in environment or .env file.")

    prompt = PROMPT_TEMPLATE.format(topic=topic, duration=duration)
    
    # Try using google.genai or fallback to google.generativeai or requests
    try:
        from google import genai
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        raw_text = response.text
    except ImportError:
        # Fallback to requests directly for zero-dependency API call
        import requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        resp.raise_for_fail() if hasattr(resp, "raise_for_fail") else resp.raise_for_status()
        data = resp.json()
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]

    cleaned = clean_json_response(raw_text)
    
    try:
        data = json.loads(cleaned)
        return ShortScript.model_validate(data)
    except Exception as e:
        logger.warning(f"Failed parsing Gemini JSON response on first attempt: {e}. Retrying with repair...")
        # Simple repair attempt
        cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)
        data = json.loads(cleaned)
        return ShortScript.model_validate(data)

def generate_script_groq(topic: str, duration: int = 45, api_key: Optional[str] = None) -> ShortScript:
    """Generates structured script using Groq API (fallback)."""
    import requests
    key = api_key or os.getenv("GROQ_API_KEY")
    if not key:
        raise ValueError("GROQ_API_KEY is not set.")

    prompt = PROMPT_TEMPLATE.format(topic=topic, duration=duration)
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "You are a professional YouTube Shorts scriptwriter who outputs valid JSON only."},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    raw_text = resp.json()["choices"][0]["message"]["content"]
    cleaned = clean_json_response(raw_text)
    data = json.loads(cleaned)
    return ShortScript.model_validate(data)

def generate_script_fallback(topic: str, duration: int = 45) -> ShortScript:
    """Mock/offline fallback generator if no API keys are present."""
    logger.info("Using built-in template generator (offline fallback)")
    return ShortScript(
        title=f"The Truth About {topic.title()} #Shorts",
        description=f"Discover fascinating facts about {topic}! Subscribe for more. #Shorts #{topic.replace(' ', '')}",
        tags=["shorts", "facts", "curiosity", topic.lower()],
        scenes=[
            Scene(
                narration=f"Did you know the universe is full of mysteries, and {topic} is one of the most astonishing?",
                visual_keywords=[topic, "mystery universe", "galaxy space"]
            ),
            Scene(
                narration=f"Scientists have discovered incredible phenomena that completely challenge how we understand {topic}.",
                visual_keywords=["laboratory scientist", "quantum physics", "technology"]
            ),
            Scene(
                narration="Every single observation reveals details that seem almost impossible to believe.",
                visual_keywords=["mind blowing discovery", "cinematic space", "abstract light"]
            ),
            Scene(
                narration="Subscribe right now if you want to uncover more mind-blowing secrets of the cosmos!",
                visual_keywords=["subscribe reminder", "space zoom", "cinematic motion"]
            )
        ]
    )

def generate_script(topic: str, duration: int = 45) -> ShortScript:
    """Main entry for script generation."""
    gemini_key = os.getenv("GEMINI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")

    if gemini_key:
        try:
            logger.info(f"Generating script using Gemini for topic: '{topic}'...")
            return generate_script_gemini(topic, duration, gemini_key)
        except Exception as e:
            logger.error(f"Gemini generation error: {e}. Falling back to Groq or template...")

    if groq_key:
        try:
            logger.info(f"Generating script using Groq for topic: '{topic}'...")
            return generate_script_groq(topic, duration, groq_key)
        except Exception as e:
            logger.error(f"Groq generation error: {e}. Falling back to template...")
    
    return generate_script_fallback(topic, duration)


CUSTOM_SCRIPT_PROMPT = """You are a YouTube Shorts video director.
Take the user's EXACT script below and split it into 4-7 visual scenes.

CRITICAL RULES:
1. Do NOT rewrite, summarize, or alter the user's words. Keep the exact narration text across the scenes in order.
2. For each scene, provide 2-4 literal, descriptive visual keywords for finding matching images/videos.
3. Generate a catchy title (under 60 chars), description with #Shorts, and 5-8 tags.
4. Return strictly valid JSON with no markdown code fences.

User Script:
\"\"\"{custom_script}\"\"\"

Expected JSON Structure:
{{
  "title": "Short catchy title",
  "description": "Engaging description #Shorts",
  "tags": ["shorts", "tag1", "tag2"],
  "scenes": [
    {{
      "narration": "Exact sentence(s) from the user's script...",
      "visual_keywords": ["keyword 1", "keyword 2"]
    }}
  ]
}}
"""


def _split_custom_script_offline(script_text: str, title: Optional[str] = None) -> ShortScript:
    """Splits a custom script into scenes without an LLM by paragraphs or sentences."""
    # Prefer blank-line paragraphs if the user separated scenes manually
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", script_text.strip()) if p.strip()]
    if len(paragraphs) < 2:
        # Split into sentences and group 1-2 sentences per scene
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
        scenes.append(Scene(narration=p, visual_keywords=keywords))

    clean_title = (title or paragraphs[0][:50].rstrip(".,!?")) + " #Shorts"
    return ShortScript(
        title=clean_title[:60],
        description=f"{paragraphs[0]}\n\n#Shorts",
        tags=["shorts", "story", "facts"],
        scenes=scenes
    )


def generate_script_from_custom_text(script_text: str, title: Optional[str] = None) -> ShortScript:
    """
    Converts a user-provided custom script into a structured ShortScript while
    preserving the user's exact narration.
    """
    prompt = CUSTOM_SCRIPT_PROMPT.format(custom_script=script_text.strip())
    gemini_key = os.getenv("GEMINI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")

    if gemini_key:
        try:
            logger.info("Structuring custom script into scenes using Gemini...")
            from google import genai
            client = genai.Client(api_key=gemini_key)
            response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            cleaned = clean_json_response(response.text)
            parsed = ShortScript.model_validate(json.loads(cleaned))
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
                        {"role": "user", "content": prompt}
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3,
                },
                timeout=30,
            )
            resp.raise_for_status()
            cleaned = clean_json_response(resp.json()["choices"][0]["message"]["content"])
            parsed = ShortScript.model_validate(json.loads(cleaned))
            if title:
                parsed.title = title[:60]
            return parsed
        except Exception as e:
            logger.warning(f"Groq custom script structuring failed: {e}")

    logger.info("Structuring custom script offline...")
    return _split_custom_script_offline(script_text, title=title)

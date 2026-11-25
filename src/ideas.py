"""
ideas.py – Topic idea generator and history tracker.

Usage (CLI):
    python -m src.ideas --niche "science facts" --count 20

The command appends new ideas to output/ideas.json and reads
output/history.json to skip topics that have already been produced.
"""

import json
import os
from pathlib import Path
from typing import Optional

from src.utils import logger, OUTPUT_DIR

IDEAS_FILE = OUTPUT_DIR / "ideas.json"
HISTORY_FILE = OUTPUT_DIR / "history.json"


def _load_json(path: Path) -> list:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            pass
    return []


def _save_json(path: Path, data: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_history() -> set:
    """Returns a set of slugified topics that have already been produced."""
    return {item.get("topic", "").lower() for item in _load_json(HISTORY_FILE)}


def mark_done(topic: str, output_path: Optional[Path] = None) -> None:
    """Record a finished topic in output/history.json."""
    history = _load_json(HISTORY_FILE)
    history.append({
        "topic": topic,
        "output": str(output_path) if output_path else None,
    })
    _save_json(HISTORY_FILE, history)
    logger.info(f"Marked as done: '{topic}'")


def generate_ideas(
    niche: str = "science facts",
    count: int = 20,
    model_name: str = "gemini-2.5-flash",
) -> list:
    """
    Ask the LLM for ``count`` fresh topic ideas with hook angles.
    Already-produced topics (from history.json) are excluded from the prompt.

    Returns a list of dicts: [{"topic": str, "hook": str}, ...]
    """
    history = load_history()
    existing = _load_json(IDEAS_FILE)
    existing_topics = {item.get("topic", "").lower() for item in existing}

    prompt = f"""You are a YouTube Shorts content strategist for a faceless {niche!r} channel.

Generate exactly {count} short-form video ideas.  Each idea must:
- Be a specific, fact-based topic (not a broad theme).
- Have a hook angle: one sentence that opens a curiosity gap, uses a surprising number, or asks a question that most people get wrong.
- Never repeat a topic from this list: {sorted(history | existing_topics)!r}

Return a JSON array of objects with keys "topic" (string) and "hook" (string).
"""

    gemini_key = os.getenv("GEMINI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")

    if not gemini_key and not groq_key:
        raise RuntimeError("No LLM API key found. Set GEMINI_API_KEY or GROQ_API_KEY in .env.")

    ideas = []

    if gemini_key:
        try:
            from google import genai
            from google.genai import types
            from pydantic import BaseModel, Field

            class _Idea(BaseModel):
                topic: str = Field(description="Specific video topic")
                hook: str = Field(description="One-sentence curiosity-gap opening")

            class _Ideas(BaseModel):
                ideas: list[_Idea]

            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_Ideas,
                    temperature=0.9,
                ),
            )
            ideas = [i.model_dump() for i in resp.parsed.ideas]
        except Exception as e:
            logger.warning(f"Gemini idea generation failed: {e}")

    if not ideas and groq_key:
        try:
            import requests as _req

            resp = _req.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": "Output valid JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.9,
                },
                timeout=30,
            )
            resp.raise_for_status()
            raw = json.loads(resp.json()["choices"][0]["message"]["content"])
            ideas = raw.get("ideas", raw) if isinstance(raw, dict) else raw
        except Exception as e:
            logger.warning(f"Groq idea generation failed: {e}")

    if not ideas:
        raise RuntimeError("All LLM providers failed to generate ideas.")

    # Merge with existing list (deduplicate by topic)
    all_topics = {item["topic"].lower() for item in existing}
    new_ideas = [i for i in ideas if i.get("topic", "").lower() not in all_topics]
    merged = existing + new_ideas
    _save_json(IDEAS_FILE, merged)
    logger.info(f"Added {len(new_ideas)} new ideas → {IDEAS_FILE}")
    return new_ideas


# ── CLI entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate Shorts topic ideas.")
    parser.add_argument("--niche", default="science facts", help="Channel niche")
    parser.add_argument("--count", type=int, default=20, help="Number of ideas to generate")
    parser.add_argument("--model", default="gemini-2.5-flash", help="LLM model name")
    args = parser.parse_args()

    ideas = generate_ideas(niche=args.niche, count=args.count, model_name=args.model)
    print(f"\n✅ {len(ideas)} new ideas written to {IDEAS_FILE}\n")
    for item in ideas:
        print(f"  • {item['topic']}")
        print(f"    Hook: {item['hook']}\n")

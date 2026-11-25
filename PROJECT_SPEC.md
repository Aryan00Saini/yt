# AI Shorts Maker: Project Spec

Build a free, CPU-only tool that turns a topic into a finished vertical (9:16) YouTube Short: script, voiceover, visuals, captions, music, rendered video, optional auto-upload.

## 1. Goals and constraints

- **Cost:** everything must use free tools and free API tiers. No paid services.
- **Hardware:** the target machine has **no GPU** and about 8 GB RAM. All local processing must run on CPU.
- **No real text-to-video models** (Sora/Veo/Runway style) locally. Visuals come from stock clips and AI-generated images, animated with pan/zoom effects.
- **Output:** 1080x1920 MP4, 30 to 60 seconds, with burned-in word-level captions, voiceover, and background music.
- **Language:** Python 3.10+.

## 2. Pipeline

```
topic -> script -> voiceover -> word timestamps -> visuals per scene -> assemble -> (optional) upload
```

| Step | Tool | Runs |
|---|---|---|
| Script / idea | Gemini API free tier (fallback: Groq or OpenRouter free models) | Cloud API |
| Voiceover | `edge-tts` (default); Piper or Kokoro TTS as offline fallback | Local CPU |
| Visuals (stock) | Pexels API, Pixabay API (free video clips) | Cloud API |
| Visuals (AI images) | Pollinations.ai or Hugging Face free inference (optional) | Cloud |
| Animation | Ken Burns zoom/pan on images, transitions, animated text | Local CPU |
| Captions | `faster-whisper` (`base` or `small`, int8) for word-level timestamps | Local CPU |
| Music | Local folder of royalty-free tracks (YouTube Audio Library / Pixabay Music) | Local |
| Assembly | MoviePy + FFmpeg | Local CPU |
| Upload | YouTube Data API v3 | Cloud API |
| Automation | GitHub Actions (optional) | Cloud |

## 3. Tech stack

- Python 3.10+, FFmpeg (system install)
- Libraries: `google-generativeai` (or `google-genai`), `edge-tts`, `faster-whisper`, `moviepy`, `requests`, `python-dotenv`, `pydantic`, `typer` (CLI), `google-api-python-client` and `google-auth-oauthlib` (upload)
- Optional UI: Streamlit or Gradio

## 4. Suggested project structure

```
ai-shorts-maker/
├── README.md
├── requirements.txt
├── .env.example
├── config.yaml              # resolution, fps, voice, whisper model, caption style
├── main.py                  # CLI entry: python main.py --topic "..."
├── app.py                   # optional Streamlit/Gradio UI
├── src/
│   ├── script_gen.py        # topic -> structured script (JSON scenes)
│   ├── tts.py               # script -> audio file (edge-tts)
│   ├── captions.py          # audio -> word-level timestamps (faster-whisper)
│   ├── visuals.py           # scene keywords -> Pexels clips / AI images
│   ├── animate.py           # Ken Burns, transitions, text effects
│   ├── assemble.py          # combine video + audio + captions + music
│   ├── upload.py            # YouTube Data API upload (optional)
│   └── utils.py             # caching, retries, logging, paths
├── assets/
│   ├── music/               # royalty-free tracks
│   └── fonts/               # caption fonts
├── cache/                   # downloaded clips, audio, timestamps
└── output/                  # final MP4s
```

## 5. Module requirements

### script_gen.py
- Input: topic, niche, target duration (default 45s).
- Prompt the LLM to return **JSON only**: `{title, description, tags[], scenes[{narration, visual_keywords[]}]}`.
- Strip markdown fences before parsing. Validate with pydantic. Retry once on invalid JSON.
- Script must have a strong hook in the first sentence and be original, not generic filler.

### tts.py
- Use `edge-tts` with a configurable voice (default: an English neural voice). Output MP3/WAV per full script.
- Optional offline fallback to Piper or Kokoro.

### captions.py
- Run `faster-whisper` on the voiceover with `word_timestamps=True`, CPU, `compute_type="int8"`.
- Return a list of `{word, start, end}`.

### visuals.py
- For each scene, search Pexels (portrait orientation) using `visual_keywords`. Download and cache the best match.
- Fall back to Pixabay, then to an AI-generated image (Pollinations/HF) if no clip is found.
- Each scene's visual duration must match its narration duration.

### animate.py
- Images: Ken Burns zoom/pan. Clips: crop/resize to 1080x1920.
- Crossfade or simple transitions between scenes.

### assemble.py
- Compose scene visuals, voiceover, background music (ducked to about 10-15% volume), and burned-in captions (large, bold, centered, active word highlighted).
- Export H.264 MP4, 1080x1920, 30 fps, AAC audio.

### upload.py (optional)
- OAuth flow for YouTube Data API. Upload with title, description, tags, `#Shorts`. Default privacy: **private** until reviewed.

## 6. Configuration

`.env.example`:
```
GEMINI_API_KEY=
PEXELS_API_KEY=
PIXABAY_API_KEY=
YOUTUBE_CLIENT_SECRETS_FILE=client_secret.json
```

All keys are free. Never commit `.env` or `client_secret.json`.

## 7. Build order (milestones)

1. **M1:** CLI: topic -> script (Gemini) -> voiceover (edge-tts).
2. **M2:** Fetch matching Pexels clips per scene and stitch them to the voiceover length.
3. **M3:** Whisper word-level captions burned into the 9:16 video.
4. **M4:** Add background music and export the final MP4.
5. **M5 (optional):** Streamlit/Gradio UI, then YouTube auto-upload, then GitHub Actions scheduling.

Complete and test each milestone before starting the next.

## 8. Acceptance criteria

- `python main.py --topic "5 facts about black holes"` produces `output/<slug>.mp4` with no manual steps.
- Video is 1080x1920, 30 to 60 seconds, with synced captions and audible voiceover.
- A 60s video renders on a CPU-only machine with 8 GB RAM in a reasonable time (a few minutes).
- API responses and downloads are cached so reruns do not re-download.
- Clear error messages for missing API keys, rate limits, and failed downloads, with retries and backoff.
- No paid API required anywhere.

## 9. Notes and risks

- **Free tiers change.** Check current limits for Gemini, Pexels, and YouTube API quota before relying on them.
- **Monetization:** YouTube demonetizes mass-produced, repetitive, low-effort AI content. Keep scripts original, pick a clear niche, and make real editing choices. YouTube Partner Program also requires minimum subscriber and watch/view thresholds.
- **Licensing:** check each stock site's license. Use only royalty-free music.
- **Optional real AI video:** open models (e.g. Wan, CogVideo) can run on Kaggle (about 30 GPU hours/week) or Google Colab free tier as a separate notebook. Expect short, low-res clips and queue limits. Keep this out of the core CPU pipeline.

## 10. Instructions for the coding agent

- Build milestone by milestone and run each module before moving on.
- Keep code modular, typed, and commented; one responsibility per file.
- Write a `README.md` with setup steps (install FFmpeg, create venv, `pip install -r requirements.txt`, copy `.env.example` to `.env`) and usage examples.
- Prefer simple, readable solutions over clever ones.

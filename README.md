# AI Shorts Maker 🎬

A free, 100% CPU-friendly tool that turns any topic into a finished vertical (9:16) YouTube Short: script generation, voiceover, stock visuals & animated images, word-level synchronized captions, background music, and optional auto-upload.

---

## ⚡ Features
- **Zero Cost / Free Tier Only:** Uses Gemini API (or built-in fallback), Edge-TTS, faster-whisper (int8 CPU), Pexels/Pixabay free stock APIs, and Pollinations.ai for AI imagery.
- **CPU & Low-RAM Optimized:** Designed to run reliably without a GPU and within 8 GB RAM using `faster-whisper` int8 quantization and MoviePy `ultrafast` H.264 CPU encoding.
- **Smart Framing (9:16):** Ken Burns zoom/pan on still images and center-cropping for vertical shorts (1080x1920 @ 30fps).
- **Burned-In Dynamic Captions:** Word-level timestamps highlighting active spoken words with snappy grouping.
- **Background Music Ducking:** Automatically layers and ducks background audio under the narration.
- **Dual Interfaces:** Easy CLI (`main.py`) or interactive web UI (`streamlit run app.py`).

---

## 📋 System Prerequisites

### 1. Install FFmpeg
FFmpeg is required for video and audio rendering.
- **Windows (winget):**
  ```powershell
  winget install Gyan.FFmpeg
  ```
- **Windows (Chocolatey):**
  ```powershell
  choco install ffmpeg
  ```
- *Note:* Ensure `ffmpeg` is added to your system `PATH`. Restart your terminal after installing.

### 2. Python 3.10+
Verify Python is installed:
```powershell
python --version
```

---

## 🚀 Setup & Installation

1. **Clone or enter the project directory:**
   ```powershell
   cd c:\yt
   ```

2. **Create and activate a virtual environment:**
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

3. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

4. **Set up API keys:**
   Copy `.env.example` to `.env`:
   ```powershell
   Copy-Item .env.example .env
   ```
   Open `.env` and fill in:
   - `GEMINI_API_KEY`: Free key from [Google AI Studio](https://aistudio.google.com/)
   - `PEXELS_API_KEY`: Free API key from [Pexels](https://www.pexels.com/api/)
   - `PIXABAY_API_KEY`: Free API key from [Pixabay](https://pixabay.com/api/docs/) (optional fallback)

*(Note: If no API keys are provided, the pipeline gracefully falls back to built-in script templates, free Pollinations.ai imagery, and procedural visual backdrops!)*

---

## 🎥 Usage

### 1. Command Line Interface (CLI)
Generate a short from any topic:
```powershell
python main.py --topic "5 facts about black holes" --duration 45
```

Options:
- `--topic`, `-t`: Topic or query for the video (Required).
- `--duration`, `-d`: Target length in seconds (Default: 45).
- `--whisper`, `-w`: Whisper model size: `tiny`, `base`, or `small` (Default: `base`).
- `--upload`, `-u`: Automatically upload the generated video to YouTube as a **private** Short (Requires `client_secret.json`).

The final MP4 will be rendered to:
`output/<topic_slug>.mp4`

### 2. Streamlit Web UI
Run the interactive dashboard:
```powershell
streamlit run app.py
```

---

## 📁 Project Architecture
```
ai-shorts-maker/
├── .env.example            # Environment template for free API keys
├── config.yaml             # Video resolutions, Edge-TTS voice, caption styling
├── requirements.txt        # Python dependencies
├── main.py                 # CLI entry point (Typer)
├── app.py                  # Streamlit graphical interface
├── src/
│   ├── script_gen.py       # Topic -> JSON script (Gemini with offline fallback)
│   ├── tts.py              # Narration -> Audio (Edge-TTS)
│   ├── captions.py         # Word-level timestamps (faster-whisper CPU)
│   ├── visuals.py          # Pexels / Pixabay / Pollinations AI image fetcher
│   ├── animate.py          # Ken Burns zoom/pan & 9:16 vertical crop
│   ├── assemble.py         # MoviePy video compositor + caption overlay + audio ducking
│   ├── upload.py           # YouTube Data API v3 uploader
│   └── utils.py            # Caching, logging, config loader
├── assets/
│   ├── music/              # Put royalty-free background MP3/WAV tracks here
│   └── fonts/              # Custom caption fonts (optional)
├── cache/                  # Downloaded stock clips, audio, and timestamp caches
└── output/                 # Final rendered 1080x1920 MP4 Shorts
```

---

## ⚖️ License & Content Safety
- All stock media accessed via Pexels & Pixabay are subject to their respective free license terms.
- Generated YouTube uploads default strictly to **private** for review before public publishing.

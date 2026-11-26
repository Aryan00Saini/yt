import streamlit as st
from pathlib import Path

from src.utils import CACHE_DIR
from src.pipeline import run_pipeline

st.set_page_config(page_title="AI Shorts Maker", page_icon="🎬", layout="wide")

st.title("🎬 AI Shorts Maker (CPU-First)")
st.markdown(
    "Automate full vertical 9:16 YouTube Shorts with AI scripts, voiceover, "
    "stock clips, animated images, and burned-in dynamic captions."
)

# ── Sidebar controls ─────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")

    # Whisper is now only a fallback; keep the selector for power users
    whisper_model = st.selectbox("Whisper Fallback Model", ["tiny", "base", "small"], index=1)

    duration = st.slider("Target Duration (seconds)", min_value=20, max_value=60, value=45, step=5)

    # en-US-AndrewNeural is the recommended default voice
    voice_name = st.selectbox(
        "Edge-TTS Voice",
        [
            "en-US-AndrewNeural",
            "en-US-AriaNeural",
            "en-US-ChristopherNeural",
            "en-US-JennyNeural",
            "en-US-GuyNeural",
            "en-GB-SoniaNeural",
        ],
    )

# ── Main area ────────────────────────────────────────────────────────────────
mode = st.radio("Script Mode", ["Auto-generate from Topic", "Use My Own Script"], horizontal=True)

topic = st.text_input(
    "Topic / Title:" if mode == "Use My Own Script" else "Enter Topic or Question:",
    placeholder="e.g. 5 facts about black holes",
)

custom_script = ""
if mode == "Use My Own Script":
    custom_script = st.text_area(
        "Paste Your Script: plain narration, or a full brief with sections like "
        "[Hook, 0-5s], On-Screen Text, Music & SFX, Visual Suggestions and Sources:",
        height=320,
        placeholder=(
            "What if I told you a single teaspoon of a neutron star weighs "
            "as much as Mount Everest?\n\n..."
        ),
    )

uploaded_media = st.file_uploader(
    "Optional: Upload Reference Photos/Videos (leave empty to auto-generate visuals)",
    type=["jpg", "jpeg", "png", "webp", "mp4", "mov"],
    accept_multiple_files=True,
)

# ── Generate ─────────────────────────────────────────────────────────────────
if st.button("🚀 Generate Short", type="primary"):
    # Validation
    if mode == "Auto-generate from Topic" and not topic.strip():
        st.error("Please enter a valid topic.")
        st.stop()
    if mode == "Use My Own Script" and not custom_script.strip():
        st.error("Please paste your script.")
        st.stop()

    slug = (topic or custom_script[:40]).strip().lower().replace(" ", "_")[:40]

    # Save uploaded media to disk so pipeline can reference them by path
    media: list[Path] = []
    if uploaded_media:
        upload_dir = CACHE_DIR / "uploads" / slug
        upload_dir.mkdir(parents=True, exist_ok=True)
        for up_file in uploaded_media:
            dest = upload_dir / up_file.name
            dest.write_bytes(up_file.getbuffer())
            media.append(dest)

    status = st.status("🎬 Producing your YouTube Short...", expanded=True)

    try:
        out, script = run_pipeline(
            topic=topic.strip() or None,
            custom_body=custom_script.strip() if mode == "Use My Own Script" else None,
            media_files=media or None,
            duration=duration,
            voice=voice_name,
            whisper_model=whisper_model,
            progress=status.write,
        )
    except Exception as e:
        status.update(label="❌ Failed", state="error")
        st.error(str(e))
        st.stop()

    status.update(label="✅ Short Completed!", state="complete", expanded=False)
    st.subheader(script.title)
    st.write(script.description)
    st.video(str(out))

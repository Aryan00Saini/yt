import streamlit as st
from pathlib import Path
from src.utils import load_config, slugify, OUTPUT_DIR, CACHE_DIR
from src.script_gen import generate_script, generate_script_from_custom_text
from src.tts import generate_voiceover, generate_scene_voiceovers
from src.captions import transcribe_with_whisper
from src.visuals import fetch_visual_for_keywords
from src.assemble import assemble_short
import moviepy.editor as mpy

st.set_page_config(page_title="AI Shorts Maker", page_icon="🎬", layout="wide")

st.title("🎬 AI Shorts Maker (CPU-First)")
st.markdown("Automate full vertical 9:16 YouTube Shorts with AI scripts, voiceover, stock clips, animated images, and burned-in dynamic captions.")

with st.sidebar:
    st.header("⚙️ Configuration")
    whisper_model = st.selectbox("Whisper Model Size", ["tiny", "base", "small"], index=1)
    duration = st.slider("Target Duration (seconds)", min_value=20, max_value=60, value=45, step=5)
    voice_name = st.selectbox(
        "Edge-TTS Voice",
        ["en-US-ChristopherNeural", "en-US-GuyNeural", "en-US-JennyNeural", "en-US-AriaNeural", "en-GB-SoniaNeural"]
    )

mode = st.radio("Script Mode", ["Auto-generate from Topic", "Use My Own Script"], horizontal=True)
topic = st.text_input(
    "Topic / Title:" if mode == "Use My Own Script" else "Enter Topic or Question:",
    placeholder="e.g. 5 facts about black holes"
)
custom_script = ""
if mode == "Use My Own Script":
    custom_script = st.text_area(
        "Paste Your Script (separate scenes with blank lines, or paste a full paragraph):",
        height=180,
        placeholder="What if I told you a single teaspoon of a neutron star weighs as much as Mount Everest?\n\n..."
    )

uploaded_media = st.file_uploader(
    "Optional: Upload Reference Photos/Videos (leave empty to auto-generate visuals)",
    type=["jpg", "jpeg", "png", "webp", "mp4", "mov"],
    accept_multiple_files=True
)

if st.button("🚀 Generate Short", type="primary"):
    if mode == "Auto-generate from Topic" and not topic.strip():
        st.error("Please enter a valid topic.")
    elif mode == "Use My Own Script" and not custom_script.strip():
        st.error("Please paste your script.")
    else:
        status = st.status("🎬 Producing your YouTube Short...", expanded=True)
        config = load_config()
        config["voice"]["name"] = voice_name
        slug = slugify(topic or custom_script[:40])

        # 1. Script
        status.write("📝 **Step 1/5: Preparing Script...**")
        if mode == "Use My Own Script":
            script = generate_script_from_custom_text(custom_script, title=topic.strip() or None)
        else:
            script = generate_script(topic, duration=duration)
        st.subheader(f"Title: {script.title}")
        st.write(script.description)

        # 2. Voiceover
        status.write("🎙️ **Step 2/5: Synthesizing Voiceover (Edge-TTS)...**")
        full_audio = CACHE_DIR / f"{slug}_voiceover.mp3"
        generate_voiceover(script, full_audio, voice=voice_name)
        
        scene_audios = generate_scene_voiceovers(script, voice=voice_name)
        scene_durations = []
        for sc_path, _ in scene_audios:
            aud = mpy.AudioFileClip(str(sc_path))
            scene_durations.append(aud.duration)
            aud.close()

        # 3. Whisper Captions
        status.write("🎯 **Step 3/5: Generating Word-Level Timestamps...**")
        timestamps = transcribe_with_whisper(full_audio, model_size=whisper_model, compute_type="int8")

        # 4. Visuals
        status.write("🎨 **Step 4/5: Fetching Visuals for each scene...**")
        custom_media_paths = []
        if uploaded_media:
            upload_dir = CACHE_DIR / "uploads" / slug
            upload_dir.mkdir(parents=True, exist_ok=True)
            for up_file in uploaded_media:
                dest = upload_dir / up_file.name
                dest.write_bytes(up_file.getbuffer())
                custom_media_paths.append(dest)

        scene_visuals = []
        for idx, s in enumerate(script.scenes):
            if idx < len(custom_media_paths):
                scene_visuals.append(custom_media_paths[idx])
            else:
                vis = fetch_visual_for_keywords(s.visual_keywords)
                scene_visuals.append(vis)

        # 5. Assemble
        status.write("🎞️ **Step 5/5: Assembling 9:16 Video + Music + Captions...**")
        output_path = OUTPUT_DIR / f"{slug}.mp4"
        assemble_short(
            scene_visuals=scene_visuals,
            scene_durations=scene_durations,
            voiceover_path=full_audio,
            word_timestamps=timestamps,
            output_path=output_path,
            config=config
        )

        status.update(label="✅ Short Completed!", state="complete", expanded=False)
        st.success(f"Video created: {output_path.name}")
        st.video(str(output_path))

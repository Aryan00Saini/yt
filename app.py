from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st

from src.pacing import MIN_SECONDS, MAX_SECONDS
from src.production import looks_like_production_script, parse_production_script
from src.utils import BASE_DIR, CACHE_DIR, MUSIC_DIR


st.set_page_config(page_title="Shorts Studio", page_icon="🎞️", layout="wide")

EXAMPLES = {"History · The Dancing Plague": "dancing_plague.txt",
            "Biography · The Titanic baker": "titanic_baker.md"}
VOICES = {"Andrew · US": "en-US-AndrewNeural", "Aria · US": "en-US-AriaNeural",
          "Christopher · US": "en-US-ChristopherNeural", "Jenny · US": "en-US-JennyNeural",
          "Guy · US": "en-US-GuyNeural", "Sonia · UK": "en-GB-SoniaNeural"}


def load_example():
    choice = st.session_state.example
    if choice in EXAMPLES:
        st.session_state.brief = (BASE_DIR / "examples" / EXAMPLES[choice]).read_text(encoding="utf-8")
        st.session_state.pop("import_problem", None)


def import_brief():
    upload = st.session_state.brief_upload
    if upload is None:
        return
    if upload.size > 256_000:
        st.session_state.import_problem = "Choose a text brief smaller than 256 KB."
        return
    try:
        st.session_state.brief = upload.getvalue().decode("utf-8-sig")
        st.session_state.pop("import_problem", None)
    except UnicodeDecodeError:
        st.session_state.import_problem = "Save the brief as UTF-8 and try again."


def keep_brief():
    st.session_state.saved_brief = st.session_state.brief


def save_upload(folder, uploaded, name):
    dest = Path(folder) / (name + Path(uploaded.name).suffix.lower())
    dest.write_bytes(uploaded.getbuffer())
    return dest


with st.sidebar:
    st.caption("SHORTS STUDIO   /   EDIT SUITE")
    st.header("Cut settings")
    pacing = st.radio("Voice pacing", ["Fit to 40–60 seconds", "Natural duration"],
                      help="Fit adjusts the actual recording without cutting words or shifting pitch.")
    fit = pacing.startswith("Fit")
    duration = st.slider("Preferred length (seconds)", MIN_SECONDS, MAX_SECONDS, 50,
                         help="Also sets the writing target for topic mode. Natural duration keeps the recording as is.")
    quality = st.radio("Export quality", ["Draft · 540 × 960", "Full HD · 1080 × 1920"])
    voice = st.selectbox("Narrator", list(VOICES))
    with st.expander("Transcription fallback"):
        whisper = st.selectbox("Whisper model", ["tiny", "base", "small"], index=1)
        st.caption("Only used if the narrator returns no usable word timings.")
    st.divider()
    st.caption("9:16 portrait   ·   H.264 MP4\n\nRenders locally. Nothing is uploaded to YouTube.")

st.markdown("""
<div style="background:linear-gradient(115deg,#101b2c 50%,#203d44);color:#f7f8f2;
     border-radius:14px;padding:clamp(1.5rem,4vw,3.4rem);border-left:5px solid #79dfcc">
 <div style="color:#9de8d5;font:600 .75rem monospace;letter-spacing:.18em">POSTPRODUCTION &nbsp;/&nbsp; 9:16</div>
 <h1 style="color:#fff;font-size:clamp(2.6rem,5vw,4.6rem);line-height:1.1;padding:.5rem 0">Shorts Studio</h1>
 <div style="font-size:1.08rem;color:#d7e5e9">Bring the script. Check the cut. Export a finished Short.</div>
</div>
""", unsafe_allow_html=True)
st.write("")

editor, inspector = st.columns([1.55, 1], gap="large")
with editor:
    st.caption("01   /   INPUT")
    st.subheader("Write your brief")
    mode = st.radio("Starting point", ["Script or full brief", "Generate from topic"], horizontal=True)
    topic, body, narration, plan = "", "", "", None
    if mode == "Script or full brief":
        with st.expander("Import text or load an example"):
            st.file_uploader("Import a UTF-8 brief", type=["txt", "md"], key="brief_upload", on_change=import_brief)
            st.selectbox("Replace editor with an example", ["Choose an example", *EXAMPLES],
                         key="example", on_change=load_example)
            if st.session_state.get("import_problem"):
                st.error(st.session_state.import_problem)
        st.session_state.setdefault("brief", st.session_state.get("saved_brief", ""))
        body = st.text_area("Script editor", key="brief", height=325, on_change=keep_brief,
                            placeholder="Video Title: …\n[Hook, 0-5s]\nWhat should the viewer hear first?\n\n"
                                        "[Story, 5-45s]\n…\n\nOn-Screen Text:\n- … (0-3s)\nMusic:\nVisuals:\nSources:").strip()
        st.session_state.saved_brief = body
        plan = parse_production_script(body) if looks_like_production_script(body) else None
        narration = plan.narration if plan is not None else body
    else:
        topic = st.text_input("Topic or question", placeholder="Why do stars twinkle but planets usually don't?").strip()
        st.caption("Topic writing needs a Gemini or Groq API key. With your own script, neither key is required.")

    with st.expander("Media & soundtrack"):
        references = st.file_uploader("Scene visuals, in order", type=["jpg", "jpeg", "png", "webp", "mp4", "mov"],
                                      accept_multiple_files=True)
        soundtrack = st.file_uploader("Use your own music", type=["mp3", "wav", "m4a", "ogg"])
        st.caption("Use media you have rights to. A no-music direction in the brief overrides the soundtrack.")

with inspector:
    st.caption("02   /   PREFLIGHT")
    st.subheader("What will be rendered?")
    with st.container(border=True):
        if body:
            wc = len(narration.split())
            estimated = round(wc / 2.5)
            st.caption("PRODUCTION BRIEF" if plan else "VERBATIM SCRIPT")
            if plan and plan.title:
                st.text(plan.title)
            left, right = st.columns(2)
            left.metric("Spoken words", wc)
            right.metric("Est. speech", f"{estimated}s")
            if not wc:
                st.error("No spoken lines yet. Add narration under Script or a timed section.")
            elif fit and not MIN_SECONDS <= estimated <= MAX_SECONDS:
                st.caption("Outside the target on paper. Final pace is based on the measured recording, not this estimate.")
            if plan:
                st.caption(f"{len(plan.sections)} sections  ·  {len(plan.overlays)} overlays  ·  {len(plan.sfx)} sound cues")
            with st.expander("Narration only"):
                st.text(narration or "No narration detected.")
            if plan:
                with st.expander("Edit instructions"):
                    for section in plan.sections:
                        st.text(f"{section.est_start:g}–{section.est_end:g}s  ·  {section.name}")
                    for label, values in [("Overlays", [o.text for o in plan.overlays]),
                                          ("SFX", [s.sound for s in plan.sfx]),
                                          ("Visuals", plan.visuals.suggestions),
                                          ("Style", [plan.visuals.style]), ("Map", [plan.visuals.map_place]),
                                          ("Music", ["Muted" if plan.music.muted else plan.music.description]),
                                          ("Sources", plan.sources)]:
                        if any(values):
                            st.caption(label)
                            st.text("\n".join(filter(None, values)))
            if plan and plan.music.description and not plan.music.muted and not soundtrack and not any(
                    p.suffix.lower() in (".mp3", ".wav", ".ogg", ".m4a") for p in MUSIC_DIR.rglob("*")):
                st.info("No music track available. Add a soundtrack under Media or the cut will use only voice and SFX.")
        elif topic:
            st.caption("GENERATED SCRIPT")
            st.write(f"The writer will aim for {duration}s. Scenes and captions are generated after you start.")
        else:
            st.markdown("**Your cut sheet appears here.**")
            st.write("Paste a brief to inspect narration, overlay text, sound cues and sources before rendering.")

    go = st.button("Render Short", type="primary", use_container_width=True, disabled=not (topic or narration))

if go:
    from src.pipeline import run_pipeline

    status = st.status("Producing your Short…", expanded=True)
    try:
        with TemporaryDirectory(dir=CACHE_DIR, ignore_cleanup_errors=True) as folder:
            media = [save_upload(folder, f, f"scene_{i:03}") for i, f in enumerate(references or [])]
            track = save_upload(folder, soundtrack, "music") if soundtrack else None
            out, script = run_pipeline(topic=topic or None, custom_body=body or None, media_files=media,
                                       music_file=track, duration=duration, fit_duration=fit, voice=VOICES[voice],
                                       whisper_model=whisper, draft=quality.startswith("Draft"), progress=status.text)
        st.session_state.last_cut = (out, script.title)
        status.update(label="Export complete", state="complete", expanded=False)
    except Exception as exc:
        status.update(label="Render stopped", state="error")
        st.error(str(exc))

if "last_cut" in st.session_state:
    out, title = st.session_state.last_cut
    st.divider()
    st.caption("03   /   LATEST EXPORT")
    st.subheader(title)
    if out.is_file():
        player, downloads = st.columns([1, 1.65], gap="large")
        player.video(str(out))
        with downloads:
            st.markdown("**Export your cut**")
            for ext, label, mime in [(".mp4", "Download MP4", "video/mp4"),
                                     (".srt", "Download captions", "application/x-subrip"),
                                     (".txt", "Download title, description & tags", "text/plain")]:
                asset = out.with_suffix(ext)
                if asset.is_file():
                    with asset.open("rb") as data:
                        st.download_button(label, data, file_name=asset.name, mime=mime)
    else:
        st.info("This export is no longer on disk. Render again to create a new copy.")

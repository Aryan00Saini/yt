import sys
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console

from src.utils import logger, load_config, slugify, OUTPUT_DIR, CACHE_DIR
from src.script_gen import generate_script, generate_script_from_custom_text
from src.tts import generate_voiceover, generate_scene_voiceovers
from src.captions import transcribe_with_whisper
from src.visuals import fetch_visual_for_keywords
from src.assemble import assemble_short
from src.upload import upload_short_to_youtube

app = typer.Typer(help="AI Shorts Maker: Fully automated CPU-first YouTube Shorts generator.")
console = Console()

@app.command()
def create(
    topic: Optional[str] = typer.Option(None, "--topic", "-t", help="Topic or idea for the Short (e.g. '5 facts about black holes')"),
    script_text: Optional[str] = typer.Option(None, "--script", "-s", help="Custom script text to follow word-for-word"),
    script_file: Optional[Path] = typer.Option(None, "--script-file", "-f", help="Path to a .txt file containing your custom script"),
    media_dir: Optional[Path] = typer.Option(None, "--media-dir", "-m", help="Folder of reference photos/videos to use for scenes (sorted by filename)"),
    duration: int = typer.Option(45, "--duration", "-d", help="Target duration in seconds (30-60)"),
    upload: bool = typer.Option(False, "--upload", "-u", help="Automatically upload to YouTube as private after rendering"),
    whisper_model: str = typer.Option("base", "--whisper", "-w", help="Whisper model size (tiny, base, small)")
):
    """
    Generate an entire vertical 9:16 YouTube Short from a topic prompt or your own custom script.
    """
    custom_body: Optional[str] = None
    if script_file:
        if not script_file.exists():
            console.print(f"[bold red]Script file not found:[/bold red] {script_file}")
            raise typer.Exit(code=1)
        custom_body = script_file.read_text(encoding="utf-8").strip()
    elif script_text:
        custom_body = script_text.strip()

    if not topic and not custom_body:
        console.print("[bold red]Please provide either --topic, --script, or --script-file.[/bold red]")
        raise typer.Exit(code=1)

    display_label = topic or (script_file.stem if script_file else custom_body[:40])
    console.print(f"\n[bold green]=== Starting AI Shorts Generation ===[/bold green]")
    console.print(f"[bold cyan]Input:[/bold cyan] {display_label}")
    
    config = load_config()
    slug = slugify(display_label)
    
    # 1. Script Generation / Parsing
    console.print("\n[bold yellow]Step 1/5: Preparing Script...[/bold yellow]")
    if custom_body:
        script = generate_script_from_custom_text(custom_body, title=topic)
    else:
        script = generate_script(topic, duration=duration)
    console.print(f"[bold green]Title:[/bold green] {script.title}")
    console.print(f"[bold cyan]Scenes:[/bold cyan] {len(script.scenes)} scenes planned.")

    # 2. Voiceover Synthesis
    console.print("\n[bold yellow]Step 2/5: Synthesizing Voiceover (Edge-TTS)...[/bold yellow]")
    voice_name = config.get("voice", {}).get("name", "en-US-ChristopherNeural")
    full_audio_path = CACHE_DIR / f"{slug}_voiceover.mp3"
    generate_voiceover(script, full_audio_path, voice=voice_name)
    
    # Measure per-scene timing
    scene_audios = generate_scene_voiceovers(script, voice=voice_name)
    
    # Calculate scene durations from per-scene audio files
    import moviepy.editor as mpy
    scene_durations = []
    for sc_path, _ in scene_audios:
        aud = mpy.AudioFileClip(str(sc_path))
        scene_durations.append(aud.duration)
        aud.close()

    # 3. Word-level Timestamps (faster-whisper)
    console.print("\n[bold yellow]Step 3/5: Generating Word Timestamps (faster-whisper)...[/bold yellow]")
    timestamps = transcribe_with_whisper(
        full_audio_path,
        model_size=whisper_model,
        compute_type=config.get("captions", {}).get("compute_type", "int8")
    )

    # 4. Visuals per scene
    console.print("\n[bold yellow]Step 4/5: Fetching Visuals for each scene...[/bold yellow]")
    custom_media = []
    if media_dir and media_dir.exists():
        allowed_exts = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}
        custom_media = sorted([p for p in media_dir.iterdir() if p.is_file() and p.suffix.lower() in allowed_exts])
        console.print(f"  Found {len(custom_media)} custom media files in {media_dir}")

    scene_visuals = []
    for idx, scene in enumerate(script.scenes):
        if idx < len(custom_media):
            vis_file = custom_media[idx]
            console.print(f"  Using your media for Scene {idx+1}/{len(script.scenes)}: {vis_file.name}")
        else:
            console.print(f"  Fetching visual for Scene {idx+1}/{len(script.scenes)}: {scene.visual_keywords}")
            vis_file = fetch_visual_for_keywords(scene.visual_keywords)
        scene_visuals.append(vis_file)

    # 5. Assembly & Export
    console.print("\n[bold yellow]Step 5/5: Assembling Video with Captions, Visuals, and Music...[/bold yellow]")
    final_output_file = OUTPUT_DIR / f"{slug}.mp4"
    assemble_short(
        scene_visuals=scene_visuals,
        scene_durations=scene_durations,
        voiceover_path=full_audio_path,
        word_timestamps=timestamps,
        output_path=final_output_file,
        config=config
    )

    console.print(f"\n[bold green]Short Successfully Generated![/bold green]")
    console.print(f"[bold cyan]File Saved to:[/bold cyan] {final_output_file.resolve()}\n")

    # Optional Upload
    if upload:
        console.print("[bold yellow]Uploading to YouTube...[/bold yellow]")
        try:
            video_id = upload_short_to_youtube(
                video_path=final_output_file,
                title=script.title,
                description=script.description,
                tags=script.tags,
                privacy_status="private"
            )
            console.print(f"[bold green]Uploaded successfully! ID: {video_id}[/bold green]")
        except Exception as e:
            console.print(f"[bold red]YouTube upload failed: {e}[/bold red]")

if __name__ == "__main__":
    app()

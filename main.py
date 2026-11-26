from pathlib import Path
from typing import Optional
import typer
from rich.console import Console

from src.utils import logger
from src.pipeline import run_pipeline
from src.upload import upload_short_to_youtube

app = typer.Typer(help="AI Shorts Maker: Fully automated CPU-first YouTube Shorts generator.")
console = Console()


@app.command()
def create(
    topic: Optional[str] = typer.Option(None, "--topic", "-t", help="Topic or idea for the Short (e.g. '5 facts about black holes')"),
    niche: Optional[str] = typer.Option(None, "--niche", "-n", help="Channel niche (e.g. 'science facts', 'history')"),
    script_text: Optional[str] = typer.Option(None, "--script", "-s", help="Custom script text to follow word-for-word"),
    script_file: Optional[Path] = typer.Option(None, "--script-file", "-f", help="Path to a .txt file containing your custom script"),
    media_dir: Optional[Path] = typer.Option(None, "--media-dir", "-m", help="Folder of reference photos/videos to use for scenes (sorted by filename)"),
    duration: int = typer.Option(50, "--duration", "-d", min=1, help="Preferred length in seconds (40-60 in fit mode)"),
    natural_duration: bool = typer.Option(False, "--natural-duration", help="Keep the original voiceover length, even outside 40-60 seconds."),
    draft: bool = typer.Option(False, "--draft", help="Fast 540x960 ultrafast crf=28 render for quick testing"),
    upload: bool = typer.Option(False, "--upload", "-u", help="Automatically upload to YouTube as private after rendering"),
    whisper_model: str = typer.Option("base", "--whisper", "-w", help="Whisper fallback model size (tiny, base, small)"),
):
    """
    Generate an entire vertical 9:16 YouTube Short from a topic prompt or your own custom script.
    """
    # ── Resolve custom script body ──────────────────────────────────────────
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

    # ── Resolve optional user media files ───────────────────────────────────
    custom_media: list[Path] = []
    if media_dir and media_dir.exists():
        allowed_exts = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}
        custom_media = sorted(
            [p for p in media_dir.iterdir() if p.is_file() and p.suffix.lower() in allowed_exts]
        )
        console.print(f"  Found {len(custom_media)} custom media files in {media_dir}")

    display_label = topic or (script_file.stem if script_file else custom_body[:40])
    console.print("\n[bold green]=== Starting AI Shorts Generation ===[/bold green]")
    console.print(f"[bold cyan]Input:[/bold cyan] {display_label}")

    # ── Run shared pipeline ──────────────────────────────────────────────────
    try:
        out, script = run_pipeline(
            topic=topic,
            custom_body=custom_body,
            media_files=custom_media or None,
            duration=duration,
            fit_duration=not natural_duration,
            niche=niche,
            whisper_model=whisper_model,
            draft=draft,
            progress=lambda m: console.print(f"[bold yellow]{m}[/bold yellow]"),
        )
    except Exception as e:
        console.print(f"[bold red]Generation failed:[/bold red] {e}")
        raise typer.Exit(code=1)

    console.print(f"\n[bold green]Short Successfully Generated![/bold green]")
    console.print(f"[bold cyan]File Saved to:[/bold cyan] {out.resolve()}\n")

    # ── Optional YouTube upload ──────────────────────────────────────────────
    if upload:
        console.print("[bold yellow]Uploading to YouTube...[/bold yellow]")
        try:
            video_id = upload_short_to_youtube(
                video_path=out,
                title=script.title,
                description=script.description,
                tags=script.tags,
                privacy_status="private",
            )
            console.print(f"[bold green]Uploaded successfully! ID: {video_id}[/bold green]")
        except Exception as e:
            console.print(f"[bold red]YouTube upload failed: {e}[/bold red]")


if __name__ == "__main__":
    app()

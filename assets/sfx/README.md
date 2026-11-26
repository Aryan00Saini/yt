# assets/sfx/

Drop royalty-free WAV files here. The pipeline looks for these exact names:

| File | When it plays | Suggested volume in config |
|------|--------------|---------------------------|
| `hit.wav` | t = 0 (hook impact, scene 1 only) | `sfx_volume: 0.3` × 1.6 |
| `whoosh.wav` | 0.1 s before every Nth scene cut | `sfx_volume: 0.3` |
| `pop.wav` | (optional, not used by default) | — |

## Production briefs

Briefs can ask for any of these sounds by name. If `assets/sfx/<name>.wav` (or `.mp3`) exists it is
used, otherwise a simple built-in version is generated into `cache/sfx_synth/`:

`heartbeat`, `scratch` (record scratch), `stomp`, `drum`, `crowd`, `whoosh`, `hit`, `riser`, `ding`,
`pop`, `wind`, `thunder`, `camera`, `typing`.

## Free sources (check each site's specific license)

- **Freesound.org** – search "whoosh" or "hit impact". Filter by CC0 or CC-BY.
- **Pixabay Sound Effects** – pixabay.com/sound-effects/ — all free for commercial use.
- **Mixkit** – mixkit.co/free-sound-effects/ — free license, no attribution required.
- **ZapSplat** – zapsplat.com (free tier with attribution).

## Tips

- Keep files short: whoosh < 0.5 s, hit < 0.3 s.
- Normalize to -12 dBFS before dropping in; `sfx_volume` in config then fine-tunes the mix.
- Test with `python main.py --topic "test" --draft` before a full render.

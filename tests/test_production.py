from pathlib import Path

from src.production import (
    looks_like_production_script,
    parse_production_script,
    resolve_timing,
    split_into_scenes,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _plan(name):
    return parse_production_script((EXAMPLES / name).read_text(encoding="utf-8"))


def test_plain_narration_is_not_a_brief():
    assert not looks_like_production_script("Octopuses have three hearts. Two pump blood to the gills.")


def test_dancing_plague_brief():
    text = (EXAMPLES / "dancing_plague.txt").read_text(encoding="utf-8")
    assert looks_like_production_script(text)
    plan = parse_production_script(text)
    assert plan.title.startswith("In 1518, Hundreds Danced")
    assert [s.name for s in plan.sections] == ["Hook", "Main", "Twist", "Ending"]
    assert [(s.est_start, s.est_end) for s in plan.sections] == [(0, 5), (5, 40), (40, 50), (50, 60)]
    narration = plan.narration
    for not_spoken in ("On-Screen Text", "Fact-Check", "Hondius", "Sources", "record-scratch", "[Hook"):
        assert not_spoken not in narration
    assert narration.startswith("In 1518, hundreds of people couldn't stop dancing.")
    assert len(plan.overlays) == 9
    assert plan.overlays[5].text == "DOCTORS: 'HOT BLOOD' 🩸"
    assert (plan.overlays[6].est_start, plan.overlays[6].est_end) == (41, 46)
    assert "eerie" in plan.music.moods and plan.music.build and plan.music.silence_before == ["twist"]
    sfx = {c.sound: c for c in plan.sfx}
    assert sfx["scratch"].phrase == "More dancing" and sfx["scratch"].stop_music
    assert sfx["heartbeat"].anchor == "last_question"
    assert sfx["stomp"].mode == "bed"
    assert plan.visuals.illustrative and plan.visuals.look == "sepia"
    assert plan.visuals.map_place == "Strasbourg"
    assert "Hondius" in plan.visuals.references[0]
    assert len(plan.sources) == 4 and "Sources:" in plan.description()


def test_different_layout_markdown_brief():
    plan = _plan("titanic_baker.md")
    assert plan.title == "He Survived the Titanic Drunk 🥃🚢"
    assert [s.name for s in plan.sections] == ["Hook", "Story", "Twist", "Outro"]
    assert [o.text for o in plan.overlays][2] == "-2°C WATER ❄️"
    assert plan.music.silence_before == ["twist"] and "sad" in plan.music.moods
    assert {c.sound for c in plan.sfx} >= {"whoosh", "ding"}
    assert plan.visuals.look == "mono" and plan.visuals.map_place == "North Atlantic"
    assert "Encyclopedia Titanica" in plan.description()


def test_scenes_keep_every_spoken_word():
    plan = _plan("dancing_plague.txt")
    scenes = split_into_scenes(plan)
    assert " ".join(t for _, t in scenes).split() == plan.narration.split()
    assert all(len(t.split()) <= 25 for _, t in scenes)


def test_timing_follows_actual_speech():
    plan = _plan("dancing_plague.txt")
    scenes = split_into_scenes(plan)
    plan.scene_sections = [si for si, _ in scenes]
    # Fake TTS: every word takes 0.4 s, so the real timeline is ~25% slower than the brief's estimates.
    words, t = [], 0.0
    durations = []
    for _, text in scenes:
        start = t
        for w in text.split():
            words.append({"word": w, "start": t, "end": t + 0.35})
            t += 0.4
        durations.append(t - start)
    resolve_timing(plan, durations, words)

    def said(word):
        return next(w["start"] for w in words if w["word"].strip(".,?").lower() == word)

    cure = next(o for o in plan.overlays if o.text.startswith("THE CURE"))
    assert abs(cure.start - said("cure")) < 0.2
    scratch = next(c for c in plan.sfx if c.sound == "scratch")
    more = next(w["start"] for i, w in enumerate(words) if w["word"] == "More" and words[i + 1]["word"].startswith("dancing"))
    assert abs(scratch.times[0][0] - more) < 0.1
    heartbeat = next(c for c in plan.sfx if c.sound == "heartbeat")
    assert said("could") <= heartbeat.times[0][0] <= said("tell")
    fear = next(o for o in plan.overlays if o.text.startswith("CAN FEAR"))
    assert abs(fear.start - said("fear")) < 0.2
    twist = plan.sections[2]
    assert any(a <= twist.act_start <= b + 0.01 for a, b in plan.music.silences)


def test_review_edge_cases():
    from src.production import _sentences

    plain = "Octopuses have three hearts.\n[pause]\nTwo of them pump blood to the gills."
    assert not looks_like_production_script(plain)

    single = "Title: Tiny brief\nHook (0-5s): Water boils at 100 degrees.\nSources\n- Some book"
    assert looks_like_production_script(single)
    plan = parse_production_script(single)
    assert plan.narration == "Water boils at 100 degrees." and plan.sources == ["Some book"]

    scenes = parse_production_script("[Hook, 0-3s] Hi there.\n[Main, 3-9s] Intro line.\nScene 2: Now for the story.")
    assert "Scene 2" not in scenes.narration and scenes.sections[-1].text == "Now for the story."

    music = parse_production_script("[Hook, 0-3s] Hi.\n[Main, 3-9s] Go.\nMusic\n- Eerie medieval folk that gets faster; silence before the twist")
    assert "eerie" in music.music.moods and music.music.build and music.music.silence_before == ["twist"]

    assert _sentences("It was 8.5 degrees. Cold.") == ["It was 8.5 degrees.", "Cold."]


def test_review_edge_cases_round2():
    from src.production import _sentences

    titled = "Title: Fun fact\nOctopuses have three hearts. Two pump blood to the gills."
    assert looks_like_production_script(titled)

    one = "[Hook, 0-5s] Water boils at 100 degrees at sea level."
    assert looks_like_production_script(one)
    assert parse_production_script(one).narration == "Water boils at 100 degrees at sea level."

    cold = parse_production_script("[Cold Open]\nNobody saw it coming.\n[Main, 4-30s] Then it happened.")
    assert cold.narration.split() == "Nobody saw it coming. Then it happened.".split()

    hyph = parse_production_script("[Hook, 0-3s] Hi.\n[Main, 3-9s] Go.\nStory-driven games sold well.")
    assert "Story-driven games sold well." in hyph.narration

    snd = parse_production_script("[Hook, 0-3s] Hi.\n[Main, 3-9s] Go.\nSound\n- Eerie folk track that builds\n- Whoosh at the start")
    assert "eerie" in snd.music.moods and snd.music.build and any(c.sound == "whoosh" for c in snd.sfx)

    assert _sentences("It was 8.5 degrees") == ["It was 8.5 degrees"]


def test_review_edge_cases_round3():
    from src.production import _sentences

    assert not looks_like_production_script("World War II (1939–1945) changed the world.")

    md = parse_production_script("[Hook, 0-3s] Hi.\n[Main, 3-9s] Go.\nSources\n[Britannica](https://britannica.com/x)\n- Some book")
    assert "britannica" not in md.narration.lower() and len(md.sources) == 2

    titled = "Title: Fun fact\nOctopuses have three hearts. Two pump blood to the gills."
    assert looks_like_production_script(titled)
    assert parse_production_script(titled).narration == titled.splitlines()[1]

    compact = parse_production_script("[Hook, 0-3s] Hi.\n[Main, 3-9s] Go.\nScene 3:Survivors were rescued.")
    assert "Scene 3" not in compact.narration

    mus = parse_production_script("[Hook, 0-3s] Hi.\n[Main, 3-9s] Go.\nMusic (background audio)\n- Soft piano that swells")
    assert mus.music.build and mus.music.description

    assert _sentences("When did it sink?1912 was the year.") == ["When did it sink?", "1912 was the year."]
    assert _sentences("It was 8.5 degrees") == ["It was 8.5 degrees"]

    cue = parse_production_script("[Cold Open] (pause) Nobody saw it coming.\n[Main, 4-30s] Then it happened.")
    assert "Nobody saw it coming." in cue.narration

    nomusic = parse_production_script("[Hook, 0-3s] Hi.\n[Main, 3-9s] Go.\nAudio (no music)\n- Whoosh at the start")
    assert any(c.sound == "whoosh" for c in nomusic.sfx)

    rel = parse_production_script("[Hook, 0-3s] Hi.\n[Main, 3-9s] Go.\nSources\n[Story](./archive/story.md)\n- Some book")
    assert "archive" not in rel.narration and len(rel.sources) == 2

    piano = parse_production_script("[Hook, 0-3s] Hi.\n[Main, 3-9s] Go.\nAudio (piano music)\n- Soft piano that swells")
    assert piano.music.build and piano.music.description

import wave

import numpy as np
import pytest

from src.captions import subtitles_srt
from src.pacing import choose_tempo, fit_voiceover, wav_seconds


@pytest.mark.parametrize("length,target", [(35, 50), (45, 40), (52, 50), (70, 60)])
def test_pace_is_natural_and_inside_short_window(length, target):
    tempo = choose_tempo(length, target)
    assert 0.85 <= tempo <= 1.2
    assert 40 < length / tempo < 60


@pytest.mark.parametrize("length", [0, 20, 80, 200])
def test_unfit_recording_gets_edit_guidance(length):
    with pytest.raises(ValueError, match="narration|spoken detail"):
        choose_tempo(length, 50)


def test_real_audio_and_timeline_are_fitted_together(tmp_path):
    voice = tmp_path / "voice.wav"
    with wave.open(str(voice), "wb") as out:
        out.setparams((1, 2, 8000, 0, "NONE", ""))
        samples = np.arange(64 * 8000) * (2 * np.pi * 220 / 8000)
        out.writeframes((5000 * np.sin(samples)).astype("<i2").tobytes())
    fitted, words, scenes = fit_voiceover(
        voice, [{"word": "Next", "start": 32, "end": 32.5}], [32, 32], 55)
    assert wav_seconds(fitted) == pytest.approx(55, abs=0.1)
    assert sum(scenes) == pytest.approx(wav_seconds(fitted))
    assert words[0]["start"] == scenes[0]
    assert "00:00:27," in subtitles_srt(words)

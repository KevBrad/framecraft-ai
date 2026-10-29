"""Every exposed caption preset is burned into real media, including karaoke."""
import copy
import json
from pathlib import Path
import pytest
from backend.config import configure
from backend import pipeline

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('preset', ['minimal', 'creator', 'karaoke', 'boxed', 'podcast'])
def test_caption_preset_changes_export_pixels(preset):
    import cv2
    source = ROOT / 'tests' / 'artifacts' / 'jfk-source.mp4'
    if not source.exists():
        pytest.skip('Run scripts/prepare_fixture.py first.')
    directory = ROOT / 'tests' / 'artifacts' / 'style-checks'
    directory.mkdir(exist_ok=True)
    caption = {'id': 'line', 'start': 0, 'end': 2, 'text': 'A real caption',
               'words': [{'word': ' A', 'start': 0, 'end': .5}, {'word': ' real', 'start': .5, 'end': 1.1}, {'word': ' caption', 'start': 1.1, 'end': 2}]}
    edit = pipeline.default_edit('Caption style check', 0, 2, [caption])
    edit.update({'caption_preset': preset, 'layout': 'fit', 'aspect_ratio': '16:9', 'font_family': 'Liberation Sans'})
    output = directory / f'{preset}.mp4'
    metadata = pipeline.render(source, output, edit)
    assert (metadata['width'], metadata['height']) == (1280, 720)
    baseline = directory / 'baseline.mp4'
    if not baseline.exists():
        plain = copy.deepcopy(edit)
        plain['captions_enabled'] = False
        pipeline.render(source, baseline, plain)
    def frame(path):
        cap = cv2.VideoCapture(str(path))
        cap.set(cv2.CAP_PROP_POS_MSEC, 1200)
        ok, pixels = cap.read()
        cap.release()
        assert ok
        return pixels[450:680].astype(float)
    assert abs(frame(output) - frame(baseline)).mean() > .4

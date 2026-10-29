"""Source timing, editable intent, injection safety and real caption pixels."""
import copy
from pathlib import Path
import subprocess

import pytest

from backend import caption_engine as captions
from backend.config import configure
from backend import pipeline


def sentence(text='This cost twenty million dollars.', offset=10):
    tokens = text.split()
    return {'start': offset, 'end': offset + len(tokens)*.4, 'text': text,
            'words': [{'word': (' ' if n else '') + token, 'start': offset+n*.4,
                       'end': offset+(n+1)*.4, 'probability': .9} for n, token in enumerate(tokens)]}


def edit(preset='creator-bold'):
    return {**pipeline.default_edit('Caption check', 0, 2, []), 'caption_preset': preset,
            'caption_y': .80, 'captions': [{'id': 'a', 'start': 0, 'end': 2,
              'text': 'A million dollars', 'words': [
                  {'word': 'A', 'start': .1, 'end': .4},
                  {'word': ' million', 'start': .5, 'end': 1.0},
                  {'word': ' dollars', 'start': 1.2, 'end': 1.8}],
              'keywords': ['million'], 'emoji': '💰', 'emoji_enabled': True}]}


def test_phrase_captions_preserve_all_real_words_and_confidence():
    source = sentence('We learned a valuable lesson. This cost twenty million dollars.')
    result = captions.make_captions([source], 10, 15)
    assert len(result) >= 3
    assert ''.join(c['text'].replace(' ', '') for c in result) == source['text'].replace(' ', '')
    for caption in result:
        assert len(caption['words']) <= 4
        assert caption['end'] - caption['start'] <= 2.4
        assert caption['text'] == ''.join(w['word'] for w in caption['words'])
        assert all(w['probability'] == .9 for w in caption['words'])
        assert all(10 <= w['start'] < w['end'] <= 15 for w in caption['words'])


def test_caption_boundary_trims_to_real_selected_interval():
    result = captions.make_captions([sentence()], 10.15, 11.85)
    assert result[0]['start'] == 10.15
    assert result[-1]['end'] == 11.85


def test_pause_and_punctuation_split_before_reading_limit():
    source = sentence('One. Two three four')
    source['words'][2]['start'] += .7
    source['words'][2]['end'] += .7
    result = captions.make_captions([source], 10, 15)
    assert result[0]['text'] == 'One.'
    assert result[1]['text'] == 'Two'


def test_no_fake_word_alignment_for_unaligned_sentence():
    source = sentence()
    source['words'] = []
    result = captions.make_captions([source], 10, 15)
    assert result[0]['words'] == []
    assert result[0]['text'] == source['text']


@pytest.mark.parametrize('text,name', [
    ('when we recreated Squid Games.', 'Squid Games.'),
    ('we worked with Prime Video.', 'Prime Video.'),
    ('we filmed with Mr. Beast.', 'Mr. Beast.'),
    ('we released the Beast Games.', 'Beast Games.'),
    ('we visited the New York City Hall.', 'New York City Hall.'),
])
def test_short_phrase_limit_never_splits_bounded_adjacent_name_tokens(text, name):
    source = sentence(text)
    result = captions.make_captions([source], 10, 20)
    assert any(name in caption['text'] for caption in result)
    assert all(len(caption['words']) <= 6 and len(caption['text']) <= 42 for caption in result)
    assert ''.join(c['text'].replace(' ', '') for c in result) == text.replace(' ', '')


@pytest.mark.parametrize('text', ['we were actually dropping everything',
    'That experience was actually really interesting.', 'Something probably happened because everything changed.'])
def test_generic_long_words_never_gain_length_only_emphasis(text):
    keywords, emoji = captions.contextual_emphasis(text)
    assert keywords == []
    assert emoji == ''


@pytest.mark.parametrize('text,expected', [('we recreated Squid Games', 'Squid Games'),
    ('our partner was Prime Video', 'Prime Video'), ('I met Mr. Beast', 'Mr. Beast'),
    ('NASA sent the spacecraft', 'NASA'), ('it cost $25,000', '$25,000')])
def test_grounded_names_and_quantities_receive_emphasis(text, expected):
    keywords, _ = captions.contextual_emphasis(text)
    assert expected in keywords


@pytest.mark.parametrize('text,emoji', [('A million dollars', '💰'), ('An important lesson', '💡'),
    ('The house was on fire', '🔥'), ('I was surprised', '😮'), ('My target was clear', '🎯'),
    ('Sales doubled', '📈'), ('I love this', '❤️'), ('We celebrate today', '🎉'),
    ('It is not expensive', ''), ('The carpet costume works', ''), ('An ordinary sentence', '')])
def test_conservative_contextual_emoji(text, emoji):
    keywords, actual = captions.contextual_emphasis(text)
    assert actual == emoji
    assert len(keywords) <= 2
    assert all(keyword.lower() in text.lower() for keyword in keywords)


@pytest.mark.parametrize('frequency,expected', [('off', 0), ('subtle', 1), ('balanced', 2), ('frequent', 3)])
def test_emoji_frequency_bounds(frequency, expected):
    result = captions.make_captions([sentence('Million dollars.', value) for value in (0, 3, 6)],
                                    0, 10, emoji_frequency=frequency)
    assert sum(bool(c['emoji']) for c in result) == expected
    disabled = captions.make_captions([sentence()], 0, 15, emoji_enabled=False)
    assert not any(c['emoji'] for c in disabled)


def test_manual_text_override_never_renders_old_words(tmp_path):
    saved = edit()
    saved['captions'][0]['text'] = 'My corrected headline'
    path = tmp_path / 'captions.ass'
    captions.write_ass(path, saved, 720, 1280)
    script = path.read_text(encoding='utf-8-sig')
    assert 'My corrected headline' in script
    assert 'million' not in script
    assert '\\u1' not in script


def test_removed_replaced_and_disabled_emoji_are_authoritative(tmp_path):
    saved = edit()
    path = tmp_path / 'captions.ass'
    captions.write_ass(path, saved, 720, 1280)
    assert '\\p1' in path.read_text(encoding='utf-8-sig')
    for change in ({'emoji': ''}, {'emoji_enabled': False}):
        changed = copy.deepcopy(saved)
        changed['captions'][0].update(change)
        captions.write_ass(path, changed, 720, 1280)
        assert '\\p1' not in path.read_text(encoding='utf-8-sig')
    saved['contextual_emojis'] = False
    captions.write_ass(path, saved, 720, 1280)
    assert '\\p1' not in path.read_text(encoding='utf-8-sig')
    saved['contextual_emojis'] = True
    saved['captions'][0]['emoji'] = '💡'
    captions.write_ass(path, saved, 720, 1280)
    assert captions._GLYPHS['💡'][0][1] in path.read_text(encoding='utf-8-sig')
    assert captions._GLYPHS['💰'][0][1] not in path.read_text(encoding='utf-8-sig')


def test_absolute_word_timing_gap_and_clip_offset(tmp_path):
    saved = edit()
    saved['start'], saved['end'] = 12, 14
    for caption in saved['captions']:
        caption['start'] += 12
        caption['end'] += 12
        for word in caption['words']:
            word['start'] += 12
            word['end'] += 12
    path = tmp_path / 'captions.ass'
    captions.write_ass(path, saved, 720, 1280)
    dialogue = [line for line in path.read_text(encoding='utf-8-sig').splitlines() if line.startswith('Dialogue: 0')]
    assert any('0:00:00.50,0:00:01.00' in line and '\\u1' in line for line in dialogue)
    assert any('0:00:01.00,0:00:01.20' in line and '\\u1' not in line for line in dialogue)
    assert all('0:00:12' not in line for line in dialogue)


def test_animations_disable_and_keyword_whole_word(tmp_path):
    saved = edit()
    saved['animated_captions'] = False
    saved['captions'][0]['text'] = 'A costly costume'
    saved['captions'][0]['keywords'] = ['cost']
    path = tmp_path / 'captions.ass'
    captions.write_ass(path, saved, 720, 1280)
    script = path.read_text(encoding='utf-8-sig')
    assert '\\u1' not in script
    assert '\\fad' not in script
    assert '\\t(' not in script
    assert 'costly costume' in script


def test_ass_override_injection_and_unsupported_emoji_never_become_commands(tmp_path):
    saved = edit()
    saved['captions'][0].update(text=r'{\pos(0,0)} unsafe', emoji=r'{\clip(0,0,1,1)}')
    path = tmp_path / 'captions.ass'
    captions.write_ass(path, saved, 720, 1280)
    script = path.read_text(encoding='utf-8-sig')
    assert r'{\pos(0,0)}' not in script
    assert '\\clip' not in script
    assert '｛＼pos(0,0)｝' in script
    assert '\\p1' not in script


def test_overlapping_caption_bomb_is_bounded_and_never_published(tmp_path):
    saved = edit()
    saved['captions'] = [copy.deepcopy(saved['captions'][0]) for _ in range(3000)]
    destination = tmp_path / 'oversized.ass'
    with pytest.raises(pipeline.PipelineError, match='Too many overlapping caption effects'):
        captions.write_ass(destination, saved, 720, 1280)
    assert not destination.exists()


@pytest.mark.parametrize('intensity,expected', [('minimal', 2), ('balanced', 3), ('energetic', 4)])
def test_intensity_changes_generated_suggestion_density(intensity, expected):
    result = captions.make_captions([sentence('Million dollars.', value) for value in (0, 4, 5, 8, 10, 12)],
                                    0, 15, emoji_frequency='balanced', intensity=intensity)
    assert sum(bool(c['emoji']) for c in result) == expected


def test_intensity_changes_motion_without_changing_source_word_timing(tmp_path):
    path = tmp_path / 'intensity.ass'
    scripts = {}
    for intensity in ('minimal', 'balanced', 'energetic'):
        saved = edit()
        saved['intensity'] = intensity
        captions.write_ass(path, saved, 720, 1280)
        scripts[intensity] = path.read_text(encoding='utf-8-sig')
        assert '0:00:00.50,0:00:01.00' in scripts[intensity]
        assert '\\u1' in scripts[intensity]
    assert '\\fad' not in scripts['minimal'] and '\\t(' not in scripts['minimal']
    assert '\\fscx96' in scripts['balanced']
    assert '\\fscx92' in scripts['energetic']


def test_opening_hook_is_authoritative_bounded_escaped_and_optional(tmp_path):
    saved = edit()
    saved.update(title=r'Source quote {\pos(0,0)}', opening_hook=True, start=12, end=20,
                 captions_enabled=False)
    path = tmp_path / 'hook.ass'
    captions.write_ass(path, saved, 720, 1280)
    script = path.read_text(encoding='utf-8-sig')
    assert 'Dialogue: 1,0:00:00.00,0:00:02.50' in script
    assert 'Source quote ｛＼pos(0,0)｝' in script
    assert r'{\pos(0,0)}' not in script
    assert 'Dialogue: 0' not in script
    saved['opening_hook'] = False
    captions.write_ass(path, saved, 720, 1280)
    assert 'Source quote' not in path.read_text(encoding='utf-8-sig')


def test_opening_hook_title_has_real_pixels_in_separate_safe_top_region(tmp_path):
    import cv2
    import numpy as np
    saved = edit()
    saved.update(title='This is a source quote', font_size=30, opening_hook=True,
                 animated_captions=False)
    fonts = (Path(__file__).resolve().parents[1]/'assets'/'fonts').as_posix().replace(':', r'\:')
    frames = []
    for enabled in (True, False):
        saved['opening_hook'] = enabled
        captions.write_ass(tmp_path/'captions.ass', saved, 360, 640)
        output = tmp_path / f'hook-{enabled}.png'
        result = subprocess.run([pipeline._binary('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'color=c=0x102030:s=360x640:r=12:d=1',
            '-vf', f"ass=filename=captions.ass:fontsdir='{fonts}'", '-frames:v', '1', str(output)],
            cwd=tmp_path, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        frames.append(cv2.imread(str(output)))
    difference = abs(frames[0].astype(float)-frames[1].astype(float)).max(axis=2)
    assert np.count_nonzero(difference[38:200] > 50) > 400
    assert np.count_nonzero(difference[:38] > 50) == 0
    assert np.count_nonzero(difference[250:] > 50) == 0


@pytest.mark.parametrize('preset', list(captions.PRESETS))
def test_actual_ffmpeg_caption_and_vector_emoji_pixels(tmp_path, preset):
    """Real libass rendering, not a mock or a saved-script-only check."""
    import cv2
    import numpy as np
    folder = tmp_path / preset
    folder.mkdir()
    saved = edit(preset)
    saved.update(font_size=30, caption_y=.78)
    captions.write_ass(folder/'captions.ass', saved, 360, 640)
    output = folder/'render.mp4'
    fonts = (Path(__file__).resolve().parents[1]/'assets'/'fonts').as_posix().replace(':', r'\:')
    result = subprocess.run([pipeline._binary('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'color=c=0x102030:s=360x640:r=12:d=2',
        '-vf', f"ass=filename=captions.ass:fontsdir='{fonts}'", '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(output)],
        cwd=folder, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    cap = cv2.VideoCapture(str(output))
    cap.set(cv2.CAP_PROP_POS_MSEC, 750)
    ok, frame = cap.read()
    cap.release()
    assert ok
    # Text is bright in its safe caption zone; colored gold pictogram is above.
    assert np.count_nonzero(np.all(frame[420:540] > 170, axis=2)) > 100
    gold = (frame[:, :, 2] > 140) & (frame[:, :, 1] > 95) & (frame[:, :, 0] < 130)
    assert np.count_nonzero(gold[330:450]) > 120
    assert not np.any(gold[:38])
    # Removing the canonical emoji removes those actual gold pixels.
    saved['captions'][0]['emoji'] = ''
    captions.write_ass(folder/'captions.ass', saved, 360, 640)
    without = folder/'without.png'
    result = subprocess.run([pipeline._binary('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'color=c=0x102030:s=360x640:r=12:d=1',
        '-vf', f"ass=filename=captions.ass:fontsdir='{fonts}'", '-frames:v', '1', str(without)],
        cwd=folder, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    clean = cv2.imread(str(without))
    clean_gold = (clean[:, :, 2] > 140) & (clean[:, :, 1] > 95) & (clean[:, :, 0] < 130)
    assert np.count_nonzero(clean_gold[330:450]) == 0


@pytest.mark.parametrize('emoji', list(captions.EMOJI_CHOICES[1:]))
def test_each_original_vector_pictogram_renders_actual_pixels(tmp_path, emoji):
    import cv2
    import numpy as np
    saved = edit()
    saved.update(font_size=30, caption_y=.78, animated_captions=False)
    saved['captions'][0]['emoji'] = emoji
    captions.write_ass(tmp_path/'captions.ass', saved, 360, 640)
    output = tmp_path/'glyph.png'
    fonts = (Path(__file__).resolve().parents[1]/'assets'/'fonts').as_posix().replace(':', r'\:')
    result = subprocess.run([pipeline._binary('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'color=c=0x102030:s=360x640:r=12:d=1',
        '-vf', f"ass=filename=captions.ass:fontsdir='{fonts}'", '-frames:v', '1', str(output)],
        cwd=tmp_path, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    frame = cv2.imread(str(output))
    difference = abs(frame[330:420].astype(float) - np.array([48, 32, 16])).max(axis=2)
    assert np.count_nonzero(difference > 50) > 150

"""Source-timed phrase captions and original, portable vector emoji pictograms.

This is a deterministic editorial formatter, not a language-model claim of
semantic understanding. Timing comes only from recognized source words. Saved
caption text, emphasis and emoji are authoritative; rendering never regenerates
or rewrites them. Legacy caption styles remain in pipeline.py.
"""
from __future__ import annotations

import math
from pathlib import Path
import re

PRESETS = {
    'creator-bold': {'label': 'Creator Bold', 'bold': True, 'outline': 3.0, 'pop': 103, 'fade': 65},
    'karaoke-pop': {'label': 'Karaoke Pop', 'bold': True, 'outline': 2.5, 'pop': 105, 'fade': 45},
    'clean-editorial': {'label': 'Clean Editorial', 'bold': False, 'outline': 1.5, 'pop': 100, 'fade': 110},
    'podcast-dynamic': {'label': 'Podcast Dynamic', 'bold': True, 'outline': 2.0, 'pop': 102, 'fade': 80},
    'viral-energy': {'label': 'Viral Energy', 'bold': True, 'outline': 3.5, 'pop': 106, 'fade': 35},
}
EMOJI_CHOICES = ('', '💰', '💡', '🔥', '😮', '🎯', '📈', '❤️', '🎉')
_CUES = (
    ('💰', r'\b(?:money|dollars?|budget|cost|costs|million|billion|expensive|profit)\b'),
    ('💡', r'\b(?:idea|lesson|learned|discovered|realized|insight)\b'),
    ('🔥', r'\b(?:burning|on fire|flames?)\b'),
    ('😮', r'\b(?:unbelievable|shocked|surprised|astonishing)\b'),
    ('🎯', r'\b(?:goal|target|bullseye)\b'),
    ('📈', r'\b(?:growth|increased|increase|grew|doubled)\b'),
    ('❤️', r'\b(?:love|loved|loving)\b'),
    ('🎉', r'\b(?:celebrate|celebration|congratulations|won|winning)\b'),
)
_STOP = frozenset('a an the is was were are be been being i me my we us our you your he she it they them this that these those and or but if so to of in on for at as with by from about just really very can could would should have has had do does did not no never when where what who why how which then than there here all some much many more most such also yet however although while before after once until since'.split())
_GENERIC = frozenset(('actually', 'really', 'experience', 'experiences', 'dropping', 'something',
                      'anything', 'everything', 'basically', 'probably', 'literally', 'because'))
_MEANINGFUL = re.compile(r'\b(?:impossible|failure|failed|success|risk|secret|mistake|record|cheaper|faster|dangerous|difference|truth|million|billion|thousand|hundred)\b', re.I)
_ABBREVIATIONS = frozenset(('mr', 'mrs', 'ms', 'dr', 'prof', 'jr', 'sr', 'st'))


def _number(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _join(words):
    result = ''
    for word in words:
        value = str(word.get('word', ''))
        if result and value and not value[0].isspace() and not re.match(r'^[,.;:!?\u2019]', value):
            result += ' '
        result += value
    return result.strip()


def _capital_token(value):
    token = str(value).strip().strip('"\u201c\u201d\u2018\u2019()[]{}.,!?;:')
    return bool(token and token[0].isalpha() and token[0].isupper())


def _proper_names(text):
    """Orthographic name hints, not a named-entity model or factual assertion."""
    tokens = list(re.finditer(r'\b[^\W\d_][\w’\'-]*\.?', text))
    names, run = [], []

    def flush():
        if len(run) >= 2:
            names.append(text[run[0].start():run[-1].end()].rstrip('.'))
        elif run:
            value = run[0].group().rstrip('.')
            if len(value) >= 2 and any(letter.isupper() for letter in value[1:]):
                names.append(value)  # Acronyms or internal capitals such as MrBeast.
        run.clear()

    for token in tokens:
        value = token.group().rstrip('.')
        separator = text[run[-1].end():token.start()] if run else ''
        if _capital_token(value) and value.lower() not in _STOP | _GENERIC:
            if run and not separator.isspace():
                flush()
            run.append(token)
        else:
            flush()
    flush()
    return names


def contextual_emphasis(text: str) -> tuple[list[str], str]:
    """Conservative lexical cues, with explicit limits and negation suppression."""
    keywords = []
    emoji = ''
    if not re.search(r"\b(?:not|never|no|isn't|wasn't|don't|didn't)\b", text, re.I):
        for candidate, pattern in _CUES:
            match = re.search(pattern, text, re.I)
            if match:
                emoji = candidate
                keywords.append(match.group())
                break
    candidates = (re.findall(r'[$£€]?\d[\d,.]*(?:%|[kKmMbB])?', text) +
                  _proper_names(text) + [match.group() for match in _MEANINGFUL.finditer(text)])
    for token in candidates:
        if token.lower() not in _STOP | _GENERIC and token.lower() not in {value.lower() for value in keywords}:
            keywords.append(token)
        if len(keywords) >= 2:
            break
    return keywords[:2], emoji


def make_captions(sentences: list[dict], start: float, end: float, *, preset='creator-bold',
                  emoji_enabled=True, emoji_frequency='balanced', intensity='balanced') -> list[dict]:
    """Group actual aligned words at punctuation, pauses, or readable phrase bounds.

    Unaligned sentences remain visibly untimed at word level; no synthetic word
    alignment is manufactured. Returned words use normalized spacing but preserve
    recognized text, source timestamps and confidence.
    """
    result, group = [], []
    last_emoji = -1e9
    separation = {'off': math.inf, 'subtle': 8.0, 'balanced': 5.0, 'frequent': 2.8}.get(emoji_frequency, 5.0)
    separation *= {'minimal': 1.6, 'balanced': 1.0, 'energetic': .75}.get(intensity, 1.0)
    maximum_words = 5 if preset == 'clean-editorial' else 4

    def append(text, left, right, words):
        nonlocal last_emoji
        if not text.strip() or right <= left:
            return
        keywords, emoji = contextual_emphasis(text)
        if not emoji_enabled or emoji_frequency == 'off' or left - last_emoji < separation:
            emoji = ''
        if emoji:
            last_emoji = left
        result.append({'id': f'caption-{len(result)+1}', 'start': left, 'end': right, 'text': text,
                       'words': words, 'keywords': keywords, 'emoji': emoji, 'emoji_enabled': True,
                       'timestamp_origin': 'recognized'})

    def flush():
        if group:
            # The canonical words must concatenate back to the displayed text.
            normalized = [{**w, 'word': ('' if n == 0 else ' ') + str(w['word']).strip()} for n, w in enumerate(group)]
            append(_join(normalized), group[0]['start'], group[-1]['end'], normalized)
            group.clear()

    for sentence in sentences:
        if _number(sentence.get('end')) <= start or _number(sentence.get('start')) >= end:
            continue
        if not sentence.get('words'):
            flush()
            append(str(sentence.get('text', '')).strip(), max(start, _number(sentence.get('start'))),
                   min(end, _number(sentence.get('end'))), [])
            continue
        source_words = sentence['words']
        for position, word in enumerate(source_words):
            left, right = max(start, _number(word.get('start'))), min(end, _number(word.get('end')))
            if right <= left or not str(word.get('word', '')).strip():
                continue
            proper_continuation = bool(group and _capital_token(group[-1]['word']) and _capital_token(word['word']))
            # Keep adjacent capitalized name tokens together, within a bounded
            # six-word/42-character/3.2-second exception to short phrase limits.
            word_limit, char_limit, time_limit = (6, 42, 3.2) if proper_continuation else (maximum_words, 32, 2.4)
            entity_tail = [word]
            if _capital_token(word['word']) and not proper_continuation:
                for following in source_words[position + 1:]:
                    if not _capital_token(following.get('word', '')):
                        break
                    entity_tail.append(following)
            entity_overflow = (len(entity_tail) > 1 and group and
                               (len(group) + len(entity_tail) > 6 or len(_join(group + entity_tail)) > 42))
            if group and (entity_overflow or len(group) >= word_limit or len(_join(group)) + len(str(word['word'])) > char_limit
                          or right - group[0]['start'] > time_limit or left - group[-1]['end'] > .45):
                flush()
            group.append({**word, 'start': left, 'end': right})
            token = str(word['word']).strip().strip('\"”\u2019')
            abbreviation = token.rstrip('.').lower() in _ABBREVIATIONS and token.endswith('.')
            if re.search(r'[.!?][\"”\u2019]*\s*$', str(word['word'])) and not abbreviation:
                flush()
        flush()
    return result


def _word_spans(text, words):
    """Only animate when recognized words still match authoritative edited text."""
    if re.sub(r'\s+', '', text) != re.sub(r'\s+', '', ''.join(str(w.get('word', '')) for w in words)):
        return []
    cursor, spans = 0, []
    for word in words:
        token = str(word.get('word', '')).strip()
        if not token:
            continue
        position = text.find(token, cursor)
        if position < 0:
            return []
        spans.append((position, position + len(token), word))
        cursor = position + len(token)
    return spans


def _keyword_spans(text, keywords):
    spans = []
    for keyword in keywords[:20]:
        if not isinstance(keyword, str) or not keyword.strip():
            continue
        # Whole lexical terms only, so 'cost' never highlights 'costume'.
        for match in re.finditer(r'(?<!\w)' + re.escape(keyword.strip()) + r'(?!\w)', text, re.I):
            spans.append(match.span())
    return spans


def _content(text, ranges, active, keyword_spans, primary, highlight, escape):
    lines = []
    for left, right in ranges:
        pieces, previous = [], None
        for index in range(left, right):
            emphasis = any(a <= index < b for a, b in keyword_spans)
            spoken = bool(active and active[0] <= index < active[1])
            state = ('active' if spoken else 'keyword' if emphasis else 'normal')
            if state != previous:
                # No font-size changes inside a line: layout stays stable as words activate.
                pieces.append('{\\1c' + (highlight if spoken or emphasis else primary) +
                              ('\\u1' if spoken else '\\u0') + '}')
                previous = state
            pieces.append(escape(text[index]))
        lines.append(''.join(pieces))
    return r'\N'.join(lines)


# Original 100×100 pictograms in libass vector coordinates. Multiple layers make
# colored glyphs reliable without copying vendor artwork or requiring emoji fonts.
_CIRCLE = 'm 50 3 b 76 3 97 24 97 50 b 97 76 76 97 50 97 b 24 97 3 76 3 50 b 3 24 24 3 50 3'
_GLYPHS = {
    '💰': [('#E7B44A', 'm 34 20 l 25 3 75 3 66 20 l 74 31 b 98 52 98 83 78 94 l 22 94 b 2 83 2 52 26 31 l 34 20'),
           ('#916319', 'm 30 20 l 70 20 70 28 30 28'), ('#FFFFFF', 'm 45 34 l 55 34 55 40 69 40 69 48 48 48 48 55 68 55 68 77 55 77 55 84 45 84 45 77 31 77 31 69 59 69 59 63 39 63 39 40 45 40')],
    '💡': [('#FFD55F', 'm 50 5 b 91 5 94 49 69 64 l 67 76 33 76 31 64 b 6 49 9 5 50 5'),
           ('#E6EAF0', 'm 33 77 l 67 77 65 88 57 96 43 96 35 88'),
           ('#BD8816', 'm 39 41 l 45 41 50 59 55 41 61 41 54 68 46 68')],
    '🔥': [('#FF7B35', 'm 52 1 b 58 30 89 32 92 60 b 100 99 11 113 8 65 b 8 40 32 31 29 15 b 44 26 39 41 52 1'),
           ('#FFE36F', 'm 54 43 b 57 58 76 63 73 79 b 67 104 30 96 30 77 b 29 63 45 62 54 43')],
    '😮': [('#FFD45A', _CIRCLE), ('#4E3325', 'm 25 29 l 35 29 35 43 25 43 m 65 29 l 75 29 75 43 65 43 m 50 52 b 69 52 69 86 50 86 b 31 86 31 52 50 52')],
    '🎯': [('#F5F5F5', _CIRCLE), ('#E64356', 'm 50 12 b 72 12 88 28 88 50 b 88 72 72 88 50 88 b 28 88 12 72 12 50 b 12 28 28 12 50 12'),
           ('#FFFFFF', 'm 50 27 b 63 27 73 37 73 50 b 73 63 63 73 50 73 b 37 73 27 63 27 50 b 27 37 37 27 50 27'),
           ('#E64356', 'm 50 39 b 65 39 65 61 50 61 b 35 61 35 39 50 39'), ('#344357', 'm 45 47 l 84 8 91 15 52 54')],
    '📈': [('#EDF3FF', 'm 5 5 l 95 5 95 95 5 95'), ('#708094', 'm 15 15 l 20 15 20 80 85 80 85 85 15 85'),
           ('#E84D5F', 'm 24 65 l 41 46 55 56 74 28 65 28 65 20 87 20 87 43 79 43 79 34 56 68 42 58 30 72')],
    '❤️': [('#FA4966', 'm 50 92 l 13 55 b -15 24 25 -9 50 20 b 75 -9 115 24 87 55 l 50 92')],
    '🎉': [('#FFC650', 'm 10 94 l 28 29 73 74 10 94'), ('#F66F85', 'm 31 32 l 40 35 24 89 15 91 m 51 49 l 59 57 49 81 39 85'),
           ('#A880FF', 'm 49 4 l 56 4 56 23 49 23 m 76 24 l 82 18 89 26 83 32 m 72 52 l 94 45 97 53 75 60'),
           ('#70DECC', 'm 26 4 l 34 4 34 14 26 14 m 89 5 l 96 5 96 12 89 12')],
}


def write_ass(path: Path, edit: dict, width: int, height: int) -> None:
    """Burn authoritative saved captions through the same ASS path as previews.

    Safe font measurement, escaping and clip offsets are shared with the existing
    renderer. A bounded event per word interval gives active-word changes while
    gaps reset emphasis. Emoji are original illustrative vector pictograms.
    """
    from .pipeline import _safe_caption_layout, _caption_plain_text, _ass_text, _ass_time, _ass_color, PipelineError
    preset = edit.get('caption_preset', 'creator-bold')
    if preset not in PRESETS:
        raise PipelineError('Unsupported professional caption preset.')
    style = PRESETS[preset]
    font = edit.get('font_family', 'DejaVu Sans')
    if font not in ('DejaVu Sans', 'Liberation Sans'):
        raise PipelineError('Unsupported caption font.')
    start, end = _number(edit.get('start')), _number(edit.get('end'))
    primary, highlight = _ass_color(edit.get('text_color', '#FFFFFF')), _ass_color(edit.get('highlight_color', '#B9F26B'))
    outline = min(8, max(0, _number(edit.get('stroke'), 2))) * style['outline'] / 2
    shadow = min(8, max(0, _number(edit.get('shadow'), 1)))
    header = (f'[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n'
              '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
              f'Style: Main,{font},42,{primary},{highlight},&H00000000,&H80000000,{-1 if style["bold"] else 0},0,0,0,100,100,0,0,1,{outline:.2f},{shadow:.2f},2,0,0,0,1\n\n'
              '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
    events = []
    event_bytes = 0
    intensity = edit.get('intensity', 'balanced')
    animate_effects = bool(edit.get('animated_captions', True) and intensity != 'minimal')

    def event(layer, left, right, content):
        nonlocal event_bytes
        if round((right - start)*100) <= round((left - start)*100):
            return
        line = f'Dialogue: {layer},{_ass_time(left-start)},{_ass_time(right-start)},Main,,0,0,0,,{content}'
        event_bytes += len(line.encode('utf-8'))
        if len(events) >= 12000 or event_bytes > 8 * 1024**2:
            raise PipelineError('Too many overlapping caption effects to render safely. Split this clip or reduce its captions.')
        events.append(line)

    hook_layout = None
    hook_end = min(end, start + 2.5)
    if edit.get('opening_hook', False) and end > start:
        title = _caption_plain_text(str(edit.get('title', '')).strip())
        if title:
            # It is the saved title, never a synthesized claim. The shared
            # measurement may reserve an 18px box while the actual hook font
            # remains <=85% of an unusually small user-selected caption font.
            requested_hook_size = min(64, max(12, _number(edit.get('font_size'), 42) * .85))
            hook_edit = {**edit, 'font_size': max(18, round(requested_hook_size)), 'caption_x': .5,
                         'caption_y': .22, 'stroke': 1.5, 'shadow': 2}
            hook_layout = _safe_caption_layout(title, hook_edit, width, height, bold=True)
            hook_size = min(requested_hook_size, hook_layout['size'])
            content = r'\N'.join(_ass_text(title[left:right]) for left, right in hook_layout['lines'])
            fade = '\\fad(110,150)' if animate_effects else ''
            overrides = f'{{\\q2\\an2\\pos({hook_layout["x"]:.1f},{hook_layout["y"]:.1f})\\fs{hook_size:.1f}\\b1\\bord1.5\\shad2\\1c{primary}{fade}}}'
            event(1, start, hook_end, overrides + content)

    for caption in edit.get('captions', []) if edit.get('captions_enabled', True) else []:
        cs, ce = max(start, _number(caption.get('start'))), min(end, _number(caption.get('end')))
        text = _caption_plain_text(str(caption.get('text', '')).strip())
        if ce <= cs or not text:
            continue
        if len(text) > 1000:
            raise PipelineError('A caption is too long to render safely; split it into shorter captions.')
        measured = {**edit, 'stroke': outline, 'shadow': shadow}
        layout = _safe_caption_layout(text, measured, width, height, bold=style['bold'])
        if hook_layout and cs < hook_end and layout['y'] - layout['height'] < hook_layout['y'] + height * .03:
            # User placement can put captions at the top. Reserve room for the
            # optional hook and its pictogram instead of allowing text collision.
            adjusted_y = (hook_layout['y'] + height * .035 + layout['height'] +
                          min(width*.105, max(28, layout['size'] * 1.12)) + 10) / height
            measured['caption_y'] = max(_number(edit.get('caption_y'), .8), adjusted_y)
            layout = _safe_caption_layout(text, measured, width, height, bold=style['bold'])
        active_words = _word_spans(text, caption.get('words', []))
        keywords = _keyword_spans(text, caption.get('keywords', [])) if edit.get('keyword_highlights', True) else []
        boundaries = sorted({cs, ce, *[max(cs, min(ce, _number(w.get(key))))
                                     for _, _, w in active_words for key in ('start', 'end')]})
        animate = edit.get('animated_captions', True)
        if not animate:
            boundaries = [cs, ce]
        for left, right in zip(boundaries, boundaries[1:]):
            if right <= left:
                continue
            middle = (left + right) / 2
            active = next(((a, b) for a, b, w in active_words
                           if _number(w.get('start')) <= middle < _number(w.get('end'))), None) if animate else None
            # Intro applies on the first segment only, keeping later word events
            # at identical scale and position. Scale <=100 preserves safe margins.
            effects = ''
            if animate_effects and left == cs:
                fade = min(round(style['fade'] * (.6 if intensity == 'energetic' else 1)), max(0, round((right-left)*500)))
                effects = f'\\fad({fade},0)'
                if style['pop'] > 100:
                    intro_scale = 92 if intensity == 'energetic' else 96
                    effects += f'\\fscx{intro_scale}\\fscy{intro_scale}\\t(0,90,\\fscx100\\fscy100)'
            overrides = f'{{\\q2\\an2\\pos({layout["x"]:.1f},{layout["y"]:.1f})\\fs{layout["size"]:.1f}{effects}}}'
            event(0, left, right, overrides + _content(text, layout['lines'], active, keywords, primary, highlight, _ass_text))
        emoji = caption.get('emoji', '')
        if edit.get('contextual_emojis', True) and edit.get('emoji_frequency', 'balanced') != 'off' and caption.get('emoji_enabled', True) and emoji in _GLYPHS:
            size = min(width * .105, max(28.0, layout['size'] * 1.12))
            # Reserve its own area above the full measured caption, safely inside
            # the image. If there is no room, suppress rather than overlap text.
            top = layout['y'] - layout['height'] - size - 10
            if top >= height * .06:
                xpos = max(width*.06, min(width*.94-size, layout['x']-size/2))
                scale = size
                for layer, (fill, drawing) in enumerate(_GLYPHS[emoji], 2):
                    tags = f'{{\\an7\\pos({xpos:.1f},{top:.1f})\\p1\\fscx{scale:.2f}\\fscy{scale:.2f}\\bord0\\shad0\\1c{_ass_color(fill)}'
                    if animate_effects:
                        fade = 40 if intensity == 'energetic' else 75
                        tags += f'\\fad({fade},{fade})'
                    event(layer, cs, ce, tags + '}' + drawing)
    path.write_text(header + '\n'.join(events) + '\n', encoding='utf-8-sig')

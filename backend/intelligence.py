"""Versioned, local audiovisual evidence and conservative genre routing.

Measurements are not semantic action recognition: a cut is not a plot point,
motion is not a kill, and an audio peak is not proof of audience excitement.
The v3 speech selector remains available and is reused for dialogue candidates.
"""
from __future__ import annotations

import copy
import json
import math
import re
import statistics
import time
from pathlib import Path

VERSION = 'multimodal-evidence-v4.2'
MAX_SAMPLES = 6000
MAX_CACHE_BYTES = 8 * 1024 * 1024
GENRES = {'auto', 'general', 'podcast', 'gaming', 'cinematic', 'education', 'sports', 'comedy', 'visual'}
LIMITATIONS = [
    'Activity measurements do not identify game wins, goals, emotions or narrative meaning.',
    'Genre confidence is an uncalibrated heuristic, not a probability of correctness.',
    'Review the source setup and ending; visual activity does not prove a complete story.',
]
_LEXICON = {
    'gaming': r'\b(gameplay|respawn|killstreak|multikill|boss|checkpoint|level up|controller|mana|loot|headshot|round win)\b',
    'education': r'\b(explain|because|learn|example|lesson|tutorial|step|science|evidence|demonstrat\w*)\b',
    'sports': r'\b(goal|scoreboard|penalty|touchdown|overtime|halftime|inning|referee|match)\b',
    'comedy': r'\b(joke|funny|laugh\w*|hilarious|punchline|ridiculous)\b',
}


def _finite(value, fallback=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else fallback
    except (ValueError, TypeError):
        return fallback


def _clamp(value, lo=0.0, hi=1.0):
    return min(hi, max(lo, _finite(value)))


def sample_interval(duration):
    """At most 6000 samples, with a normal half-second temporal resolution."""
    return max(.5, max(0., _finite(duration)) / (MAX_SAMPLES - 1))


def _atomic(path, value):
    pending = path.with_suffix('.pending.json')
    pending.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')
    pending.replace(path)


def _valid_samples(samples, duration):
    if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_SAMPLES:
        return False
    previous = -1.
    for item in samples:
        if not isinstance(item, dict):
            return False
        t = _finite(item.get('time'), -1)
        if not 0 <= t <= duration + .05 or t <= previous:
            return False
        if any(not 0 <= _finite(item.get(k), -1) <= 1 for k in ('motion', 'scene_delta', 'brightness', 'detail')):
            return False
        if not isinstance(item.get('cut'), bool):
            return False
        previous = t
    # A cancelled/truncated decode must never be reused as a complete source.
    tolerance = max(2., sample_interval(duration) * 2)
    return samples[0]['time'] <= sample_interval(duration) and previous >= max(0., duration - tolerance)


def _visual_samples(proxy, duration, progress):
    import cv2
    import numpy as np
    from .pipeline import PipelineError

    cap = cv2.VideoCapture(str(proxy))
    if not cap.isOpened():
        cap.release()
        raise PipelineError('Visual analysis could not open the local video proxy.')
    samples = []
    fps = _finite(cap.get(cv2.CAP_PROP_FPS), 8.)
    fps = fps if .1 <= fps <= 240 else 8.
    step = sample_interval(duration)
    next_time, frame_index, last_cut = 0., 0, -10.
    previous, previous_hist = None, None
    started = time.monotonic()
    timeout = max(120., min(1800., duration * 1.5))
    max_frames = math.ceil((duration + 2) * fps)
    try:
        while frame_index < max_frames and len(samples) < MAX_SAMPLES:
            timestamp = frame_index / fps
            if timestamp > duration + .01:
                break
            if time.monotonic() - started > timeout:
                raise PipelineError('Visual analysis exceeded its time budget. Speech analysis is retained for retry.')
            if not cap.grab():
                break
            frame_index += 1
            if timestamp + .0001 < next_time:
                continue
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            scale = min(1., 320 / width, 180 / height)
            small = cv2.resize(frame, (max(2, round(width * scale)), max(2, round(height * scale))), interpolation=cv2.INTER_AREA)
            gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (3, 3), 0)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
            cv2.normalize(hist, hist, alpha=1, norm_type=cv2.NORM_L1)
            motion = float(cv2.absdiff(gray, previous).mean() / 255) if previous is not None and previous.shape == gray.shape else 0.
            delta = float(cv2.compareHist(previous_hist, hist, cv2.HISTCMP_BHATTACHARYYA)) if previous_hist is not None else 0.
            cut = delta >= .58 and motion >= .07 and timestamp - last_cut >= .8
            if cut:
                last_cut = timestamp
            samples.append({'time': round(timestamp, 4), 'motion': round(_clamp(motion), 5),
                            'scene_delta': round(_clamp(delta), 5), 'brightness': round(float(gray.mean()) / 255, 5),
                            'detail': round(_clamp(float(gray.std()) / 128), 5), 'cut': bool(cut)})
            previous, previous_hist = gray, hist
            next_time += step
            if len(samples) == 1 or len(samples) % 60 == 0:
                progress(f'Measuring visual activity · {timestamp:.0f}s / {duration:.0f}s')
        if not samples:
            raise PipelineError('Visual analysis found no decodable video samples.')
        if duration > 4 and samples[-1]['time'] < duration * .85:
            raise PipelineError('The video proxy ended before the source timeline. Retry preprocessing.')
        return samples
    finally:
        cap.release()


def _speech_coverage(segments, start, end):
    intervals = sorted((max(start, _finite(s.get('start'))), min(end, _finite(s.get('end')))) for s in segments
                       if _finite(s.get('end')) > start and _finite(s.get('start')) < end)
    total, finish = 0., start
    for left, right in intervals:
        total += max(0., right - max(left, finish))
        finish = max(finish, right)
    return _clamp(total / max(.001, end - start))


def classify(samples, transcript, duration, start=0., end=None):
    """Conservative multilabel evidence; metadata/filenames are not classifiers."""
    end = duration if end is None else end
    local = [s for s in samples if start <= s['time'] < end]
    segments = [s for s in transcript.get('segments', []) if _finite(s.get('end')) > start and _finite(s.get('start')) < end]
    coverage = _speech_coverage(segments, start, end)
    text = ' '.join(str(s.get('text', '')) for s in segments)
    motion = statistics.median([s['motion'] for s in local] or [0.])
    cuts = sum(s['cut'] for s in local)
    candidates = []
    if coverage >= .30:
        candidates.append(('podcast', .58 if motion < .09 else .42,
                           f'Recognized speech covers {coverage:.0%} of this interval; median sampled image change {motion:.3f}.'))
    for genre, pattern in _LEXICON.items():
        count = len(re.findall(pattern, text, re.I))
        if count < (3 if genre in ('gaming', 'sports') else 2) or coverage < .10:
            continue
        if genre in ('gaming', 'sports') and motion < .012:
            continue
        confidence = min(.76, .48 + .035 * count)
        candidates.append((genre, confidence, f'{count} relevant recognized terms with {coverage:.0%} speech coverage and {motion:.3f} measured image change.'))
    if coverage < .12 and (motion > .006 or cuts > 0):
        candidates.append(('visual', .64, f'Low recognized speech coverage ({coverage:.0%}) with measured visual activity; content semantics are unknown.'))
    candidates.sort(key=lambda x: -x[1])
    reliable = [c for c in candidates if c[1] >= .55]
    primary = reliable[0][0] if reliable else 'general'
    return {'primary': primary, 'labels': [c[0] for c in reliable] or ['general'],
            'confidence': round(reliable[0][1] if reliable else .25, 3), 'source': 'automatic',
            'evidence': [c[2] for c in reliable] or ['Evidence is insufficient for a specialist; using the general route.']}


def analyze_evidence(source, workdir, metadata, transcript, progress, energy=None, proxy=None):
    """Return a small reusable timeline; cached pixels never leave the computer."""
    from .pipeline import PipelineError
    source, workdir = Path(source), Path(workdir)
    proxy = Path(proxy) if proxy is not None else source
    duration = _finite(metadata.get('duration'))
    if not 0 < duration <= 24 * 3600:
        raise ValueError('Visual analysis requires a finite video duration of at most 24 hours.')
    if not source.is_file() or not proxy.is_file():
        raise PipelineError('Visual analysis requires an existing local source and proxy.')
    workdir.mkdir(parents=True, exist_ok=True)
    source_stat, proxy_stat = source.stat(), proxy.stat()
    fingerprint = {'version': VERSION, 'size': source_stat.st_size, 'mtime': source_stat.st_mtime_ns,
                   'proxy_size': proxy_stat.st_size, 'proxy_mtime': proxy_stat.st_mtime_ns,
                   'duration': duration, 'sample_limit': MAX_SAMPLES}
    cache = workdir / 'multimodal-visual-v4.json'
    samples = None
    if cache.is_file() and cache.stat().st_size <= MAX_CACHE_BYTES:
        try:
            cached = json.loads(cache.read_text(encoding='utf-8'))
            if cached.get('fingerprint') == fingerprint and _valid_samples(cached.get('samples'), duration):
                samples = cached['samples']
        except (ValueError, OSError, AttributeError):
            pass
    hit = samples is not None
    if samples is None:
        samples = _visual_samples(proxy, duration, progress)
        _atomic(cache, {'fingerprint': fingerprint, 'samples': samples})
    else:
        progress('Reusing measured visual timeline')
    genre = classify(samples, transcript, duration)
    regions = []
    for start in range(0, math.ceil(duration), 30):
        end = min(duration, start + 30)
        regions.append({'start': start, 'end': end, 'genre': classify(samples, transcript, duration, start, end)})
    return {'version': VERSION, 'samples': samples, 'sample_count': len(samples),
            'scene_cuts': [s['time'] for s in samples if s['cut']], 'regions': regions, 'genre': genre,
            'automatic_genre': copy.deepcopy(genre),
            'diagnostics': {'visual_cache_hit': hit, 'sample_interval_seconds': round(sample_interval(duration), 4),
                            'max_samples': MAX_SAMPLES, 'analysis_pixels_max': [320, 180],
                            'method': 'sampled image change and HSV histogram distance; local lexical speech routing',
                            'limitations': LIMITATIONS}}


def _metrics(samples, energy, transcript, start, end):
    local = [s for s in samples if start <= s['time'] <= end]
    audio = [_finite(s.get('dbfs'), -120) for s in energy or [] if start <= _finite(s.get('start')) <= end]
    return {'motion': statistics.mean([s['motion'] for s in local] or [0.]),
            'motion_peak': max([s['motion'] for s in local] or [0.]), 'cuts': sum(s['cut'] for s in local),
            'brightness': statistics.mean([s['brightness'] for s in local] or [.5]),
            'detail': statistics.mean([s['detail'] for s in local] or [0.]),
            'audio_peak': max(audio) if audio else None,
            'audio_range': max(audio) - min(audio) if audio else 0.,
            'speech_coverage': _speech_coverage(transcript.get('segments', []), start, end)}


def _bounds(anchor, duration, minimum, maximum, genre, cuts):
    target = min(duration, maximum, max(minimum, 24 if genre in ('gaming', 'sports') else 20))
    pre = .45 if genre == 'cinematic' else .35
    start = max(0., anchor - target * pre)
    end = min(duration, start + target)
    start = max(0., end - target)
    if genre == 'cinematic':
        left = [t for t in [0., *cuts] if t <= start]
        right = [t for t in [*cuts, duration] if t >= end]
        proposed = (left[-1], right[0])
        if minimum <= proposed[1] - proposed[0] <= maximum:
            start, end = proposed
    return round(start, 3), round(end, 3)



def _activity_end(samples, transcript, start, end, anchor, minimum, genre):
    """Trim a sustained low-motion tail, without pretending to know its content.

    Restrict this to activity routes and sparse dialogue. Cinematic pauses and
    speech endings keep their original context. A small closing beat remains.
    """
    if genre not in ('gaming', 'sports', 'visual') or _speech_coverage(transcript.get('segments', []), start, end) >= .12:
        return end
    active = [s['motion'] for s in samples if max(start, anchor - 3) <= s['time'] <= anchor and not s['cut']]
    if not active or statistics.median(active) < .025:
        return end
    threshold = min(.015, max(.004, statistics.median(active) * .15))
    quiet_start = None
    for sample in samples:
        if sample['time'] < anchor + 1 or sample['time'] > end:
            continue
        if sample['motion'] > threshold or sample['cut']:
            quiet_start = None
            continue
        if quiet_start is None:
            quiet_start = sample['time']
        if sample['time'] - quiet_start >= 3:
            proposed = max(start + minimum, quiet_start + 1)
            # No recognized words may be discarded by this activity heuristic.
            if proposed < end - 1 and not _speech_coverage(transcript.get('segments', []), proposed, end):
                return round(proposed, 3)
    return end


def _observations(samples, energy, start, end, coverage):
    local = [s for s in samples if start <= s['time'] <= end]
    result = []
    if local:
        peak = max(local, key=lambda s: s['motion'])
        result.append({'time': peak['time'], 'kind': 'motion', 'value': round(peak['motion'], 4),
                       'description': 'Peak sampled image change; camera movement and edits can also cause this signal.'})
        for cut in [s for s in local if s['cut']][:4]:
            result.append({'time': cut['time'], 'kind': 'scene', 'value': cut['scene_delta'],
                           'description': 'Measured color-distribution change consistent with a scene cut.'})
    audio = [s for s in energy or [] if start <= _finite(s.get('start')) <= end]
    if audio:
        peak = max(audio, key=lambda s: _finite(s.get('dbfs'), -120))
        result.append({'time': _finite(peak.get('start')), 'kind': 'audio', 'value': _finite(peak.get('dbfs'), -120),
                       'description': 'Measured audio level in dBFS; no sound-event identity is inferred.'})
    if coverage:
        result.append({'time': start, 'kind': 'speech', 'value': round(coverage, 3),
                       'description': 'Fraction of interval covered by recognized speech timestamps.'})
    return result[:8]


def select_clips(transcript, duration, settings, evidence, energy=None, context=None):
    """Reuse dialogue stories, add measured visual windows, and diversify results."""
    from .editorial import discover
    from .pipeline import default_edit, _sentences, _overlap
    from .caption_engine import make_captions

    duration = _finite(duration)
    if duration < .3:
        return []
    samples = evidence.get('samples', [])
    if not _valid_samples(samples, duration):
        raise ValueError('Visual timeline is invalid or not aligned with this source.')
    chosen_genre = settings.get('content_genre', 'auto')
    if chosen_genre not in GENRES:
        raise ValueError('Unknown content genre.')
    # Selectors may be replayed with several overrides against one pixel cache.
    # Preserve the original automatic route rather than inheriting a prior override.
    automatic = evidence.get('automatic_genre', evidence.get('genre'))
    if not isinstance(automatic, dict) or automatic.get('source') != 'automatic':
        automatic = classify(samples, transcript, duration)
    evidence['automatic_genre'] = copy.deepcopy(automatic)
    genre = copy.deepcopy(automatic)
    if chosen_genre != 'auto':
        genre = {'primary': chosen_genre, 'labels': list(dict.fromkeys([chosen_genre, *genre['labels']])),
                 'confidence': 1., 'source': 'user-override', 'evidence': ['The user selected this editorial route; this is not an automatic classification claim.']}
    evidence['genre'] = genre
    route = genre['primary']
    minimum = max(3., min(180., _finite(settings.get('min_duration'), 15)))
    maximum = max(minimum, min(180., _finite(settings.get('max_duration'), 45)))
    minimum = min(minimum, duration)
    count = max(1, min(20, int(settings.get('requested_clips', 10))))
    speech_settings = dict(settings)
    mode = settings.get('discovery_mode', 'best_highlights')
    if mode in ('best_highlights', 'viral_moments'):
        if route == 'education':
            speech_settings['discovery_mode'] = 'educational_insights'
        elif route == 'comedy':
            speech_settings['discovery_mode'] = 'funny_moments'
    clips = discover(transcript, duration, speech_settings, energy, context=context)
    speech_ids = {id(c) for c in clips}
    total_coverage = _speech_coverage(transcript.get('segments', []), 0., duration)
    # Custom prompts remain grounded in recognized text. No visual search model
    # exists here, so an unrelated prompt must not return arbitrary action clips.
    visual_allowed = mode != 'custom_prompt' and not settings.get('topic', '').strip() and (
        route in ('gaming', 'sports', 'cinematic', 'visual', 'general') or total_coverage < .12)
    if visual_allowed:
        median_motion = statistics.median([s['motion'] for s in samples])
        deviation = statistics.median([abs(s['motion'] - median_motion) for s in samples])
        threshold = max(.008, median_motion + max(.004, deviation * 1.25))
        anchors = []
        for i, s in enumerate(samples):
            if s['brightness'] < .035 or s['detail'] < .015:
                continue
            # Cut-only spikes are weak: a title-card cut is not an action peak.
            motion = s['motion'] if not s['cut'] else s['motion'] * .35
            nearby = samples[max(0, i - 2):min(len(samples), i + 3)]
            if motion < threshold or motion < max((v['motion'] if not v['cut'] else v['motion'] * .35) for v in nearby):
                continue
            # Isolated menu/slide transitions can change many pixels. Activity
            # routes need surrounding movement; the cause still remains unknown.
            if route in ('gaming', 'sports', 'visual') and statistics.median([v['motion'] for v in nearby]) < .012:
                continue
            anchors.append((motion, s['time']))
        # Steady visual action may lack a relative outlier. Use a source-relative
        # strong window only when an absolute amount of actual movement exists.
        if not anchors and median_motion > .018:
            anchors = [(s['motion'], s['time']) for s in samples if not s['cut'] and s['brightness'] > .035 and s['detail'] > .015]
        anchors.sort(reverse=True)
        accepted = []
        for strength, anchor in anchors:
            if any(abs(anchor - prior) < max(3., minimum * .6) for prior in accepted):
                continue
            accepted.append(anchor)
            if len(accepted) >= min(80, count * 8):
                break
        sentences = _sentences(transcript)
        for anchor in accepted:
            local_route = route
            if chosen_genre == 'auto':
                region = next((r for r in evidence.get('regions', []) if r['start'] <= anchor < r['end']), None)
                if region and region['genre']['confidence'] >= .55:
                    local_route = region['genre']['primary']
            start, end = _bounds(anchor, duration, minimum, maximum, local_route, evidence.get('scene_cuts', []))
            original_end = end
            end = _activity_end(samples, transcript, start, end, anchor, minimum, local_route)
            tail_trimmed = end < original_end
            metrics = _metrics(samples, energy, transcript, start, end)
            # Never turn a short cut into an excerpt of otherwise black/static media.
            if metrics['motion'] < .006 or metrics['brightness'] < .04:
                continue
            overlapping = [s for s in sentences if s['end'] > start and s['start'] < end]
            # If speech crosses a proposed boundary, retain whole recognized units
            # when they fit; otherwise reject the visual proposal and keep v3 stories.
            if overlapping:
                start = min(start, overlapping[0]['start'])
                end = max(end, overlapping[-1]['end'])
                if end - start > maximum + .01:
                    continue
            if not .3 <= end - start <= maximum + .01 or start < 0 or end > duration + .001:
                continue
            metrics = _metrics(samples, energy, transcript, start, end)
            title = f"Visual activity at {int(anchor)//60:02d}:{int(anchor)%60:02d}"
            captions = make_captions(sentences, start, end, preset='clean-editorial', emoji_enabled=False) if overlapping else []
            edit = default_edit(title, start, end, captions)
            edit.update({'spec_version': 3, 'layout': 'fit', 'caption_preset': 'clean-editorial',
                         'captions_enabled': bool(captions), 'output_resolution': settings.get('output_resolution', '720p'),
                         'contextual_emojis': False, 'emoji_frequency': 'off', 'animated_captions': False,
                         'keyword_highlights': False, 'audio_normalize': False, 'opening_hook': False, 'intensity': 'minimal'})
            visual_strength = _clamp(metrics['motion'] / .15)
            score = round(min(8.2, 4.5 + 2.5 * visual_strength + .5 * _clamp(metrics['audio_range'] / 25)), 2)
            clips.append({'title': title, 'start': round(start, 3), 'end': round(end, 3), 'duration': round(end - start, 3),
                          'summary': 'Measured visual activity with surrounding source context. Review event relevance and narrative continuity.',
                          'hook': 'Observed visual change; semantic importance requires review.', 'score': round(score * 10),
                          'score_explanation': 'Local measured image/audio activity, not event recognition or a virality probability.',
                          'segment_ids': list(dict.fromkeys(sid for s in overlapping for sid in s.get('segment_ids', []))),
                          'provenance': VERSION, 'edit': edit,
                          '_activity_tail_trimmed': tail_trimmed,
                          'editorial': {'version': VERSION, 'score': score, 'rank': 0,
                                        'dimensions': {'visual_activity': {'score': round(visual_strength * 10, 2),
                                                                        'evidence': f"Mean sampled image change {metrics['motion']:.4f}; {metrics['cuts']} possible cuts."}},
                                        'explanation': 'A local activity proposal with setup and follow-through, not a verified semantic highlight.',
                                        'hook': 'Visual activity', 'topic': local_route, 'audience': '', 'completeness': 'review',
                                        'tags': ['Visual activity', 'Review context'], 'confidence': None,
                                        'method': 'bounded audiovisual activity heuristic; not a virality prediction',
                                        'review_indicators': ['Confirm that the activity is meaningful and that its setup/payoff remain visible.']}})
    for clip in clips:
        is_speech = id(clip) in speech_ids
        metrics = _metrics(samples, energy, transcript, clip['start'], clip['end'])
        local_genre = copy.deepcopy(genre)
        if chosen_genre == 'auto':
            at = (clip['start'] + clip['end']) / 2
            region = next((r for r in evidence.get('regions', []) if r['start'] <= at < r['end']), None)
            if region and region['genre']['confidence'] >= .55:
                local_genre = copy.deepcopy(region['genre'])
        effective = local_genre['primary']
        if effective in ('gaming', 'cinematic', 'sports', 'visual', 'education'):
            clip['edit']['layout'] = 'fit'
        if effective in ('gaming', 'cinematic', 'sports', 'visual', 'education'):
            clip['edit'].update(contextual_emojis=False, emoji_frequency='off', animated_captions=False,
                                caption_preset='clean-editorial', opening_hook=False, intensity='minimal')
        if effective in ('gaming', 'cinematic', 'sports', 'visual'):
            clip['edit']['audio_normalize'] = False
        base = clip['editorial']['score']
        # Genre-relative reranking is explicit and source-relative, while speech
        # routes preserve their established dialogue quality criteria.
        if is_speech and effective in ('gaming', 'sports', 'cinematic', 'visual'):
            weight = .22 if effective in ('gaming', 'sports', 'visual') else .12
            visual = min(10., metrics['motion'] / .15 * 10)
            clip['editorial']['base_speech_score'] = base
            clip['editorial']['score'] = round((1 - weight) * base + weight * visual, 2)
            clip['editorial']['dimensions']['visual_activity'] = {'score': round(visual, 2), 'weight': weight,
                                                                  'evidence': f"Mean sampled image change {metrics['motion']:.4f}; genre route {effective}."}
            clip['score'] = round(clip['editorial']['score'] * 10)
        clip['intelligence'] = {'version': VERSION, 'genre': local_genre,
                                'basis': 'speech' if is_speech else ('mixed' if metrics['speech_coverage'] else 'visual'),
                                'observed_signals': _observations(samples, energy, clip['start'], clip['end'], metrics['speech_coverage']),
                                'limitations': list(LIMITATIONS),
                                'boundary_note': ('Recognized speech boundaries retained.' if is_speech else
                                                  'A sustained low-motion ending was shortened, retaining a closing beat. Review setup and payoff.' if clip.pop('_activity_tail_trimmed', False) else
                                                  'Context surrounds a measured activity peak. Check setup and payoff; no story semantics were inferred.')}
    clips = [c for c in clips if 0 <= _finite(c.get('start'), -1) < _finite(c.get('end'), -1) <= duration + .001
             and .3 <= c['end'] - c['start'] <= maximum + .01]
    clips.sort(key=lambda c: (-c['editorial']['score'], c['start']))
    selected = []
    for clip in clips:
        if any(_overlap(clip, other) >= .50 for other in selected):
            continue
        selected.append(clip)
        if len(selected) >= count:
            break
    for rank, clip in enumerate(selected, 1):
        clip['editorial']['rank'] = rank
    return selected

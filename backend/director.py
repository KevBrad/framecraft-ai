"""Bounded, scene-aware visual composition; deliberately no speaker attribution.

Coordinates refer to the complete source frame, normalized to [0, 1]. Haar
frontal-face detections are observations, not identities or speaking evidence.
The renderer may retain old ``samples/time/x/y`` data; new samples add ``cut``
so interpolation can hold the preceding shot instead of panning across a cut.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

VERSION = "scene-director-v3.1"
MAX_SAMPLES = 2400
MAX_DETECTION_WIDTH = 640
DEADZONE = 0.035
MAX_VELOCITY = 0.12


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _finite(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def sample_times(duration: float, intervals=None, limit: int = MAX_SAMPLES) -> tuple[list[float], float]:
    """Merge candidate coverage before sampling. Work and result size are capped."""
    duration = _finite(duration)
    limit = max(2, min(MAX_SAMPLES, int(limit)))
    if duration <= 0:
        return [], 0.5
    bounds = []
    for interval in (intervals[:256] if intervals is not None else [(0, duration)]):
        if len(interval) != 2:
            continue
        start, end = (_clamp(_finite(v), 0, duration) for v in interval)
        if end > start:
            bounds.append((start, end))
    merged = []
    for start, end in sorted(bounds):
        if merged and start <= merged[-1][1] + .001:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    if not merged:
        return [], .5
    total = sum(end - start for start, end in merged)
    # Whole-source discovery has a smaller budget. Candidate intervals receive
    # 2fps until the global budget requires a coarser explicitly reported step.
    budget = limit if intervals is not None else min(limit, 360)
    step = max(.5, total / max(1, budget - 2 * len(merged)))
    times = set()
    for start, end in merged:
        for index in range(min(budget, math.ceil((end - start) / step))):
            times.add(round(min(start + index * step, duration - .05), 3))
        times.add(round(max(0, min(end, duration - .05)), 3))
    times = sorted(max(0, t) for t in times)
    if len(times) > budget:
        # Unusually numerous disjoint intervals still never allocate unbounded
        # video samples. Keep their chronological extent, including endpoints.
        times = [times[round(i * (len(times) - 1) / (budget - 1))] for i in range(budget)]
    return times, step


class CompositionTracker:
    """Geometric continuity with dead zone and bounded motion inside each shot."""
    def __init__(self):
        self.x = self.y = .5
        self.last_time = None
        self.last_seen = -100.0
        self.target = None
        self.pending = None
        self.pending_count = 0

    def update(self, timestamp: float, faces: list[dict], cut: bool = False) -> dict:
        gap = self.last_time is None or timestamp - self.last_time > 4
        reset = gap or cut
        dt = max(.001, timestamp - self.last_time) if self.last_time is not None else .5
        if reset:
            self.target, self.pending, self.pending_count = None, None, 0
        selected = None
        if faces:
            anchor = self.target if self.target is not None else (.5, .42)
            # Selection is nearest the tracked position, never greatest area.
            selected = min(faces, key=lambda box: math.hypot(box['cx'] - anchor[0], box['cy'] - anchor[1]))
            position = (selected['cx'], selected['cy'])
            distant = self.target is not None and math.dist(position, self.target) > .23
            if distant and not reset:
                self.pending_count = self.pending_count + 1 if self.pending and math.dist(position, self.pending) < .12 else 1
                self.pending = position
                if self.pending_count < 2:
                    selected = None
            if selected is not None:
                self.target, self.last_seen = position, timestamp
                self.pending, self.pending_count = None, 0
        if selected is not None:
            # A small downward offset leaves headroom without cutting off chins.
            target_x, target_y = selected['cx'], _clamp(selected['cy'] + .13)
        elif reset or timestamp - self.last_seen > 2:
            target_x = target_y = .5
            if reset:
                self.target = None
        else:
            target_x, target_y = self.x, self.y
        if reset:
            self.x, self.y = target_x, target_y
        else:
            for key, target in [('x', target_x), ('y', target_y)]:
                current = getattr(self, key)
                delta = target - current
                # Hysteresis: only correct the excess outside the safe deadzone.
                excess = math.copysign(max(0.0, abs(delta) - DEADZONE), delta)
                setattr(self, key, _clamp(current + _clamp(excess * .65, -MAX_VELOCITY * dt, MAX_VELOCITY * dt)))
        self.last_time = timestamp
        return {'time': round(timestamp, 3), 'x': round(self.x, 5), 'y': round(self.y, 5),
                'cut': bool(cut), 'reset': reset, 'faces': faces}


def _scene_signature(cv2, frame):
    small = cv2.resize(frame, (96, 54))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
    cv2.normalize(histogram, histogram, alpha=1, beta=0, norm_type=cv2.NORM_L1)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype('float32') / 255.0
    return histogram, gray


def _scene_change(cv2, previous, current):
    if previous is None:
        return False, 0.0
    distance = float(cv2.compareHist(previous[0], current[0], cv2.HISTCMP_BHATTACHARYYA))
    difference = float(abs(previous[1] - current[1]).mean())
    # Both color distribution and spatial changes are required, except extremely
    # large luminance changes. Fast motion/flash may still yield false positives.
    return (distance > .48 and difference > .12) or difference > .38, round(distance, 4)


def _fallback(reason):
    return {'version': VERSION, 'method': 'center-fallback',
            'samples': [{'time': 0, 'x': .5, 'y': .5, 'cut': False, 'faces': []}],
            'reason': reason, 'recommended_layout': 'fit', 'detected_samples': 0,
            'speaker_attribution': 'unavailable'}


def analyze_framing(video: Path, metadata: dict | float, intervals=None,
                    report: Callable[[str], None] | None = None) -> dict:
    """Analyze a proxy or source with bounded frames/working resolution.

    ``metadata`` may be a duration for a small compatibility wrapper. Decoder
    input is an already validated local media file; no network URLs are accepted.
    """
    duration = metadata.get('duration', 0) if isinstance(metadata, dict) else metadata
    times, step = sample_times(duration, intervals)
    if not times:
        return _fallback('No valid candidate video intervals to analyze.')
    if not Path(video).is_file():
        return _fallback('Local video is unavailable; use fit or manual composition.')
    try:
        import cv2
    except ImportError:
        return _fallback('OpenCV unavailable; use fit or manual composition.')
    cv2.setNumThreads(2)
    classifier_type = getattr(cv2, 'CascadeClassifier', None) or getattr(getattr(cv2, 'xobjdetect', None), 'CascadeClassifier', None)
    if classifier_type is None or not hasattr(cv2, 'data'):
        return _fallback('OpenCV frontal-face detector unavailable; use fit or manual composition.')
    detector = classifier_type(str(Path(cv2.data.haarcascades) / 'haarcascade_frontalface_default.xml'))
    if detector.empty():
        return _fallback('Frontal-face model unavailable; use fit or manual composition.')
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        capture.release()
        return _fallback('Video decoder unavailable; use fit or manual composition.')
    tracker, previous, previous_time = CompositionTracker(), None, None
    samples, cuts, detected = [], [], 0
    width = height = 0
    try:
        for index, timestamp in enumerate(times):
            if report and (index % 8 == 0 or index == len(times) - 1):
                report(f'Analyzing scene-aware framing · {index + 1}/{len(times)} samples')
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            if max(width, height) > MAX_DETECTION_WIDTH:
                scale = MAX_DETECTION_WIDTH / max(width, height)
                frame = cv2.resize(frame, (max(2, round(width * scale)), max(2, round(height * scale))))
            fh, fw = frame.shape[:2]
            signature = _scene_signature(cv2, frame)
            cut, score = _scene_change(cv2, previous if previous_time is not None and timestamp - previous_time <= max(1.1, step * 1.5) else None, signature)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            boxes = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                              minSize=(max(20, fw // 32), max(20, fh // 32)))
            faces = [{'x': round(float(x) / fw, 5), 'y': round(float(y) / fh, 5),
                      'w': round(float(w) / fw, 5), 'h': round(float(h) / fh, 5),
                      'cx': round(float(x + w / 2) / fw, 5), 'cy': round(float(y + h / 2) / fh, 5)}
                     for x, y, w, h in boxes[:16]]
            sample = tracker.update(timestamp, faces, cut)
            sample['scene_difference'] = score
            samples.append(sample)
            detected += bool(faces)
            if cut:
                cuts.append(round(timestamp, 3))
            previous, previous_time = signature, timestamp
    finally:
        capture.release()
    if not samples:
        return _fallback('No decodable sampled frames; use fit or manual composition.')
    result = {'version': VERSION, 'method': 'face-aware' if detected else 'center-fallback',
              'samples': samples, 'scene_cuts': cuts, 'detected_samples': detected,
              'sample_interval': round(step, 4), 'sample_budget': MAX_SAMPLES,
              'source_aspect': width / max(1, height), 'speaker_attribution': 'unavailable',
              'reason': 'Scene-reset geometric face continuity with deadzone and bounded motion; no speaker identity or active-speaker inference.',
              'diagnostics': trajectory_metrics(samples)}
    result['recommended_layout'] = recommended_layout(result, 0, _finite(duration))
    return result


def recommended_layout(framing: dict, start: float, end: float, aspect_ratio='9:16') -> str:
    """Preserve wide groups/general visuals rather than guess a speaking face."""
    if framing.get('version') != VERSION:
        return 'smart'  # Persisted legacy clips retain their existing behavior.
    samples = [s for s in framing.get('samples', []) if start <= s.get('time', 0) <= end]
    if not samples or framing.get('method') != 'face-aware':
        return 'fit'
    try:
        rw, rh = (float(v) for v in aspect_ratio.split(':'))
        source_aspect = max(.01, framing.get('source_aspect', 16 / 9))
        visible_width = min(1.0, (rw / rh) / source_aspect)
        visible_height = min(1.0, source_aspect / (rw / rh))
    except (ValueError, ZeroDivisionError):
        visible_width, visible_height = .3164, 1.0
    # A face box must fit entirely with breathing room. Multiple widely spread
    # faces require a full frame: geometric detection is not speaking evidence.
    unsafe = 0
    for sample in samples:
        faces = sample.get('faces', [])
        if not faces:
            unsafe += 1
        else:
            center_x = _clamp(sample.get('x', .5), visible_width / 2, 1 - visible_width / 2)
            center_y = _clamp(sample.get('y', .5), visible_height / 2, 1 - visible_height / 2)
            left, right = center_x - visible_width / 2, center_x + visible_width / 2
            top, bottom = center_y - visible_height / 2, center_y + visible_height / 2
            # The actual smoothed crop, not just the target/face size, must
            # contain observed faces. Fast movement or a drifting group can
            # otherwise pass a size-only check while showing half a face.
            unsafe += any(f['x'] < left + (.025 if left > 0 else 0) or
                          f['x'] + f['w'] > right - (.025 if right < 1 else 0) or
                          f['y'] < top + (.02 if top > 0 else 0) or
                          f['y'] + f['h'] > bottom - (.02 if bottom < 1 else 0)
                          for f in faces)
    return 'fit' if unsafe / len(samples) >= .35 else 'smart'


def trajectory_metrics(samples):
    velocities, steps, reversals = [], [], 0
    previous_delta = 0
    for left, right in zip(samples, samples[1:]):
        dt = right['time'] - left['time']
        if dt <= 0 or right.get('cut') or right.get('reset'):
            previous_delta = 0
            continue
        delta = right['x'] - left['x']
        steps.append(abs(delta))
        velocities.append(abs(delta) / dt)
        if abs(delta) > .003 and abs(previous_delta) > .003 and delta * previous_delta < 0:
            reversals += 1
        previous_delta = delta
    steps.sort()
    return {'within_shot_max_x_velocity': round(max(velocities, default=0), 6),
            'within_shot_mean_x_step': round(sum(steps) / max(1, len(steps)), 6),
            'within_shot_p95_x_step': round(steps[min(len(steps) - 1, math.floor(.95 * len(steps)))] if steps else 0, 6),
            'within_shot_x_direction_reversals': reversals,
            'cut_resets': sum(bool(s.get('cut')) for s in samples)}


def trajectory_expression(framing: dict, key: str, start: float, end: float, dimension: int, crop: int) -> str:
    """FFmpeg pixel trajectory with a hold/step at shot cuts, <=64 knots.

    Frame timestamps are relative to a setpts=PTS-STARTPTS clip. For v3 samples
    a right-hand cut must never interpolate from the previous shot's subject.
    """
    if dimension <= crop:
        return '0'
    samples = sorted((s for s in framing.get('samples', []) if math.isfinite(_finite(s.get('time'), math.nan))), key=lambda s: s['time'])
    if not samples:
        return '0'

    def interpolate(timestamp):
        if timestamp <= samples[0]['time']:
            return _clamp(_finite(samples[0].get(key), .5))
        for left, right in zip(samples, samples[1:]):
            if timestamp < right['time']:
                a, b = _clamp(_finite(left.get(key), .5)), _clamp(_finite(right.get(key), .5))
                return a if right.get('cut') or right.get('reset') else a + (b - a) * (timestamp - left['time']) / max(.001, right['time'] - left['time'])
        return _clamp(_finite(samples[-1].get(key), .5))

    points = [{'time': start, key: interpolate(start), 'cut': False}] + [s for s in samples if start < s['time'] < end] + [{'time': end, key: interpolate(end), 'cut': False}]
    if len(points) > 64:
        keep = {0, len(points) - 1} | {i for i, p in enumerate(points) if p.get('cut') or p.get('reset')}
        if len(keep) > 64:
            # Highly cut-heavy material should be fit; a bounded center crop is
            # still safer than a huge parser expression if called explicitly.
            return f'{max(0, (dimension - crop) / 2):.3f}'
        for i in range(64):
            if len(keep) >= 64:
                break
            keep.add(round(i * (len(points) - 1) / 63))
        points = [points[i] for i in sorted(keep)]
    pixel = lambda p: _clamp(_finite(p.get(key), .5) * dimension - crop / 2, 0, dimension - crop)
    expression = f'{pixel(points[-1]):.3f}'
    for left, right in reversed(list(zip(points, points[1:]))):
        t1, t2 = left['time'] - start, right['time'] - start
        a, b = pixel(left), pixel(right)
        line = f'{a:.3f}' if right.get('cut') or right.get('reset') else f'({a:.3f}+({b-a:.3f})*(t-{t1:.3f})/{max(.001,t2-t1):.3f})'
        expression = f'if(lt(t,{t2:.3f}),{line},{expression})'
    return expression

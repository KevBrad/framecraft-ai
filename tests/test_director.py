"""Shot resets and composition safety, with real decoded fixture coverage."""
import math
from pathlib import Path

import pytest

from backend.director import (CompositionTracker, MAX_SAMPLES, MAX_VELOCITY, VERSION,
                              analyze_framing, recommended_layout, sample_times,
                              trajectory_expression, trajectory_metrics)


def face(cx, cy=.35, width=.12):
    return {'cx': cx, 'cy': cy, 'x': cx-width/2, 'y': cy-.1, 'w': width, 'h': .2}


def test_micro_motion_is_held_in_deadzone():
    tracker = CompositionTracker()
    samples = [tracker.update(i*.5, [face(.5 + (.01 if i % 2 else -.01))]) for i in range(20)]
    assert len({s['x'] for s in samples}) == 1


def test_motion_bounded_inside_shot():
    tracker = CompositionTracker()
    samples = [tracker.update(i*.5, [face(.2 + i*.025)]) for i in range(22)]
    assert samples[-1]['x'] - samples[0]['x'] > .35
    assert trajectory_metrics(samples)['within_shot_max_x_velocity'] <= MAX_VELOCITY + .0001


def test_cut_resets_without_old_shot_lag():
    tracker = CompositionTracker()
    tracker.update(0, [face(.2)])
    after = tracker.update(.5, [face(.8)], cut=True)
    assert after['x'] == .8 and after['cut']


def test_background_face_size_does_not_override_continuity():
    tracker = CompositionTracker()
    tracker.update(0, [face(.4)])
    sample = tracker.update(.5, [face(.41), face(.8, width=.38)])
    assert sample['x'] == .4


def test_distant_switch_requires_confirmation():
    tracker = CompositionTracker()
    tracker.update(0, [face(.2)])
    first = tracker.update(.5, [face(.8)])
    second = tracker.update(1, [face(.79)])
    assert first['x'] == .2
    assert .2 < second['x'] <= .26


def test_gaps_do_not_follow_stale_subject():
    tracker = CompositionTracker()
    tracker.update(0, [face(.2)])
    assert tracker.update(30, [face(.8)])['x'] == .8


def test_disappearing_subject_hold_then_slow_center():
    tracker = CompositionTracker()
    tracker.update(0, [face(.2)])
    assert tracker.update(1, [])['x'] == .2
    sample = tracker.update(3, [])
    assert .2 < sample['x'] < .5


@pytest.mark.parametrize('duration', [900, 3600, 99999999])
def test_whole_source_sampling_remains_bounded(duration):
    times, step = sample_times(duration)
    assert 2 <= len(times) <= 360
    assert times == sorted(set(times))
    assert all(0 <= t < duration for t in times)


def test_overlapping_candidates_merge_and_do_not_sample_gaps():
    times, step = sample_times(3600, [(100, 130), (110, 140), (1000, 1030)])
    assert len(times) < 150
    assert all(100 <= t <= 140 or 1000 <= t <= 1030 for t in times)
    assert step == .5


def test_candidate_budget_is_hard_limit():
    times, _ = sample_times(3600, [(0, 3600)])
    assert len(times) <= MAX_SAMPLES


def test_invalid_intervals_return_no_samples():
    assert sample_times(10, [(20, 30), (8, 2)])[0] == []
    assert sample_times(math.nan)[0] == []


def test_wide_multi_person_prefers_fit_without_speaker_guess():
    framing = {'version': VERSION, 'method': 'face-aware', 'source_aspect': 16/9,
               'samples': [{'time': i, 'faces': [face(.2), face(.8)]} for i in range(4)]}
    assert recommended_layout(framing, 0, 3) == 'fit'
    assert recommended_layout(framing, 0, 3, '16:9') == 'smart'


def test_safe_single_face_prefers_smart_and_legacy_unchanged():
    framing = {'version': VERSION, 'method': 'face-aware', 'source_aspect': 16/9,
               'samples': [{'time': i, 'faces': [face(.5)]} for i in range(4)]}
    assert recommended_layout(framing, 0, 3) == 'smart'
    assert recommended_layout({'method': 'face-aware'}, 0, 3) == 'smart'


def test_large_face_and_general_visuals_prefer_full_frame():
    framing = {'version': VERSION, 'method': 'face-aware', 'source_aspect': 16/9,
               'samples': [{'time': 0, 'faces': [face(.5, width=.4)]}]}
    assert recommended_layout(framing, 0, 3) == 'fit'
    framing['samples'][0]['faces'] = []
    assert recommended_layout(framing, 0, 3) == 'fit'


def test_motion_lag_cannot_recommend_half_face_crop():
    framing = {'version': VERSION, 'method': 'face-aware', 'source_aspect': 16/9,
               'samples': [{'time': 0, 'x': .2, 'y': .5, 'faces': [face(.8)]}]}
    assert recommended_layout(framing, 0, 3) == 'fit'


def test_expression_holds_preceding_shot_then_jumps_at_cut():
    framing = {'samples': [{'time': 0, 'x': .2}, {'time': 1, 'x': .8, 'cut': True}, {'time': 2, 'x': .8}]}
    expr = trajectory_expression(framing, 'x', 0, 2, 1000, 200)
    assert 'if(lt(t,1.000),100.000,' in expr
    assert '(600.000)*(t-0.000)' not in expr


def test_expression_remains_bounded_and_cuts_retained():
    samples = [{'time': i*.5, 'x': .4, 'cut': i == 70} for i in range(200)]
    expr = trajectory_expression({'samples': samples}, 'x', 0, 100, 1920, 608)
    assert expr.count('if(') <= 63
    assert 'lt(t,35.000)' in expr
    assert trajectory_expression({'samples': samples}, 'x', 0, 100, 100, 100) == '0'


def test_missing_video_is_honest_fallback(tmp_path):
    result = analyze_framing(tmp_path / 'absent.mp4', 4)
    assert result['method'] == 'center-fallback'
    assert result['recommended_layout'] == 'fit'
    assert result['speaker_attribution'] == 'unavailable'


def test_real_decoded_motion_fixture():
    fixture = Path(__file__).parent / 'artifacts' / 'face-motion-proxy.mp4'
    if not fixture.is_file():
        pytest.skip('Real motion fixture is generated by the existing face tracking regression.')
    result = analyze_framing(fixture, 4, [(0, 4)])
    assert result['method'] == 'face-aware', result
    assert result['detected_samples'] >= 5
    assert result['diagnostics']['within_shot_max_x_velocity'] <= MAX_VELOCITY + .0001
    assert result['samples'][-1]['x'] > result['samples'][0]['x'] + .04
    assert len(result['samples']) <= MAX_SAMPLES

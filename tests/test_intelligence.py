"""Numerical/synthetic contracts, not claims of human editorial quality.

The optional real-media test uses separately downloaded licensed fixtures; it
does not require redistributing those assets or downloading them during CI.
"""
import copy
import json
import math
import os
from pathlib import Path

import pytest

from backend import intelligence as engine
from backend.schemas import EditSpec


def timeline(duration=60, motion=.025):
    step=engine.sample_interval(duration)
    return [{'time':round(i*step,5),'motion':motion,'scene_delta':0.,'brightness':.5,'detail':.3,'cut':False}
            for i in range(min(engine.MAX_SAMPLES,math.ceil(duration/step)))]


def evidence(samples, transcript=None, duration=60):
    tr=transcript or {'segments':[]}
    return {'version':engine.VERSION,'samples':samples,'genre':engine.classify(samples,tr,duration),'scene_cuts':[],'regions':[]}


def settings(**kwargs):
    return {'analysis_version':4,'content_genre':'visual','min_duration':8,'max_duration':18,'requested_clips':4,**kwargs}


@pytest.mark.parametrize('duration',[.3,2,60,3600,86400])
def test_sampling_budget_and_coverage(duration):
    samples=timeline(duration)
    assert 1<=len(samples)<=6000
    assert engine._valid_samples(samples,duration)


@pytest.mark.parametrize('mutation',[
    lambda s:s[:3],
    lambda s:[{**s[0],'time':-.1},*s[1:]],
    lambda s:[s[0],s[0],*s[2:]],
    lambda s:[{**s[0],'motion':float('nan')},*s[1:]],
    lambda s:[{**s[0],'cut':1},*s[1:]],
    lambda s:[{**s[0],'brightness':1.1},*s[1:]],
    lambda s:s*60,
])
def test_rejects_poisoned_or_incomplete_pixel_cache(mutation):
    assert not engine._valid_samples(mutation(timeline()),60)


def test_speech_coverage_unions_overlapping_intervals():
    assert engine._speech_coverage([{'start':-2,'end':8},{'start':2,'end':12}],0,10)==1
    assert engine._speech_coverage([{'start':2,'end':4},{'start':3,'end':5}],0,10)==.3


def test_automatic_visual_route_does_not_claim_game_or_sport_identity():
    genre=engine.classify(timeline(),{'segments':[]},60)
    assert genre['primary']=='visual' and genre['confidence']<1
    assert 'gaming' not in genre['labels'] and 'sports' not in genre['labels']


def test_mixed_regions_can_change_route_without_filename_metadata():
    samples=timeline(60,motion=.002)
    for sample in samples:
        if sample['time']>=30:sample['motion']=.09
    transcript={'segments':[{'start':0,'end':25,'text':'This lesson explains the science evidence because the example helps us learn.'}]}
    assert engine.classify(samples,transcript,60,0,30)['primary']=='education'
    assert engine.classify(samples,transcript,60,30,60)['primary']=='visual'


def test_manual_override_does_not_poison_subsequent_automatic_replay():
    e=evidence(timeline())
    manual=engine.select_clips({'segments':[]},60,settings(content_genre='gaming'),e)
    assert manual and manual[0]['intelligence']['genre']['source']=='user-override'
    automatic=engine.select_clips({'segments':[]},60,settings(content_genre='auto'),e)
    assert automatic and automatic[0]['intelligence']['genre']['primary']=='visual'
    assert automatic[0]['intelligence']['genre']['source']=='automatic'


def test_silent_activity_proposals_have_real_bounds_edit_contract_and_diversity():
    samples=timeline(80)
    for i,sample in enumerate(samples):
        if 20<=sample['time']<=25 or 52<=sample['time']<=58:sample['motion']=.10+(i%3)*.02
    clips=engine.select_clips({'segments':[]},80,settings(),evidence(samples,duration=80))
    assert clips and len(clips)<=4
    from backend.pipeline import _overlap
    for i,clip in enumerate(clips):
        assert 0<=clip['start']<clip['end']<=80
        assert 8<=clip['duration']<=18
        assert clip['editorial']['rank']==i+1
        assert not clip['edit']['captions'] and not clip['edit']['captions_enabled']
        assert clip['edit']['layout']=='fit'
        assert not clip['edit']['contextual_emojis']
        assert clip['intelligence']['observed_signals']
        assert 'kill' not in clip['title'].lower()
        EditSpec.model_validate(clip['edit'])
        assert all(_overlap(clip,previous)<.5 for previous in clips[:i])


@pytest.mark.parametrize('motion,brightness,detail',[(0,.5,.2),(.5,0,.2),(.2,.5,0)])
def test_static_black_or_uniform_frames_do_not_generate_fillers(motion,brightness,detail):
    samples=timeline(motion=motion)
    for s in samples:s.update(brightness=brightness,detail=detail)
    assert engine.select_clips({'segments':[]},60,settings(),evidence(samples))==[]


def test_custom_text_prompt_cannot_be_satisfied_by_arbitrary_motion():
    e=evidence(timeline(motion=.1))
    assert engine.select_clips({'segments':[]},60,settings(discovery_mode='custom_prompt',custom_prompt='winning goal'),e)==[]


def test_short_visual_source_and_end_anchors_stay_inside_source():
    for duration in (1.,2.7,8.,19.1):
        samples=timeline(duration,motion=.1)
        clips=engine.select_clips({'segments':[]},duration,settings(min_duration=15,max_duration=45),evidence(samples,duration=duration))
        assert clips
        assert all(0<=c['start']<c['end']<=duration+.001 for c in clips)


def test_cinematic_envelope_uses_whole_cut_boundaries_when_duration_allows():
    start,end=engine._bounds(19,60,8,30,'cinematic',[5,15,30,45])
    assert (start,end)==(5,30)
    start,end=engine._bounds(1,60,8,18,'cinematic',[5,15,30,45])
    assert 0<=start<end<=60 and end-start<=18


def test_audio_evidence_is_measured_and_never_assigned_event_identity():
    observed=engine._observations(timeline(),[{'start':10,'end':10.5,'dbfs':-6},{'start':11,'end':11.5,'dbfs':-30}],8,18,0)
    audio=next(x for x in observed if x['kind']=='audio')
    assert audio['time']==10 and audio['value']==-6
    assert 'no sound-event identity' in audio['description']


@pytest.fixture(scope='module')
def synthetic_video(tmp_path_factory):
    from backend.config import configure
    from backend.pipeline import _binary,_run
    configure()
    path=tmp_path_factory.mktemp('visual-fixture')/'moving-pattern.mp4'
    _run([_binary('ffmpeg'),'-v','error','-nostdin','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=8:duration=6',
          '-an','-c:v','libx264','-threads','2','-pix_fmt','yuv420p',str(path)])
    return path


def test_real_decoder_cache_hit_and_corrupt_prefix_recovery(synthetic_video,tmp_path,monkeypatch):
    meta={'duration':6}
    first=engine.analyze_evidence(synthetic_video,tmp_path,meta,{'segments':[]},lambda _:None)
    assert first['sample_count']>=10 and not first['diagnostics']['visual_cache_hit']
    original=engine._visual_samples
    def forbidden(*args):raise AssertionError('Decoder reran on a valid cache')
    monkeypatch.setattr(engine,'_visual_samples',forbidden)
    second=engine.analyze_evidence(synthetic_video,tmp_path,meta,{'segments':[]},lambda _:None)
    assert second['diagnostics']['visual_cache_hit']
    cached=tmp_path/'multimodal-visual-v4.json';data=json.loads(cached.read_text());data['samples']=data['samples'][:1];cached.write_text(json.dumps(data))
    monkeypatch.setattr(engine,'_visual_samples',original)
    repaired=engine.analyze_evidence(synthetic_video,tmp_path,meta,{'segments':[]},lambda _:None)
    assert not repaired['diagnostics']['visual_cache_hit'] and repaired['sample_count']>=10


def test_cache_invalidation_follows_source_identity(synthetic_video,tmp_path,monkeypatch):
    source=tmp_path/'source.mp4';source.write_bytes(synthetic_video.read_bytes())
    engine.analyze_evidence(source,tmp_path,{'duration':6},{'segments':[]},lambda _:None)
    stat=source.stat();os.utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
    result=engine.analyze_evidence(source,tmp_path,{'duration':6},{'segments':[]},lambda _:None)
    assert not result['diagnostics']['visual_cache_hit']


def test_cancel_callback_is_not_swallowed_and_cache_not_published(synthetic_video,tmp_path):
    class Cancelled(Exception):pass
    def stop(_):raise Cancelled('Test interruption')
    with pytest.raises(Cancelled):engine.analyze_evidence(synthetic_video,tmp_path,{'duration':6},{'segments':[]},stop)
    assert not (tmp_path/'multimodal-visual-v4.json').exists()


def test_invalid_video_fails_clearly(tmp_path):
    from backend.pipeline import PipelineError
    source=tmp_path/'broken.mp4';source.write_bytes(b'not video')
    with pytest.raises(PipelineError,match='could not open'):engine.analyze_evidence(source,tmp_path,{'duration':6},{'segments':[]},lambda _:None)


def test_recognized_speech_engine_remains_available_without_reranking(monkeypatch):
    from backend import editorial
    from backend.pipeline import default_edit
    original={'start':5.,'end':15.,'duration':10.,'title':'A complete thought','score':70,'edit':default_edit('A complete thought',5,15,[]),
              'editorial':{'score':7.,'rank':1,'dimensions':{}}}
    monkeypatch.setattr(editorial,'discover',lambda *args,**kwargs:[copy.deepcopy(original)])
    clips=engine.select_clips({'segments':[]},60,settings(content_genre='podcast'),evidence(timeline(motion=0)))
    assert clips[0]['editorial']['score']==7 and clips[0]['intelligence']['basis']=='speech'


def test_invalid_speech_cache_boundaries_are_not_exported(monkeypatch):
    from backend import editorial
    from backend.pipeline import default_edit
    bad={'start':50.,'end':65.,'duration':15.,'title':'Invalid','score':70,'edit':default_edit('Invalid',50,65,[]),'editorial':{'score':7.,'rank':1,'dimensions':{}}}
    monkeypatch.setattr(editorial,'discover',lambda *args,**kwargs:[copy.deepcopy(bad)])
    assert engine.select_clips({'segments':[]},60,settings(content_genre='podcast'),evidence(timeline(motion=0)))==[]



@pytest.mark.parametrize('genre',['gaming','sports','visual'])
def test_activity_tail_ends_after_sustained_quiet_without_discarding_speech(genre):
    samples=timeline(60,motion=.12)
    for sample in samples:
        if sample['time']>=35:sample['motion']=.006
    assert engine._activity_end(samples,{'segments':[]},25,48,30,8,genre)==36
    speech={'segments':[{'start':40,'end':42,'text':'An ending must remain.'}]}
    assert engine._activity_end(samples,speech,25,48,30,8,genre)==48


def test_activity_tail_preserves_brief_pause_cinematic_and_minimum_length():
    samples=timeline(60,motion=.12)
    for sample in samples:
        if 35<=sample['time']<37:sample['motion']=.006
    assert engine._activity_end(samples,{'segments':[]},25,48,30,8,'gaming')==48
    for sample in samples:
        if sample['time']>=35:sample['motion']=.006
    assert engine._activity_end(samples,{'segments':[]},25,48,30,8,'cinematic')==48
    assert engine._activity_end(samples,{'segments':[]},25,48,30,18,'gaming')==43


def test_isolated_large_transitions_do_not_beat_sustained_visual_activity():
    samples=timeline(60,motion=.001)
    for sample in samples:
        if sample['time'] in (5,10,15):sample['motion']=.4
        if 30<=sample['time']<=45:sample['motion']=.10+(.04 if sample['time']==36 else 0)
    clips=engine.select_clips({'segments':[]},60,settings(content_genre='gaming'),evidence(samples))
    assert clips and clips[0]['start']>=25
    assert all(c['start']>=20 for c in clips)

"""Real media integration and bounded fault injection; fixtures are synthetic."""
from pathlib import Path
import pytest
from backend.config import configure
from backend import pipeline
from backend.schemas import AnalysisSettings,EditSpec
configure()

@pytest.fixture(scope='module')
def media(tmp_path_factory):
    root=tmp_path_factory.mktemp('multimodal-integration')
    for name,visual,audio in [('moving','testsrc2=size=320x180:rate=15',False),('tone','testsrc2=size=320x180:rate=15',True),('static','color=c=gray:s=320x180:r=15',False)]:
        command=[pipeline._binary('ffmpeg'),'-v','error','-nostdin','-y','-f','lavfi','-i',visual]
        if audio:command+=['-f','lavfi','-i','sine=frequency=440:sample_rate=16000']
        command+=['-t','6','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p']
        if audio:command+=['-c:a','aac']
        command+=[str(root/(name+'.mp4'))]
        pipeline._run(command)
    return root

SETTINGS=AnalysisSettings(analysis_version=4,content_genre='visual',min_duration=3,max_duration=5,requested_clips=2).model_dump()

def test_no_audio_actual_analysis_edit_render_and_cache(media,tmp_path):
    source=media/'moving.mp4'
    with pytest.raises(pipeline.PipelineError,match='no audio'):
        pipeline.analyze(source,tmp_path/'legacy',{'analysis_version':3},lambda _:None)
    result=pipeline.analyze(source,tmp_path/'v4',SETTINGS,lambda _:None)
    assert result['clips'] and not result['transcript']['segments']
    edit=EditSpec.model_validate(result['clips'][0]['edit']).model_dump()
    assert edit['layout']=='fit' and not edit['captions_enabled']
    edit.update(title='Reviewed visual clip',output_resolution='720p')
    output=tmp_path/'actual.mp4'
    checked=pipeline.render(source,output,edit,result['framing'])
    assert checked['decode_verified'] and not checked['has_audio']
    assert (checked['width'],checked['height'])==(720,1280)
    repeat=pipeline.analyze(source,tmp_path/'v4',SETTINGS,lambda _:None)
    assert repeat['metadata']['intelligence']['diagnostics']['visual_cache_hit']

def test_no_speech_is_distinct_from_failed_asr(media,tmp_path,monkeypatch):
    def no_speech(*args):raise pipeline.NoSpeechError('No reliable speech')
    monkeypatch.setattr(pipeline,'transcribe',no_speech)
    result=pipeline.analyze(media/'tone.mp4',tmp_path/'empty',SETTINGS,lambda _:None)
    assert result['transcript']['speech_status']=='not_recognized' and result['clips']
    def failed(*args):raise pipeline.PipelineError('Model could not load')
    monkeypatch.setattr(pipeline,'transcribe',failed)
    with pytest.raises(pipeline.PipelineError,match='Model could not load'):
        pipeline.analyze(media/'tone.mp4',tmp_path/'failed',SETTINGS,lambda _:None)

def test_static_silent_media_is_not_filled_with_fake_highlights(media,tmp_path):
    with pytest.raises(pipeline.PipelineError,match='No sufficiently supported moment'):
        pipeline.analyze(media/'static.mp4',tmp_path,SETTINGS,lambda _:None)

def test_corrupt_decode_cannot_be_certified(media,tmp_path,monkeypatch):
    source=media/'moving.mp4';info=pipeline.probe(source)
    def failed(*args,**kwargs):raise pipeline.PipelineError('Injected corrupt packet decode failure')
    monkeypatch.setattr(pipeline,'_run',failed)
    with pytest.raises(pipeline.PipelineError,match='corrupt packet'):
        pipeline.validate_export(source,info['duration'],(320,180),False)

def test_v3_default_and_strict_version_validation():
    assert AnalysisSettings().analysis_version==3
    with pytest.raises(ValueError):AnalysisSettings(analysis_version=4,content_genre='invented')

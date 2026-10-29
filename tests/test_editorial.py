"""Editorial contracts use invented test dialogue, not claimed quality evidence."""
import io,zipfile
import pytest
from backend.editorial import discover,source_map,WEIGHTS
from backend.schemas import AnalysisSettings,EditSpec
from backend.archives import zip_exports

def speech(lines):
    out=[];t=0
    for i,text in enumerate(lines):
        words=[]
        for token in text.split():
            words.append({'start':round(t,3),'end':round(t+.36,3),'word':' '+token,'probability':.94});t+=.4
        out.append({'id':str(i),'start':words[0]['start'],'end':words[-1]['end'],'text':text,'words':words});t+=.5
    return {'segments':out},t

LINES=['Why does research matter for making a clear decision?',
       'Because evidence helps us understand the reason behind each important decision.',
       'The lesson is to check the evidence before we commit our resources.',
       'I disagree with ignoring the problem because that would be a mistake.',
       'Our budget increased from 20 million dollars to 40 million dollars.',
       'That is why we learned to plan a realistic budget for the project.']

def test_source_grounded_story_scores_and_boundaries():
    tr,d=speech(LINES);clips=discover(tr,d,{'min_duration':4,'max_duration':24,'requested_clips':10})
    assert clips and len(clips)<10
    for c in clips:
        assert 0<=c['start']<c['end']<=d
        assert 4<=c['duration']<=24
        e=c['editorial'];assert 0<=e['score']<=10
        assert set(e['dimensions'])==set(WEIGHTS)
        assert abs(e['score']-sum(v['score']*WEIGHTS[k] for k,v in e['dimensions'].items()))<.02
        quote=e['title_evidence'];assert quote['source_text'] in ' '.join(LINES)
        assert 'virality prediction' in e['method']
        EditSpec.model_validate(c['edit'])

def test_questions_get_answers_and_titles_never_relabel_prime_as_youtube():
    lines=['What was your most expensive project in the past year?',
           'Our Prime Video show cost 20 million dollars because we built a city.',
           'The project was our television show and the contestants lived in that city.']
    tr,d=speech(lines);clips=discover(tr,d,{'min_duration':4,'max_duration':30})
    assert clips
    assert not any('youtube' in c['title'].lower() for c in clips)
    assert all(not c['editorial']['dimensions']['payoff']['evidence'].strip().endswith('?') for c in clips)

def test_discovery_mode_is_real_filter_and_prompts_are_only_data():
    tr,d=speech(LINES)
    assert discover(tr,d,{'min_duration':4,'max_duration':24,'discovery_mode':'strong_opinions'})
    assert not discover(tr,d,{'min_duration':4,'max_duration':24,'discovery_mode':'funny_moments'})
    assert not discover(tr,d,{'min_duration':4,'max_duration':24,'discovery_mode':'custom_prompt','custom_prompt':'volcanic kittens'})
    clips=discover(tr,d,{'min_duration':4,'max_duration':24,'discovery_mode':'custom_prompt','custom_prompt':'budget'})
    assert clips and all('budget' in (c['summary']+' '+c['editorial']['dimensions']['payoff']['evidence']).lower() for c in clips)

def test_map_is_extractively_grounded_and_bounded():
    tr,d=speech(LINES*8);context=source_map(tr,d)
    assert context['speakers']=='unattributed'
    assert len(context['segments'])<=int(d/90)+1
    assert all(s['summary'] in ' '.join(LINES) for s in context['segments'])
    assert all(len(s['summary'])<=400 for s in context['segments'])

def test_short_source_is_explicitly_reviewed_not_invented():
    tr,d=speech(['Ask not what your country can do for you.'])
    clips=discover(tr,d,{'min_duration':15,'max_duration':45})
    assert clips and clips[0]['end']<=d
    assert clips[0]['editorial']['completeness']=='review'
    assert 'shorter' in ' '.join(clips[0]['editorial']['review_indicators'])

@pytest.mark.parametrize('field,value',[('requested_clips',0),('requested_clips',21),('model_mode','secret-cloud'),('output_resolution','4k'),('discovery_mode','execute')])
def test_settings_reject_invalid_structured_values(field,value):
    with pytest.raises(ValueError):AnalysisSettings.model_validate({field:value})

def test_zip_streams_exact_files_without_staging_archive(tmp_path):
    a=tmp_path/'a.mp4';b=tmp_path/'b.mp4';a.write_bytes(b'a'*1024*1024);b.write_bytes(b'b'*10)
    chunks=list(zip_exports([('a.mp4',a),('b.mp4',b)]))
    assert max(map(len,chunks))<=256*1024
    with zipfile.ZipFile(io.BytesIO(b''.join(chunks))) as archive:
        assert archive.read('a.mp4')==a.read_bytes();assert archive.read('b.mp4')==b.read_bytes()


def test_complete_answer_retains_later_contrast_before_next_question():
    lines=['What was your most expensive video production last year?',
           'At the time we spent four million dollars on that video.',
           'The money does not really matter to our audience any more.',
           'But I will say the most expensive thing was our Prime Video show.',
           'In that television show we spent twenty million dollars building a city.',
           'The contestants lived there and we got a little carried away.',
           'What is the most popular video that you have made?']
    tr,d=speech(lines)
    clips=discover(tr,d,{'min_duration':10,'max_duration':60,'requested_clips':1})
    assert clips
    assert clips[0]['start']<.1
    assert clips[0]['end']>=tr['segments'][5]['end']
    assert 'Prime Video' in ' '.join(c['text'] for c in clips[0]['edit']['captions'])


def test_low_word_confidence_surfaces_review_instead_of_complete_claim():
    tr,d=speech(LINES)
    for s in tr['segments']:
        for w in s['words']:w['probability']=.6
    clips=discover(tr,d,{'min_duration':4,'max_duration':24})
    assert clips and all(c['editorial']['completeness']=='review' for c in clips)
    assert all(any('ASR' in item for item in c['editorial']['review_indicators']) for c in clips)


def test_abbreviation_stays_with_name_across_normal_speech_gap():
    from backend.pipeline import _sentences
    tr={'segments':[{'id':'name','start':0,'end':3,'text':'What is Mr. Beast doing?',
        'words':[{'word':' What','start':0,'end':.3},{'word':' is','start':.3,'end':.5},{'word':' Mr.','start':.5,'end':.8},
                 {'word':' Beast','start':1.5,'end':2},{'word':' doing?','start':2,'end':3}]}]}
    assert len(_sentences(tr))==1


def test_retained_legacy_style_can_render_independent_opening_hook(tmp_path):
    from backend.pipeline import ROOT,default_edit,render
    fixture=ROOT/'tests/artifacts/jfk-source.mp4'
    edit=default_edit('A clear source quote',0,2,[])
    edit.update(caption_preset='boxed',captions_enabled=False,opening_hook=True,layout='fit')
    output=tmp_path/'legacy-hook.mp4'
    metadata=render(fixture,output,edit)
    assert output.stat().st_size>1000 and metadata['video_codec']=='h264'


def test_source_link_is_idempotent_and_backfill_preserves_explicit_binding(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from types import SimpleNamespace
    from backend.database import Base,SourceLink,Project
    from backend import media_library
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(SourceLink(youtube_id='TestVideo01',source_id='explicit-source',created_at='2020-original'))
        db.flush()
        media_library.link_youtube(db,SimpleNamespace(id='explicit-source'),'TestVideo01')
        assert db.get(SourceLink,'TestVideo01').created_at=='2020-original'
        p=Project(id='test-project',title='legacy',filename='test.mp4',youtube_url='https://youtu.be/TestVideo01',media={'provenance':{'rights_confirmed':True}})
        db.add(p);db.flush()
        monkeypatch.setattr(media_library,'register_project',lambda db,p:SimpleNamespace(id='legacy-project-original'))
        media_library.backfill(db)
        assert db.get(SourceLink,'TestVideo01').source_id=='explicit-source'
        assert db.get(SourceLink,'TestVideo01').created_at=='2020-original'

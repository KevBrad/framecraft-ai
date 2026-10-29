"""Real local media/API/queue integration; Google/transport mocks are explicit."""
import json
from pathlib import Path
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, create_engine, text, func
from backend.main import app
from backend import database, import_service, import_transport, media_library, providers, drive
from backend.database import Session, ImportTask, Project, Job, MediaSource
from backend.ingestion import IngestionError

ROOT=Path(__file__).resolve().parents[1]


def project_count():
    # Other API resource tests intentionally leave a malformed edit fixture;
    # count rows directly instead of serializing that unrelated fixture.
    with Session() as db:
        return db.scalar(select(func.count()).select_from(Project))


@pytest.fixture(scope='module')
def client():
    with TestClient(app) as client:
        yield client


def wait_import(client, task_id, expected='complete', timeout=90):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        task=client.get('/api/imports/'+task_id).json()
        if task['status'] not in ('queued','running'):
            assert task['status']==expected,task
            return task
        time.sleep(.1)
    raise AssertionError('Import did not finish')


def wait_analysis(client,pid):
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        project=client.get('/api/projects/'+pid).json()
        if project['status'] in ('ready','failed'):
            assert project['status']=='ready',project.get('error')
            return project
        time.sleep(.1)
    raise AssertionError('Analysis did not finish')


def test_additive_v1_migration_preserves_project_rows(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'v1.sqlite3'))
    database.Base.metadata.create_all(engine,tables=[database.Project.__table__,database.Clip.__table__,database.Job.__table__,database.Export.__table__])
    with engine.begin() as c:
        c.execute(database.Project.__table__.insert(),{'id':'legacy','title':'Keep this project'})
        c.execute(text('pragma user_version=1'))
    monkeypatch.setattr(database,'engine',engine)
    database.migrate()
    with engine.connect() as c:
        assert c.execute(text('select title from projects')).scalar()=='Keep this project'
        assert c.execute(text('pragma user_version')).scalar()==2
        assert c.execute(text("select count(*) from sqlite_master where name in ('imports','source_links','media_sources')")).scalar()==3
    engine.dispose()


def test_approved_adapter_can_precede_metadata_only_youtube(monkeypatch):
    monkeypatch.setattr(providers,'_ADAPTERS',list(providers._ADAPTERS))
    class Approved:
        name='explicitly_approved_fixture'
        def matches(self,url):return 'youtu.be/' in url
        def resolve(self,url):return {'provider':self.name,'readiness':'ready','authorized':True}
        def plan(self,reference,secret):return providers.DownloadPlan('https://licensed.example/original.mp4')
    providers.register_provider(Approved(),before='youtube')
    adapter=providers.adapter_for('https://youtu.be/ZyxWvu98765')
    assert adapter.name=='explicitly_approved_fixture'
    assert adapter.plan({},{}).url=='https://licensed.example/original.mp4'


def test_unified_resolution_never_calls_youtube_metadata_ingestion(client,monkeypatch):
    monkeypatch.setattr(providers.YouTubeReference,'resolve',lambda self,url:{'provider':'youtube','youtube_id':providers.youtube_id(url),'readiness':'requires_file','message':'Metadata only'})
    response=client.post('/api/sources/resolve',json={'url':'https://youtu.be/ZyxWvu98765'})
    assert response.status_code==200 and response.json()['readiness']=='requires_file'
    before=project_count()
    assert client.post('/api/imports',json={'url':'https://youtu.be/ZyxWvu98765','rights_confirmed':True}).status_code==422
    assert project_count()==before
    assert client.post('/api/sources/resolve',json={'url':'https://drive.google.com/drive/folders/not-a-file'}).status_code==422
    monkeypatch.setattr(drive,'connection_status',lambda:{'configured':False,'connected':False,'message':'Setup required'})
    result=client.post('/api/sources/resolve',json={'url':'https://drive.google.com/file/d/drive_video_123/view'}).json()
    assert result['provider']=='google_drive' and result['readiness']=='requires_connection'


def test_registry_youtube_reuse_creates_one_real_analyzed_project(client):
    fixture=ROOT/'tests/artifacts/jfk-source.mp4'
    with fixture.open('rb') as media:
        uploaded=client.post('/api/projects',files={'file':('fixture.mp4',media,'video/mp4')},data={'rights_confirmed':'true','title':'Registry original'}).json()
    wait_analysis(client,uploaded['id'])
    assert client.post('/api/sources/link',json={'project_id':uploaded['id'],'youtube_url':'https://youtu.be/QweRty12345'}).status_code==422
    linked=client.post('/api/sources/link',json={'project_id':uploaded['id'],'youtube_url':'https://youtu.be/QweRty12345','rights_confirmed':True})
    assert linked.status_code==200 and linked.json()['available']
    key=str(uuid4())
    request={'url':'https://youtu.be/QweRty12345','rights_confirmed':True,'idempotency_key':key,'title':'Reused original'}
    response=client.post('/api/imports',json=request)
    assert response.status_code==202
    task=response.json()
    assert client.post('/api/imports',json=request).json()['id']==task['id']
    complete=wait_import(client,task['id'])
    project=wait_analysis(client,complete['project_id'])
    assert project['transcript']['segments'] and project['clips']
    assert project['provenance']['source_kind']=='authorized_library_reuse'
    assert client.post('/api/imports',json=request).json()['project_id']==project['id']
    with Session() as db:
        assert len(list(db.scalars(select(Job).where(Job.project_id==project['id'],Job.kind=='analysis'))))==1
    # Remove the original project; its registry original must remain reusable.
    assert client.delete('/api/projects/'+uploaded['id']).status_code==204
    assert next(s for s in client.get('/api/sources').json() if s['id']==linked.json()['id'])['available']


def test_import_failure_never_publishes_project_and_keeps_retry_state(client,monkeypatch):
    def fake(url,destination,**kwargs):
        kwargs['checkpoint']()
        destination.write_bytes(b'not a decodable movie')
        kwargs['progress']({'received_bytes':20,'total_bytes':20,'attempt':1})
        return {'filename':'bad.mp4','provenance':{'source_kind':'authorized_https_import'}}
    monkeypatch.setattr(import_transport,'download_resumable',fake)
    before=project_count()
    response=client.post('/api/imports',json={'url':'https://media.example/movie.mp4','rights_confirmed':True})
    assert response.status_code==202
    task=wait_import(client,response.json()['id'],expected='failed')
    assert 'decodable video' in task['error']
    assert task['project_id'] is None and project_count()==before
    assert 'media.example' not in json.dumps(client.get('/api/imports').json())
    assert client.delete('/api/imports/'+task['id']).json()=={'discarded':True}
    assert client.get('/api/imports/'+task['id']).status_code==404
    assert project_count()==before


def test_pause_resume_api_preserves_partial_and_real_media_pipeline(client,monkeypatch):
    fixture=(ROOT/'tests/artifacts/jfk-source.mp4').read_bytes()
    starts=[]
    def slow(url,destination,**kwargs):
        partial=Path(str(destination)+'.part')
        initial=partial.stat().st_size if partial.exists() else 0
        starts.append(initial)
        with partial.open('ab') as target:
            for offset in range(initial,len(fixture),128*1024):
                kwargs['checkpoint']()
                chunk=fixture[offset:offset+128*1024]
                target.write(chunk)
                target.flush()
                kwargs['progress']({'received_bytes':offset+len(chunk),'total_bytes':len(fixture),'attempt':1,'resumed':bool(initial)})
                time.sleep(.02)
        partial.replace(destination)
        return {'filename':'fixture.mp4','provenance':{'source_kind':'authorized_https_import'}}
    monkeypatch.setattr(import_transport,'download_resumable',slow)
    response=client.post('/api/imports',json={'url':'https://media.example/owned.mp4?signature=PRIVATE','rights_confirmed':True})
    task_id=response.json()['id']
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if client.get('/api/imports/'+task_id).json()['bytes_received']>0:
            break
        time.sleep(.03)
    assert client.post('/api/imports/'+task_id+'/cancel').json()['status']=='paused'
    while import_service._active_id==task_id and time.monotonic()<deadline:
        time.sleep(.03)
    assert client.post('/api/imports/'+task_id+'/retry',json={}).status_code==200
    complete=wait_import(client,task_id)
    project=wait_analysis(client,complete['project_id'])
    assert starts[0]==0 and starts[1]>0
    assert project['clips'] and 'PRIVATE' not in json.dumps(client.get('/api/imports').json())


def test_new_routes_body_origin_and_callback_privacy(client):
    assert client.post('/api/sources/resolve',content=b'x'*17000,headers={'Content-Type':'application/json'}).status_code==413
    assert client.post('/api/imports',json={'url':'https://example.com/video.mp4','rights_confirmed':True},headers={'Origin':'https://evil.test'}).status_code==403
    callback=client.get('/api/connections/google-drive/callback?error=%3Cscript%3Ealert(1)%3C/script%3E&state=PRIVATE')
    assert callback.status_code==400 and '<script>' not in callback.text and 'PRIVATE' not in callback.text
    assert callback.headers['cache-control']=='no-store'
    import logging
    from backend.import_routes import OAuthLogPrivacy
    record=logging.LogRecord('uvicorn.access',20,'',0,'%s %s %s %s %s',('host','GET','/api/connections/google-drive/callback?code=PRIVATE','1.1',200),None)
    OAuthLogPrivacy().filter(record)
    assert 'PRIVATE' not in str(record.args)


def test_completed_download_is_reused_after_probe_pause(client,monkeypatch):
    fixture=(ROOT/'tests/artifacts/jfk-source.mp4').read_bytes()
    calls=[]
    real_validate=import_service._validate
    def download(url,destination,**kwargs):
        calls.append(url)
        destination.write_bytes(fixture)
        return {'filename':'fixture.mp4','provenance':{'source_kind':'authorized_https_import'}}
    paused=[]
    def validate(directory,checkpoint):
        if not paused:
            paused.append(True)
            import_service.pause(directory.name)
            raise import_service.ImportPaused('Pause after acquisition')
        return real_validate(directory,checkpoint)
    monkeypatch.setattr(import_transport,'download_resumable',download)
    monkeypatch.setattr(import_service,'_validate',validate)
    task=client.post('/api/imports',json={'url':'https://media.example/resumable.mp4','rights_confirmed':True}).json()
    wait_import(client,task['id'],expected='paused')
    deadline=time.monotonic()+10
    while import_service._active_id==task['id'] and time.monotonic()<deadline:
        time.sleep(.02)
    client.post('/api/imports/'+task['id']+'/retry',json={}).raise_for_status()
    complete=wait_import(client,task['id'])
    wait_analysis(client,complete['project_id'])
    assert len(calls)==1
    assert client.delete('/api/projects/'+complete['project_id']).status_code==204
    history=client.get('/api/imports/'+task['id']).json()
    assert history['project_id'] is None and 'retained' in history['stage']
    assert next(s for s in client.get('/api/sources').json() if s['id']==history['source_id'])['available']


def test_library_detects_same_size_corruption_and_copy_failure(tmp_path,monkeypatch):
    monkeypatch.setattr(media_library,'DATA',tmp_path)
    original=tmp_path/'original'
    original.write_bytes(b'valid library unit bytes')
    with Session.begin() as db:
        source=media_library.register_source(db,original,{},'unit.mp4','Unit',{'rights_confirmed':True})
        canonical=media_library.source_path(source)
        canonical.unlink()
        canonical.write_bytes(b'x'*original.stat().st_size)
        with pytest.raises(IngestionError,match='damaged'):
            media_library.register_source(db,original,{},'unit.mp4','Unit',{'rights_confirmed':True})
    monkeypatch.setattr(media_library.os,'link',lambda *args:(_ for _ in ()).throw(OSError('unsupported')))
    destination=tmp_path/'failed-copy'
    def cancel():
        raise import_service.ImportPaused('pause')
    with pytest.raises(import_service.ImportPaused):
        media_library.copy_original(original,destination,cancel)
    assert not destination.exists() and original.read_bytes()==b'valid library unit bytes'


def test_library_reserves_quota_before_creating_reusable_copy(tmp_path,monkeypatch):
    monkeypatch.setattr(media_library,'DATA',tmp_path)
    monkeypatch.setattr(media_library,'MAX_DISK',1)
    original=tmp_path/'original-quota'
    original.write_bytes(b'quota-protected original')
    with Session.begin() as db:
        with pytest.raises(IngestionError,match='storage limit'):
            media_library.register_source(db,original,{},'quota.mp4','Quota',{'rights_confirmed':True})
    assert original.read_bytes()==b'quota-protected original'
    assert not (tmp_path/'media_sources').exists()

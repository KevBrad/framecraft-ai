"""Durable imports feed the existing analysis queue after real media validation."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from uuid import UUID, uuid4

from sqlalchemy import select
from .config import DATA, ROOT, MAX_UPLOAD, MAX_DISK
from .database import Session, ImportTask, MediaSource, SourceLink, Project, Job, lock, now
from .ingestion import IngestionError
from .media_library import available, source_path, register_source, copy_original, file_digest, link_youtube
from .providers import adapter_for, adapter_named, youtube_id
from .resources import check_disk_budget, contain_process

_stop = threading.Event()
_thread = None
_active_id = None


class ImportPaused(IngestionError):
    cancelled = True


def import_json(task):
    return {**{key:getattr(task,key) for key in ('id','provider','status','stage','bytes_received','total_bytes','attempts',
                'error','project_id','source_id','created_at','updated_at')},'title':(task.spec or {}).get('title','')}


def resolve(url):
    adapter = adapter_for(url)
    if adapter.name=='youtube':
        video_id=youtube_id(url)
        with Session() as db:
            link=db.get(SourceLink,video_id)
            source=db.get(MediaSource,link.source_id) if link else None
            if source and available(source):
                return {'provider':'youtube','youtube_id':video_id,'source_id':source.id,'readiness':'ready',
                        'title':source.title,'size_bytes':source.size_bytes,'metadata':{'video_id':video_id,'metadata_status':'stored_original'},
                        'message':'Authorized original found in your source library. Ready to process without another upload.'}
    result = adapter.resolve(url)
    if adapter.name == 'youtube':
        with Session() as db:
            link = db.get(SourceLink, result['youtube_id'])
            source = db.get(MediaSource, link.source_id) if link else None
            if source and available(source):
                result.update(readiness='ready', source_id=source.id, title=result.get('title') or source.title,
                              size_bytes=source.size_bytes, message='Authorized original found in your source library. Ready to process without another upload.')
    return result


def create(spec):
    """Persist intent before network I/O. A metadata-only source cannot queue."""
    from .secrets import save_secret
    if not spec.rights_confirmed:
        raise IngestionError('Confirm that you have permission to process this video.')
    with lock, Session() as db:
        existing = db.scalar(select(ImportTask).where(ImportTask.idempotency_key == spec.idempotency_key))
        if existing:
            # Returning the original is safe; a key cannot create a second project.
            return import_json(existing)
    provider, reference, secret = 'library', {}, None
    source_id = spec.source_id
    video_id = youtube_id(spec.youtube_url) if spec.youtube_url else None
    if spec.url:
        adapter = adapter_for(spec.url)
        provider = adapter.name
        if provider == 'youtube':
            video_id = youtube_id(spec.url)
            with Session() as db:
                link = db.get(SourceLink, video_id)
                if not link:
                    raise IngestionError('YouTube metadata is not downloadable media. Link or upload an authorized original first.')
                source_id, provider = link.source_id, 'library'
        elif provider == 'google_drive':
            descriptor = adapter.resolve(spec.url)
            if descriptor['readiness'] != 'ready':
                raise IngestionError(descriptor['message'])
            reference['file_id'] = descriptor['file_id']
        elif provider=='direct':
            # Syntactic validation happens before enqueue; pinned network and
            # full media validation run in the import worker, never this request.
            from .ingestion import _parse_url
            _parse_url(spec.url)
            secret = {'url':spec.url}
        else:
            # Explicitly installed licensed adapters may identify a reference
            # URL and return a separately authorized download plan. They must
            # establish authorization before the generic queue can accept it.
            descriptor=adapter.resolve(spec.url)
            if descriptor.get('readiness')!='ready' or descriptor.get('authorized') is not True:
                raise IngestionError('This provider has not supplied authorized downloadable media.')
            reference['provider_source']=descriptor.get('source_ref') or {}
            secret={'url':spec.url}
    if source_id:
        with Session() as db:
            source = db.get(MediaSource, str(UUID(source_id)))
            if not source or not available(source):
                raise IngestionError('The stored original is unavailable. Choose another source or upload the original.')
        reference['source_id'], provider = source_id, 'library'
    if provider == 'library' and not reference.get('source_id'):
        raise IngestionError('Choose an authorized source or video URL.')
    task_id = str(uuid4())
    if secret:
        save_secret('import-'+task_id, secret)
    from .schemas import AnalysisSettings
    analysis = spec.settings.model_dump() if getattr(spec,'settings',None) is not None else AnalysisSettings.model_validate({k:getattr(spec,k) for k in AnalysisSettings.model_fields}).model_dump()
    settings = {**analysis, 'title':spec.title, 'rights_confirmed':True, 'youtube_id':video_id}
    reference['project_id'] = str(uuid4())
    try:
        with lock, Session.begin() as db:
            # Serialized recheck handles duplicate clicks arriving simultaneously.
            existing = db.scalar(select(ImportTask).where(ImportTask.idempotency_key == spec.idempotency_key))
            if existing:
                from .secrets import delete_secret
                delete_secret('import-'+task_id)
                return import_json(existing)
            task = ImportTask(id=task_id, provider=provider, source_ref=reference, spec=settings,
                              idempotency_key=spec.idempotency_key, source_id=source_id)
            db.add(task)
            db.flush()
            return import_json(task)
    except Exception:
        if secret:
            from .secrets import delete_secret
            delete_secret('import-'+task_id)
        raise


def pause(task_id):
    with lock, Session.begin() as db:
        task = db.get(ImportTask, task_id)
        if not task:
            raise IngestionError('Import not found.')
        if task.status in ('queued','running'):
            task.status, task.stage, task.updated_at = 'paused', 'Paused — partial download retained', now()
        return import_json(task)


def retry(task_id, url=None):
    from .secrets import save_secret
    with lock, Session.begin() as db:
        task = db.get(ImportTask, task_id)
        if not task:
            raise IngestionError('Import not found.')
        if task.status in ('queued','running','complete'):
            return import_json(task)
        if _active_id == task_id:
            raise IngestionError('The import is stopping. Wait a moment, then resume.')
        if url:
            if task.provider != 'direct' or adapter_for(url).name != 'direct':
                raise IngestionError('A replacement link must be a direct HTTPS video-file source.')
            from .ingestion import _parse_url
            _parse_url(url)
            save_secret('import-'+task_id, {'url':url})
        task.status, task.stage, task.error, task.updated_at = 'queued', 'Queued to resume', None, now()
        return import_json(task)


def discard(task_id):
    """Discard only an unfinished import's staging data, never a stored source."""
    from .secrets import delete_secret
    with lock,Session.begin() as db:
        task=db.get(ImportTask,task_id)
        if not task:
            raise IngestionError('Import not found.')
        if task.status not in ('paused','failed','cancelled') or _active_id==task_id or task.project_id:
            raise IngestionError('Pause the unfinished import and wait for it to stop before discarding its partial download.')
        directory=DATA/'imports'/task_id
        if directory.resolve().parent!=(DATA/'imports').resolve() or directory.is_symlink():
            raise IngestionError('Import storage path validation failed.')
        if directory.exists():
            shutil.rmtree(directory)
        delete_secret('import-'+task_id)
        db.delete(task)
    return {'discarded':True}


def start():
    global _thread
    _stop.clear()
    with lock, Session.begin() as db:
        for task in db.scalars(select(ImportTask).where(ImportTask.status=='running')):
            task.status, task.stage = 'queued', 'Resuming interrupted import'
    _thread = threading.Thread(target=_loop, name='framecraft-imports', daemon=True)
    _thread.start()


def stop():
    _stop.set()
    if _thread:
        _thread.join(timeout=45)


def _loop():
    global _active_id
    while not _stop.is_set():
        with lock, Session.begin() as db:
            task = db.scalar(select(ImportTask).where(ImportTask.status=='queued').order_by(ImportTask.created_at).limit(1))
            task_id = task.id if task else None
            if task:
                task.status, task.stage, task.updated_at = 'running', 'Connecting to source', now()
                _active_id = task_id
        if not task_id:
            _stop.wait(.3)
            continue
        try:
            _execute(task_id)
        except ImportPaused:
            with lock, Session.begin() as db:
                task = db.get(ImportTask,task_id)
                if task and task.status=='running':
                    task.status,task.stage = 'queued','Resume after restart'
        except Exception as exc:
            from .drive import DriveError
            from .secrets import SecretError
            safe = str(exc)[:600] if isinstance(exc,(IngestionError,DriveError,SecretError)) else 'Import failed. Check the source, connection and storage, then retry.'
            with lock, Session.begin() as db:
                task = db.get(ImportTask,task_id)
                if task and task.status=='running':
                    task.status,task.stage,task.error,task.updated_at='failed','Import needs attention',safe,now()
        finally:
            with lock:
                _active_id = None


def _validate(directory, checkpoint):
    gate = directory/'probe.ready'
    gate.unlink(missing_ok=True)
    for name in ('probe.json','probe-error.json'):
        (directory/name).unlink(missing_ok=True)
    executable, env = sys.executable, os.environ.copy()
    if os.name=='nt':
        executable=getattr(sys,'_base_executable',sys.executable)
        env['__PYVENV_LAUNCHER__']=sys.executable
    process = subprocess.Popen([executable,'-m','backend.import_probe',str(directory),str(gate)],cwd=ROOT,env=env,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                               start_new_session=os.name!='nt')
    containment = None
    try:
        containment = contain_process(process)
        gate.write_text('contained',encoding='ascii')
        deadline = time.monotonic()+100
        while process.poll() is None:
            checkpoint()
            containment.check()
            if time.monotonic()>deadline:
                raise IngestionError('Media validation timed out. Use a supported original video file.')
            time.sleep(.1)
        if process.returncode or not (directory/'probe.json').is_file():
            raise IngestionError('The downloaded file is not a supported, decodable video within the configured limits.')
        return json.loads((directory/'probe.json').read_text(encoding='utf-8'))['metadata']
    finally:
        if containment:
            containment.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


def _execute(task_id):
    from .import_transport import download_resumable
    from .secrets import load_secret, delete_secret
    with Session() as db:
        task=db.get(ImportTask,task_id)
        provider,reference,spec=task.provider,dict(task.source_ref),dict(task.spec)
        prior_attempts=task.attempts or 0
    directory=DATA/'imports'/task_id
    directory.mkdir(parents=True,exist_ok=True)
    media=directory/'media'
    last_check=last_report=0
    def checkpoint():
        nonlocal last_check
        if _stop.is_set():
            raise ImportPaused('Service stopping; partial download saved.')
        if time.monotonic()-last_check>.1:
            with Session() as db:
                current=db.get(ImportTask,task_id)
                if not current or current.status!='running':
                    raise ImportPaused('Import paused.')
            if shutil.disk_usage(DATA).free<512*1024**2:
                raise IngestionError('Not enough free disk space. Free storage and resume this import.')
            last_check=time.monotonic()
        if not hasattr(checkpoint,'budget_at') or time.monotonic()-checkpoint.budget_at>2:
            budget=check_disk_budget(DATA)
            if budget['used_bytes']+256*1024>MAX_DISK:
                raise IngestionError('Workspace storage limit reached. Free storage and resume this import.')
            checkpoint.budget_at=time.monotonic()
    def progress(update):
        nonlocal last_report
        checkpoint()
        received=update.get('received_bytes',0)
        total=update.get('total_bytes')
        if received>MAX_UPLOAD or total and total>MAX_UPLOAD:
            raise IngestionError('The video exceeds the configured import size limit.')
        if time.monotonic()-last_report<.2 and received!=total:
            return
        with lock,Session.begin() as db:
            current=db.get(ImportTask,task_id)
            if current and current.status=='running':
                current.bytes_received=received
                current.total_bytes=total
                current.attempts=prior_attempts+update.get('attempt',1)
                current.stage=update.get('stage') or ('Resuming video import' if update.get('resumed') else 'Importing video')
                current.updated_at=now()
        last_report=time.monotonic()
    def stage(message):
        checkpoint()
        with lock,Session.begin() as db:
            current=db.get(ImportTask,task_id)
            if current and current.status=='running':
                current.stage,current.updated_at=message,now()
    checkpoint()
    filename='Imported video.mp4'
    provenance={'rights_confirmed':True,'imported_at':now(),'source_kind':'authorized_library_reuse'}
    if provider=='library':
        with Session() as db:
            stored=db.get(MediaSource,reference['source_id'])
            if not stored or not available(stored):
                raise IngestionError('The linked original is unavailable. Restore or choose another source.')
            original=source_path(stored)
            expected=stored.sha256
            filename=stored.filename
            provenance.update(reused_source_id=stored.id)
        stage('Verifying stored authorized original')
        if file_digest(original,checkpoint)!=expected:
            raise IngestionError('The stored original has changed. Relink the correct original before processing.')
        if not media.exists():
            copy_original(original,media)
        elif file_digest(media,checkpoint)!=expected:
            raise IngestionError('An incomplete cached source differs from the authorized original.')
        progress({'received_bytes':media.stat().st_size,'total_bytes':media.stat().st_size,'attempt':1})
    else:
        plan=adapter_named(provider).plan(reference,load_secret('import-'+task_id) or {})
        identity={'url_hash':hashlib.sha256(plan.url.encode()).hexdigest(),'provider_identity':plan.identity,
                  'expected_size':plan.expected_size,'expected_md5':plan.expected_md5}
        acquisition=directory/'acquisition.json'
        try:
            saved=json.loads(acquisition.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            saved={}
        result=None
        if media.exists() and saved.get('identity')==identity:
            candidate=saved.get('result') or {}
            if candidate.get('sha256')==file_digest(media,checkpoint) and media.stat().st_size==candidate.get('size_bytes'):
                result=candidate
                progress({'received_bytes':media.stat().st_size,'total_bytes':media.stat().st_size,'attempt':1,'resumed':True})
        if result is None:
            if media.exists():
                media.replace(Path(str(media)+'.part'))
            result=download_resumable(plan.url,media,max_bytes=MAX_UPLOAD,progress=progress,checkpoint=checkpoint,
                                     headers_factory=plan.headers_factory,allowed_auth_host=plan.allowed_auth_host,
                                     expected_size=plan.expected_size,expected_md5=plan.expected_md5)
            if not result.get('sha256'):
                result['sha256']=file_digest(media,checkpoint)
            result.setdefault('size_bytes',media.stat().st_size)
            from .pipeline import _write_json
            _write_json(acquisition,{'identity':identity,'result':result})
        filename=plan.filename or result['filename']
        provenance.update(result.get('provenance') or {},rights_confirmed=True)
        if provider=='google_drive':
            provenance.update(source_kind='authorized_google_drive',**(plan.identity or {}))
    stage('Validating downloaded video')
    metadata=_validate(directory,checkpoint)
    metadata['provenance']=provenance
    title=spec['title'].strip() or Path(filename).stem or 'Imported video'
    checkpoint()
    digest=file_digest(media,checkpoint)
    canonical=DATA/'media_sources'/digest/'source'
    if canonical.exists() and file_digest(canonical,checkpoint)!=digest:
        raise IngestionError('The stored original is damaged. Restore it before reusing this source.')
    project_source=DATA/'projects'/reference['project_id']/'source'
    extra_copies=int(not canonical.exists())+int(not project_source.exists())
    if check_disk_budget(DATA)['used_bytes']+extra_copies*media.stat().st_size>MAX_DISK:
        raise IngestionError('Workspace storage limit cannot retain the original and project copy. Free storage and resume this import.')
    # Source registration and project/job/import publication share one database
    # transaction. A crash cannot publish two projects for the same import.
    with lock,Session.begin() as db:
        current=db.get(ImportTask,task_id)
        if not current or current.status!='running':
            raise ImportPaused('Import paused before publication.')
        source=register_source(db,media,metadata,filename,title,provenance,lambda:None,verified_digest=digest)
        if spec.get('youtube_id'):
            link_youtube(db,source,spec['youtube_id'])
        pid=reference['project_id']
        project_dir=DATA/'projects'/pid
        project_dir.mkdir(parents=True,exist_ok=True)
        if not (project_dir/'source').exists():
            copy_original(source_path(source),project_dir/'source')
        elif file_digest(project_dir/'source')!=source.sha256:
            raise IngestionError('Project source identity does not match the imported video.')
        from .schemas import AnalysisSettings
        settings=AnalysisSettings.model_validate({k:v for k,v in spec.items() if k in AnalysisSettings.model_fields}).model_dump()
        project=Project(id=pid,title=title[:200],filename=filename[:200],media=metadata,settings=settings,
                        youtube_url=f"https://www.youtube.com/watch?v={spec['youtube_id']}" if spec.get('youtube_id') else None)
        db.add(project)
        db.flush()
        db.add(Job(id=str(uuid4()),project_id=pid,kind='analysis',spec=settings))
        current.status,current.stage,current.project_id,current.source_id='complete','Imported — processing project',pid,source.id
        current.bytes_received,current.total_bytes=source.size_bytes,source.size_bytes
        current.error,current.updated_at=None,now()
    delete_secret('import-'+task_id)
    # Publication retains immutable library/project links. Only this import's
    # redundant staging name is removed; partials remain for failed/paused tasks.
    media.unlink(missing_ok=True)

"""Framecraft's loopback-only API and single-user workspace."""
import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import select, delete

from .config import ROOT, DATA, MAX_UPLOAD, MAX_DISK
from .database import Session, Project, Clip, Job, Export, ImportTask, migrate, lock, now
from .schemas import AnalysisSettings, EditSpec, URLImport
from . import worker, import_service, media_library
from .resources import check_disk_budget, ResourceLimitError, resource_status


@asynccontextmanager
async def lifespan(app):
    migrate()
    with lock, Session.begin() as db:
        media_library.backfill(db)
    worker.start()
    import_service.start()
    try:
        yield
    finally:
        import_service.stop()
        worker.stop()


app = FastAPI(title='Framecraft', version='1.0.0', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])


class UploadBodyLimit:
    """Bound multipart bytes before Starlette spools them to temporary storage."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or not scope.get('path', '').startswith('/api/'):
            return await self.app(scope, receive, send)
        limit = MAX_UPLOAD + 1024 * 1024 if scope.get('path') == '/api/projects' else 16384
        headers = dict(scope.get('headers', []))
        try:
            length = int(headers.get(b'content-length', b'0'))
        except ValueError:
            return await JSONResponse({'detail': 'Invalid Content-Length.'}, status_code=400)(scope, receive, send)
        if length < 0:
            return await JSONResponse({'detail': 'Invalid Content-Length.'}, status_code=400)(scope, receive, send)
        if length > limit:
            return await JSONResponse({'detail': 'Upload request exceeds the configured size limit.'}, status_code=413)(scope, receive, send)
        seen = 0
        async def bounded_receive():
            nonlocal seen
            message = await receive()
            seen += len(message.get('body', b''))
            if seen > limit:
                raise HTTPException(413, 'Upload request exceeds the configured size limit.')
            return message
        return await self.app(scope, bounded_receive, send)


app.add_middleware(UploadBodyLimit)


@app.middleware('http')
async def local_origin(request: Request, call_next):
    origin = request.headers.get('origin')
    if origin:
        parsed = urlparse(origin)
        if parsed.hostname not in ('localhost', '127.0.0.1', '::1') or parsed.scheme not in ('http', 'https'):
            return JSONResponse({'detail': 'Only the local application may access this workspace.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


def uid(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError):
        raise HTTPException(404, 'Item not found.')


def get(db, cls, value):
    item = db.get(cls, uid(value))
    if not item:
        raise HTTPException(404, 'Item not found.')
    return item


def export_json(e):
    return {'id': e.id, 'project_id': e.project_id, 'clip_id': e.clip_id, 'version': e.version,
            'status': 'complete', 'created_at': e.created_at, 'metadata': e.media,
            'download_url': f'/api/exports/{e.id}/download', 'preview_url': f'/api/exports/{e.id}/preview'}


def job_json(j):
    return {key: getattr(j, key) for key in ('id', 'project_id', 'clip_id', 'kind', 'status', 'stage', 'error', 'created_at', 'updated_at', 'edit_version')}


def clip_json(db, c):
    return {**c.candidate, 'id': c.id, 'project_id': c.project_id, 'title': c.edit['title'],
            'start': c.edit['start'], 'end': c.edit['end'], 'duration': c.edit['end'] - c.edit['start'],
            'edit': c.edit, 'version': c.version, 'framing': c.framing,
            'exports': [export_json(e) for e in db.scalars(select(Export).where(Export.clip_id == c.id).order_by(Export.created_at.desc()))]}


def project_json(db, p):
    return {**{key: getattr(p, key) for key in ('id', 'title', 'created_at', 'updated_at', 'status', 'stage', 'error', 'filename', 'youtube_url', 'settings', 'transcript')},
            'metadata': p.media, 'source_url': f'/api/projects/{p.id}/source', 'thumbnail_url': f'/api/projects/{p.id}/thumbnail',
            'provenance': (p.media or {}).get('provenance'),
            'clips': [clip_json(db, c) for c in db.scalars(select(Clip).where(Clip.project_id == p.id))],
            'jobs': [job_json(j) for j in db.scalars(select(Job).where(Job.project_id == p.id).order_by(Job.created_at.desc()))],
            'exports': [export_json(e) for e in db.scalars(select(Export).where(Export.project_id == p.id).order_by(Export.created_at.desc()))]}


def enqueue_analysis(db, project, settings):
    existing = db.scalar(select(Job).where(Job.project_id == project.id, Job.kind == 'analysis', Job.status.in_(['queued', 'running'])))
    if existing:
        return existing
    job = Job(id=str(uuid4()), project_id=project.id, kind='analysis', spec=settings)
    db.add(job)
    project.settings, project.status, project.stage, project.error = settings, 'queued', 'Queued', None
    project.updated_at = now()
    db.flush()
    return job


@app.get('/api/health')
def health():
    from .pipeline import dependency_status
    return {'status': 'ok', **dependency_status(), 'resources': resource_status()}


@app.get('/api/projects')
def projects():
    with Session() as db:
        return [project_json(db, p) for p in db.scalars(select(Project).order_by(Project.created_at.desc()))]


@app.get('/api/projects/{project_id}')
def project(project_id: str):
    with Session() as db:
        return project_json(db, get(db, Project, project_id))


@app.post('/api/projects/import-url', status_code=201)
def import_url(spec: URLImport):
    """Compatibility route: existing clients receive Project; new UI uses /imports.

    Both routes use the same durable, resumable backend. A disconnected legacy
    request does not discard the import or restart a partially downloaded file.
    """
    from .import_routes import ImportRequest, call
    task=call(import_service.create,ImportRequest(**spec.model_dump()))
    deadline=time.monotonic()+1000
    while time.monotonic()<deadline:
        with Session() as db:
            from .database import ImportTask
            current=db.get(ImportTask,task['id'])
            if current.project_id:
                return project_json(db,db.get(Project,current.project_id))
            if current.status in ('failed','paused','cancelled'):
                raise HTTPException(422,current.error or current.stage)
        time.sleep(.2)
    raise HTTPException(408,'Import is still pending. Check /api/imports for saved progress.')


@app.post('/api/projects', status_code=201)
async def create_project(file: UploadFile = File(...), rights_confirmed: bool = Form(False), title: str = Form(''),
                         youtube_url: str = Form(''), min_duration: float = Form(15), max_duration: float = Form(45), topic: str = Form(''), settings: str = Form('')):
    if not rights_confirmed:
        raise HTTPException(422, 'Confirm that you have the rights to process this media.')
    try:
        settings = AnalysisSettings.model_validate_json(settings).model_dump() if settings else AnalysisSettings(min_duration=min_duration, max_duration=max_duration, topic=topic).model_dump()
    except ValueError:
        raise HTTPException(422, 'Clip duration must be 3–180 seconds, with minimum no greater than maximum.')
    if youtube_url:
        youtube_id(youtube_url)
    filename = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', file.filename or 'Untitled video')[:200]
    if not filename.lower().endswith(('.mp4', '.mov', '.webm', '.mkv', '.m4v')):
        raise HTTPException(415, 'Choose an MP4, MOV, WebM, MKV or M4V video file.')
    pid = str(uuid4())
    directory = worker.project_dir(pid)
    directory.mkdir(parents=True)
    size = 0
    digest = hashlib.sha256()
    try:
        # Reserve headroom before accepting a potentially large input.
        try:
            budget = check_disk_budget(DATA)
        except ResourceLimitError as exc:
            raise HTTPException(507, str(exc))
        used = budget['used_bytes']
        if used >= MAX_DISK or shutil.disk_usage(DATA).free < 512 * 1024**2:
            raise HTTPException(507, 'Workspace storage is full. Delete unused projects before uploading.')
        with (directory / 'source').open('wb') as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, f'Upload exceeds the {MAX_UPLOAD // 1024**2} MB limit.')
                if used + size > MAX_DISK:
                    raise HTTPException(507, 'Workspace storage limit exceeded.')
                target.write(chunk)
                digest.update(chunk)
        from .pipeline import probe
        try:
            metadata = await asyncio.to_thread(probe, directory / 'source')
        except Exception as exc:
            raise HTTPException(415, str(exc)[:500])
        metadata['provenance'] = {'sha256': digest.hexdigest(), 'size_bytes': size, 'rights_confirmed': True,
                                  'imported_at': now(), 'source_kind': 'authorized_upload', 'reference_url': youtube_url or None}
        with lock, Session.begin() as db:
            p = Project(id=pid, title=(title.strip() or Path(filename).stem)[:200], filename=filename,
                        youtube_url=youtube_url or None, media=metadata, settings=settings)
            db.add(p)
            db.flush()
            source = media_library.register_source(db, directory / 'source', metadata, filename, p.title, metadata['provenance'],verified_digest=digest.hexdigest())
            if youtube_url:
                media_library.link_youtube(db, source, youtube_id(youtube_url))
            enqueue_analysis(db, p, settings)
        with Session() as db:
            return project_json(db, db.get(Project, pid))
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await file.close()


@app.post('/api/projects/{project_id}/analyze')
def analyze_project(project_id: str, settings: AnalysisSettings):
    with lock, Session.begin() as db:
        p = get(db, Project, project_id)
        return job_json(enqueue_analysis(db, p, settings.model_dump()))


@app.post('/api/jobs/{job_id}/cancel')
def cancel_job(job_id: str):
    with lock, Session.begin() as db:
        j = get(db, Job, job_id)
        if j.status in ('queued', 'running'):
            j.status, j.stage, j.updated_at = 'cancelled', 'Cancelled', now()
            if j.kind == 'analysis':
                p = get(db, Project, j.project_id)
                p.status, p.stage, p.updated_at = 'cancelled', 'Cancelled', now()
        return job_json(j)


@app.put('/api/clips/{clip_id}')
def save_clip(clip_id: str, spec: EditSpec):
    with lock, Session.begin() as db:
        clip = get(db, Clip, clip_id)
        p = get(db, Project, clip.project_id)
        if spec.end > p.media['duration'] + .05:
            raise HTTPException(422, 'Clip end exceeds the source duration.')
        if any(c.end > p.media['duration'] + .05 for c in spec.captions):
            raise HTTPException(422, 'Caption timing exceeds the source duration.')
        if spec.expected_version is not None and spec.expected_version != clip.version:
            raise HTTPException(409, 'This clip changed elsewhere. Reopen it before saving.')
        edit = spec.model_dump(exclude={'expected_version'}, exclude_none=True)
        # Changed text has no trustworthy per-word alignment; pipeline redistributes within the line.
        for c in edit['captions']:
            if c.get('words') and ' '.join(w['word'].strip() for w in c['words']).strip() != c['text'].strip():
                c['words'] = []
                c['timestamp_origin'] = 'user-corrected'
        if edit != clip.edit:
            clip.edit, clip.version = edit, clip.version + 1
            p.updated_at = now()
        db.flush()
        return clip_json(db, clip)


@app.post('/api/clips/{clip_id}/render')
def render_clip(clip_id: str):
    with lock, Session.begin() as db:
        c = get(db, Clip, clip_id)
        active = db.scalar(select(Job).where(Job.clip_id == c.id, Job.edit_version == c.version, Job.kind == 'render', Job.status.in_(['queued', 'running', 'complete'])).order_by(Job.created_at.desc()))
        if active:
            if active.status != 'complete':
                return job_json(active)
            exports = db.scalars(select(Export).where(Export.clip_id == c.id, Export.version == c.version)).all()
            if any((worker.project_dir(e.project_id) / 'exports' / f'{e.id}.mp4').is_file() for e in exports):
                return job_json(active)
        j = Job(id=str(uuid4()), project_id=c.project_id, clip_id=c.id, kind='render', edit_version=c.version, spec={'edit': c.edit, 'framing': c.framing})
        db.add(j)
        db.flush()
        return job_json(j)


@app.get('/api/projects/{project_id}/exports.zip')
def project_archive(project_id: str):
    from .archives import zip_exports
    with Session() as db:
        p=get(db,Project,project_id)
        exports=list(db.scalars(select(Export).where(Export.project_id==p.id).order_by(Export.created_at.desc()).limit(201)))
        if not exports:raise HTTPException(404,'Render a clip before downloading a bundle.')
        if len(exports)>200:raise HTTPException(422,'This project has more than 200 exports. Download clips individually.')
        files=[]
        for e in exports:
            path=worker.project_dir(p.id)/'exports'/f'{e.id}.mp4'
            if not path.is_file():raise HTTPException(404,'An exported file is unavailable.')
            files.append((f'clip-{e.clip_id}-v{e.version}-{e.id[:8]}.mp4',path))
        return StreamingResponse(zip_exports(files),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="framecraft-{p.id}.zip"'})


@app.get('/api/projects/{project_id}/source')
def source(project_id: str):
    with Session() as db:
        p = get(db, Project, project_id)
        suffix = Path(p.filename).suffix.lower()
        mime = {'.webm': 'video/webm', '.mov': 'video/quicktime', '.mkv': 'video/x-matroska'}.get(suffix, 'video/mp4')
        return FileResponse(worker.project_dir(p.id) / 'source', media_type=mime)


@app.get('/api/projects/{project_id}/thumbnail')
def thumbnail(project_id: str):
    with Session() as db:
        p = get(db, Project, project_id)
        path = worker.project_dir(p.id) / 'analysis' / 'thumbnail.jpg'
        if not path.exists():
            raise HTTPException(404, 'Thumbnail is not ready yet.')
        return FileResponse(path, media_type='image/jpeg')


@app.get('/api/exports/{export_id}/{action}')
def export_file(export_id: str, action: str):
    if action not in ('preview', 'download'):
        raise HTTPException(404, 'Item not found.')
    with Session() as db:
        e = get(db, Export, export_id)
        path = worker.project_dir(e.project_id) / 'exports' / f'{e.id}.mp4'
        if not path.is_file():
            raise HTTPException(404, 'Export file is missing. Render a new edit version.')
        clip = get(db, Clip, e.clip_id)
        name = re.sub(r'[^\w\s.-]', '', clip.edit['title'])[:100] or 'Framecraft clip'
        return FileResponse(path, media_type='video/mp4', filename=f'{name}.mp4' if action == 'download' else None)


@app.delete('/api/projects/{project_id}', status_code=204)
def delete_project(project_id: str):
    with lock, Session.begin() as db:
        p = get(db, Project, project_id)
        active = db.scalar(select(Job).where(Job.project_id == p.id, Job.status.in_(['queued', 'running'])))
        draining = worker.active_job_id and db.get(Job, worker.active_job_id)
        if active or (draining and draining.project_id == p.id):
            raise HTTPException(409, 'Cancel or finish active jobs before deleting this project.')
        directory = worker.project_dir(p.id)
        # UUID-derived absolute path is always under this workspace's projects folder.
        if directory.resolve().parent != (DATA / 'projects').resolve():
            raise HTTPException(500, 'Storage path validation failed.')
        shutil.rmtree(directory, ignore_errors=False)
        for imported in db.scalars(select(ImportTask).where(ImportTask.project_id==p.id)):
            imported.project_id=None
            imported.stage='Original retained in source library; project deleted'
        for cls in (Export, Job, Clip):
            db.execute(delete(cls).where(cls.project_id == p.id))
        db.delete(p)


def youtube_id(url: str):
    try:
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError()
        host = (parsed.hostname or '').lower()
        if host in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
            parts = parsed.path.strip('/').split('/')
            value = parse_qs(parsed.query).get('v', [''])[0] if parsed.path == '/watch' else (parts[1] if len(parts) == 2 and parts[0] in ('shorts', 'embed', 'live') else '')
        elif host == 'youtu.be':
            value = parsed.path.strip('/')
        else:
            raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
            raise ValueError()
        return value
    except ValueError:
        raise HTTPException(422, 'Use a valid HTTPS YouTube watch, Shorts or youtu.be link.')


@app.get('/api/youtube')
async def youtube(url: str):
    video_id = youtube_id(url)
    canonical = f'https://www.youtube.com/watch?v={video_id}'
    result = {'video_id': video_id, 'url': canonical, 'embed_url': f'https://www.youtube-nocookie.com/embed/{video_id}',
              'title': None, 'author_name': None, 'thumbnail_url': None, 'metadata_status': 'unavailable', 'requires_upload': True}
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.get('https://www.youtube.com/oembed', params={'url': canonical, 'format': 'json'})
            if response.status_code == 200 and len(response.content) < 65536:
                data = response.json()
                result.update({k: str(data.get(k, ''))[:1000] for k in ('title', 'author_name', 'thumbnail_url')})
                result['metadata_status'] = 'available'
    except (httpx.HTTPError, ValueError):
        pass
    return result


from .import_routes import router as import_router
app.include_router(import_router)


frontend_dir=Path(os.getenv('FRAMECRAFT_FRONTEND_DIR',str(ROOT/'frontend'/'dist')))
if frontend_dir.is_dir():
    app.mount('/', StaticFiles(directory=frontend_dir, html=True), name='frontend')

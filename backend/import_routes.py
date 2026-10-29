"""Unified source discovery, source registry, imports and Drive connection API."""
import html
import logging
import re
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import Field, model_validator
from sqlalchemy import select
from .database import Session, MediaSource, SourceLink, ImportTask, Project, lock
from .schemas import AnalysisSettings, StrictModel
from . import import_service, drive, media_library
from .ingestion import IngestionError
from .secrets import SecretError
from .providers import youtube_id

router=APIRouter(prefix='/api')


class SourceResolve(StrictModel):
    url: str=Field(min_length=1,max_length=8192)


class ImportRequest(AnalysisSettings):
    settings: AnalysisSettings | None = None
    url: str|None=Field(default=None,max_length=8192)
    source_id: str|None=Field(default=None,max_length=36)
    title: str=Field(default='',max_length=200)
    youtube_url: str=Field(default='',max_length=8192)
    rights_confirmed: bool=False
    idempotency_key: str=Field(default_factory=lambda:str(uuid4()),min_length=1,max_length=100)

    @model_validator(mode='after')
    def identity(self):
        if not self.url and not self.source_id:
            raise ValueError('Choose a source or supply a video URL.')
        if self.source_id:
            self.source_id=str(UUID(self.source_id))
        return self


class LinkSource(StrictModel):
    source_id: str|None=Field(default=None,max_length=36)
    project_id: str|None=Field(default=None,max_length=36)
    youtube_url: str=Field(max_length=8192)
    rights_confirmed: bool=False


class RetryImport(StrictModel):
    url: str|None=Field(default=None,max_length=8192)


def require_id(value):
    try:
        return str(UUID(value))
    except ValueError:
        raise HTTPException(404,'Item not found.')


def call(operation,*args,**kwargs):
    try:
        return operation(*args,**kwargs)
    except (IngestionError,drive.DriveError,SecretError,ValueError) as exc:
        raise HTTPException(422,str(exc)[:700]) from None


@router.post('/sources/resolve')
def resolve_source(spec:SourceResolve):
    return call(import_service.resolve,spec.url)


@router.get('/sources')
def list_sources():
    with Session() as db:
        return [media_library.source_json(db,s) for s in db.scalars(select(MediaSource).order_by(MediaSource.created_at.desc()))]


@router.post('/sources/link')
def link_source(spec:LinkSource):
    if not spec.rights_confirmed:
        raise HTTPException(422,'Confirm permission to link this creator-stored original.')
    video_id=call(youtube_id,spec.youtube_url)
    with lock,Session.begin() as db:
        if spec.source_id:
            source=db.get(MediaSource,require_id(spec.source_id))
        elif spec.project_id:
            project=db.get(Project,require_id(spec.project_id))
            if not project:
                raise HTTPException(404,'Project not found.')
            source=call(media_library.register_project,db,project,rights_confirmed=True)
        else:
            raise HTTPException(422,'Choose a stored source or existing project original.')
        if not source or not media_library.available(source):
            raise HTTPException(422,'The original file is unavailable.')
        call(media_library.link_youtube,db,source,video_id)
        db.flush()
        return media_library.source_json(db,source)


@router.post('/imports',status_code=202)
def create_import(spec:ImportRequest):
    return call(import_service.create,spec)


@router.get('/imports')
def list_imports():
    with Session() as db:
        return [import_service.import_json(t) for t in db.scalars(select(ImportTask).order_by(ImportTask.created_at.desc()).limit(50))]


@router.get('/imports/{task_id}')
def import_status(task_id:str):
    with Session() as db:
        task=db.get(ImportTask,require_id(task_id))
        if not task:
            raise HTTPException(404,'Import not found.')
        return import_service.import_json(task)


@router.post('/imports/{task_id}/cancel')
def cancel_import(task_id:str):
    return call(import_service.pause,require_id(task_id))


@router.post('/imports/{task_id}/retry')
def retry_import(task_id:str,spec:RetryImport=RetryImport()):
    return call(import_service.retry,require_id(task_id),spec.url)


@router.delete('/imports/{task_id}')
def discard_import(task_id:str):
    return call(import_service.discard,require_id(task_id))


@router.get('/connections/google-drive')
def drive_status():
    return drive.connection_status()


@router.post('/connections/google-drive/authorize')
def drive_authorize():
    return {'authorization_url':call(drive.begin_auth)}


@router.get('/connections/google-drive/callback',response_class=HTMLResponse)
def drive_callback(code:str=Query(default='',max_length=4096),state:str=Query(default='',max_length=1024),error:str=Query(default='',max_length=200)):
    # Google errors and arbitrary query data are never reflected into HTML.
    success=False
    if error:
        message='Google Drive access was not approved. Return to Framecraft and connect again when ready.'
    else:
        try:
            drive.complete_auth(code,state)
            success=True
            message='Google Drive connected. Return to Framecraft and choose your video.'
        except (drive.DriveError,SecretError):
            message='Google Drive sign-in could not be completed. Return to Framecraft and start the connection again.'
    body='<!doctype html><html lang="en"><meta charset="utf-8"><title>Framecraft · Google Drive</title><body><main><h1>'+('Connected' if success else 'Connection needs attention')+'</h1><p>'+html.escape(message)+'</p><a href="/">Return to Framecraft</a></main></body></html>'
    return HTMLResponse(body,status_code=200 if success else 400,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"})


@router.get('/connections/google-drive/files')
def drive_files(query:str=Query(default='',max_length=200),page_token:str=Query(default='',max_length=4096)):
    return call(drive.list_videos,query,page_token)


@router.post('/connections/google-drive/disconnect')
def drive_disconnect():
    return call(drive.disconnect)


class OAuthLogPrivacy(logging.Filter):
    def filter(self,record):
        if isinstance(record.args,tuple) and len(record.args)==5:
            values=list(record.args)
            if str(values[2]).startswith('/api/connections/google-drive/callback'):
                values[2]='/api/connections/google-drive/callback'
                record.args=tuple(values)
        return True


logging.getLogger('uvicorn.access').addFilter(OAuthLogPrivacy())

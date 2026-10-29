"""Immutable, content-addressed originals independent of project lifetimes."""
import hashlib
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

from sqlalchemy import select
from .config import DATA, MAX_DISK
from .database import MediaSource, SourceLink, Project, now
from .ingestion import IngestionError
from .resources import check_disk_budget


def file_digest(path, checkpoint=lambda:None):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        while chunk := source.read(256*1024):
            checkpoint()
            digest.update(chunk)
    return digest.hexdigest()


def source_path(source):
    if not re.fullmatch(r'[0-9a-f]{64}', source.sha256 or ''):
        raise IngestionError('The stored source identity is invalid.')
    return DATA/'media_sources'/source.sha256/'source'


def available(source):
    try:
        path = source_path(source)
        return path.is_file() and not path.is_symlink() and path.stat().st_size == source.size_bytes
    except (OSError, IngestionError):
        return False


def copy_original(source, destination, checkpoint=lambda:None):
    """Hardlinks retain originals after deleting either owning project/library entry.

    The app never modifies a source in place. Cross-volume copies are bounded.
    Destination must be new; no existing source or export is overwritten.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError:
        created=False
        try:
            with Path(source).open('rb') as src, destination.open('xb') as dest:
                created=True
                while chunk:=src.read(256*1024):
                    checkpoint()
                    dest.write(chunk)
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise


def register_source(db, path, metadata, filename, title, provenance, checkpoint=lambda:None, verified_digest=None):
    if not provenance.get('rights_confirmed'):
        raise IngestionError('Confirm permission before adding a source to the library.')
    digest = verified_digest or file_digest(path, checkpoint)
    size = Path(path).stat().st_size
    destination=DATA/'media_sources'/digest/'source'
    if not destination.exists():
        budget=check_disk_budget(DATA)
        if budget['used_bytes']+size>MAX_DISK:
            raise IngestionError('Workspace storage limit cannot retain this reusable original. Free storage and retry.')
    source = db.scalar(select(MediaSource).where(MediaSource.sha256 == digest))
    if source is None:
        source = MediaSource(id=str(uuid4()), sha256=digest, filename=filename[:200], title=title[:200] or filename[:200],
                             size_bytes=size, media=metadata, provenance={**provenance,'sha256':digest,'size_bytes':size})
        db.add(source)
        db.flush()
    destination = source_path(source)
    if not destination.exists():
        copy_original(path, destination, checkpoint)
    elif destination.stat().st_size != size or file_digest(destination,checkpoint)!=digest:
        raise IngestionError('The stored original is damaged. Restore it before reusing this source.')
    return source


def link_youtube(db, source, video_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise IngestionError('Invalid YouTube video identity.')
    link = db.get(SourceLink, video_id)
    if link:
        if link.source_id != source.id:
            link.source_id, link.created_at = source.id, now()
    else:
        db.add(SourceLink(youtube_id=video_id, source_id=source.id))


def source_json(db, source):
    return {'id':source.id, 'title':source.title, 'filename':source.filename, 'size_bytes':source.size_bytes,
            'available':available(source), 'youtube_ids':list(db.scalars(select(SourceLink.youtube_id).where(SourceLink.source_id==source.id))),
            'metadata':source.media, 'created_at':source.created_at}


def register_project(db, project, *, rights_confirmed=False):
    provenance = dict((project.media or {}).get('provenance') or {})
    if not (provenance.get('rights_confirmed') or rights_confirmed):
        raise IngestionError('Confirm permission to reuse this project original.')
    provenance.update(rights_confirmed=True)
    return register_source(db, DATA/'projects'/project.id/'source', project.media, project.filename, project.title, provenance)


def backfill(db):
    """Register confirmed legacy originals; never infer rights from a filename."""
    for project in db.scalars(select(Project)):
        if not ((project.media or {}).get('provenance') or {}).get('rights_confirmed'):
            continue
        try:
            source = register_project(db, project)
            if project.youtube_url:
                from .providers import youtube_id
                video_id=youtube_id(project.youtube_url)
                if db.get(SourceLink,video_id) is None:
                    link_youtube(db, source, video_id)
        except (OSError, IngestionError):
            # Missing old media stays untouched and is simply not reusable.
            continue

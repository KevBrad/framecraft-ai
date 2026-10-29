"""SQLite WAL persistence. Versioned schema and transactional job transitions."""
from datetime import datetime, timezone
from threading import RLock
from sqlalchemy import Column, String, Integer, JSON, create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker
from .config import DATA

lock = RLock()
engine = create_engine(f'sqlite:///{(DATA / "framecraft.sqlite3").as_posix()}', connect_args={'check_same_thread': False, 'timeout': 30})


@event.listens_for(engine, 'connect')
def pragmas(connection, _):
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA foreign_keys=ON')


Base = declarative_base()
Session = sessionmaker(engine, expire_on_commit=False)


def now():
    return datetime.now(timezone.utc).isoformat()


class Project(Base):
    __tablename__ = 'projects'
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    created_at = Column(String, default=now)
    updated_at = Column(String, default=now)
    status = Column(String, default='queued')
    stage = Column(String, default='Queued')
    error = Column(String, nullable=True)
    filename = Column(String)
    youtube_url = Column(String, nullable=True)
    media = Column(JSON, default=dict)
    transcript = Column(JSON, nullable=True)
    settings = Column(JSON, default=dict)


class Clip(Base):
    __tablename__ = 'clips'
    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    candidate = Column(JSON, default=dict)
    edit = Column(JSON, default=dict)
    version = Column(Integer, default=1)
    framing = Column(JSON, default=dict)


class Job(Base):
    __tablename__ = 'jobs'
    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    clip_id = Column(String, nullable=True)
    kind = Column(String, nullable=False)
    status = Column(String, default='queued', index=True)
    stage = Column(String, default='Queued')
    error = Column(String, nullable=True)
    created_at = Column(String, default=now)
    updated_at = Column(String, default=now)
    edit_version = Column(Integer, nullable=True)
    spec = Column(JSON, default=dict)


class Export(Base):
    __tablename__ = 'exports'
    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    clip_id = Column(String, nullable=False, index=True)
    version = Column(Integer)
    created_at = Column(String, default=now)
    media = Column(JSON)


class MediaSource(Base):
    __tablename__ = 'media_sources'
    id = Column(String, primary_key=True)
    sha256 = Column(String, nullable=False, unique=True)
    title = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    media = Column(JSON, nullable=False)
    provenance = Column(JSON, nullable=False)
    created_at = Column(String, default=now)


class SourceLink(Base):
    __tablename__ = 'source_links'
    youtube_id = Column(String, primary_key=True)
    source_id = Column(String, nullable=False, index=True)
    created_at = Column(String, default=now)


class ImportTask(Base):
    __tablename__ = 'imports'
    id = Column(String, primary_key=True)
    provider = Column(String, nullable=False)
    status = Column(String, default='queued', index=True)
    stage = Column(String, default='Queued')
    bytes_received = Column(Integer, default=0)
    total_bytes = Column(Integer, nullable=True)
    attempts = Column(Integer, default=0)
    error = Column(String, nullable=True)
    project_id = Column(String, nullable=True)
    source_id = Column(String, nullable=True)
    source_ref = Column(JSON, default=dict)
    spec = Column(JSON, default=dict)
    idempotency_key = Column(String, unique=True, nullable=False)
    created_at = Column(String, default=now)
    updated_at = Column(String, default=now)


def migrate():
    """v2 adds import/library tables without rewriting any existing project data."""
    with engine.begin() as conn:
        version = conn.execute(text('PRAGMA user_version')).scalar()
        if version > 2:
            raise RuntimeError('Database is newer than this application.')
        Base.metadata.create_all(conn)
        conn.execute(text('PRAGMA user_version=2'))

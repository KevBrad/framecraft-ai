"""Concurrent API reads must not combine states from opposite sides of a commit."""
import sys
import threading
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


@pytest.mark.parametrize('endpoint', ['detail', 'list'])
def test_analysis_failure_is_a_coherent_project_snapshot(tmp_path, monkeypatch, endpoint):
    # This test neither starts a worker nor opens the user's project database.
    if 'backend.config' not in sys.modules:
        monkeypatch.setenv('FRAMECRAFT_DATA', str(tmp_path / 'unused-app-root'))
    from backend import main, worker
    from backend.database import Base, Project, Job

    engine = create_engine(f'sqlite:///{(tmp_path / "concurrent.sqlite3").as_posix()}',
                           connect_args={'check_same_thread': False, 'timeout': 5})
    @event.listens_for(engine, 'connect')
    def wal(connection, _):
        connection.execute('PRAGMA journal_mode=WAL')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    snapshot_lock = threading.RLock()
    monkeypatch.setattr(main, 'Session', sessions)
    monkeypatch.setattr(worker, 'Session', sessions)
    monkeypatch.setattr(main, 'lock', snapshot_lock)
    monkeypatch.setattr(worker, 'lock', snapshot_lock)
    pid, jid = str(uuid4()), str(uuid4())
    with sessions.begin() as db:
        db.add(Project(id=pid, title='Disposable concurrency fixture', status='processing',
                       stage='Loading local speech model', error=None))
        db.add(Job(id=jid, project_id=pid, kind='analysis', status='running',
                   stage='Loading local speech model', error=None))

    read_started = threading.Event()
    transition_attempted = threading.Event()
    transition_finished = threading.Event()
    writer_errors = []
    real_serialize = main.project_json

    def transition():
        try:
            assert read_started.wait(5), 'Reader did not reach its serialization boundary.'
            # Deterministically try the actual transition between the project's
            # initial SELECT and its subsequent jobs SELECT. If the reader owns
            # the shared lock, signal contention before waiting for its release.
            if snapshot_lock.acquire(blocking=False):
                try:
                    worker.fail(jid, 'Cannot load Whisper model: fixture failure')
                finally:
                    snapshot_lock.release()
                transition_attempted.set()
            else:
                transition_attempted.set()
                worker.fail(jid, 'Cannot load Whisper model: fixture failure')
        except BaseException as exc:
            writer_errors.append(exc)
            transition_attempted.set()
        finally:
            transition_finished.set()

    def serialize_during_transition(db, project):
        read_started.set()
        assert transition_attempted.wait(5), 'Writer did not reach its transition boundary.'
        return real_serialize(db, project)

    monkeypatch.setattr(main, 'project_json', serialize_during_transition)
    thread = threading.Thread(target=transition, daemon=True)
    thread.start()
    try:
        response = main.project(pid) if endpoint == 'detail' else main.projects()[0]
        assert transition_finished.wait(5), 'Writer remained blocked after the read returned.'
        thread.join(timeout=1)
        assert not writer_errors
        job = response['jobs'][0]
        # Either complete snapshot is valid. A failed job with a stale running
        # project / missing error is the real UI bug this test exposes.
        assert (response['status'], response['error'], job['status'], job['error']) in (
            ('processing', None, 'running', None),
            ('failed', 'Cannot load Whisper model: fixture failure',
             'failed', 'Cannot load Whisper model: fixture failure'),
        )
        with sessions() as db:
            assert db.get(Project, pid).status == 'failed'
            assert db.get(Project, pid).error == db.get(Job, jid).error
    finally:
        read_started.set()
        thread.join(timeout=5)
        engine.dispose()

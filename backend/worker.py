"""One durable queue consumer; expensive jobs run in killable child processes."""
import json
import os
import queue
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from .config import DATA, ROOT, JOB_TIMEOUT
from .database import Session, Project, Clip, Job, Export, lock, now
from .resources import contain_process, check_disk_budget

stop_event = threading.Event()
worker_thread = None
active_process = None
active_job_id = None


def project_dir(project_id):
    return DATA / 'projects' / str(uuid4() if project_id is None else project_id)


def cleanup_temporary(taskdir, directory):
    """Only remove app-owned abandoned staging files after its process is reaped."""
    for candidate in taskdir.glob('framecraft-render-*'):
        if candidate.is_dir() and not candidate.is_symlink() and candidate.resolve().parent == taskdir.resolve():
            for attempt in range(20):
                try:
                    shutil.rmtree(candidate)
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    time.sleep(.05)
    for candidate in (taskdir / 'output.mp4', directory / 'analysis' / 'pending-audio.wav', directory / 'analysis' / 'pending-proxy.mp4'):
        for attempt in range(20):
            try:
                candidate.unlink(missing_ok=True)
                break
            except OSError:
                # Windows can retain a killed descendant's file handle briefly.
                time.sleep(.05)


def terminate(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=15)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def start():
    global worker_thread
    stop_event.clear()
    with lock, Session.begin() as db:
        # Interrupted jobs are retryable with their original immutable spec.
        for job in db.scalars(select(Job).where(Job.status == 'running')):
            job.status, job.stage, job.updated_at = 'queued', 'Resuming after restart', now()
    worker_thread = threading.Thread(target=loop, name='framecraft-worker', daemon=True)
    worker_thread.start()


def stop():
    stop_event.set()
    if active_process:
        terminate(active_process)
    if worker_thread:
        worker_thread.join(timeout=20)


def loop():
    global active_job_id
    while not stop_event.is_set():
        with lock, Session.begin() as db:
            job = db.scalar(select(Job).where(Job.status == 'queued').order_by(Job.created_at).limit(1))
            job_id = job.id if job else None
            if job:
                job.status, job.stage, job.updated_at = 'running', 'Starting', now()
                active_job_id = job_id
        if not job_id:
            stop_event.wait(.4)
            continue
        try:
            execute(job_id)
        except Exception as exc:
            fail(job_id, str(exc)[:1500])
        finally:
            with lock:
                active_job_id = None


def fail(job_id, error):
    with lock, Session.begin() as db:
        job = db.get(Job, job_id)
        if job and job.status not in ('cancelled', 'complete'):
            job.status, job.stage, job.error, job.updated_at = 'failed', 'Failed', error, now()
            project = db.get(Project, job.project_id)
            if project and job.kind == 'analysis':
                project.status, project.stage, project.error = 'failed', 'Failed', error
                project.updated_at = now()


def execute(job_id):
    global active_process
    with Session() as db:
        job = db.get(Job, job_id)
        if not job or job.status != 'running':
            return
        pid, kind, spec, cid, version = job.project_id, job.kind, job.spec, job.clip_id, job.edit_version
    directory = project_dir(pid)
    check_disk_budget(DATA)
    taskdir = directory / 'jobs' / job_id
    taskdir.mkdir(parents=True, exist_ok=True)
    cleanup_temporary(taskdir, directory)
    task = {'kind': kind, 'source': str(directory / 'source'), 'workdir': str(directory / 'analysis'), 'spec': spec}
    if kind == 'render':
        task['output'] = str(taskdir / 'output.mp4')
    (taskdir / 'task.json').write_text(json.dumps(task), encoding='utf-8')
    gate = taskdir / 'run.ready'
    gate.unlink(missing_ok=True)
    lines = queue.Queue()
    creation = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    interpreter = sys.executable
    environment = os.environ.copy()
    if os.name == 'nt':
        # A venv redirector can create its interpreter child before assignment to
        # the Job Object. Start the actual interpreter, with this venv's packages,
        # so the gated process itself is always the contained root.
        interpreter = getattr(sys, '_base_executable', sys.executable)
        environment['__PYVENV_LAUNCHER__'] = sys.executable
    process = subprocess.Popen([interpreter, '-m', 'backend.task_runner', str(taskdir / 'task.json'), str(gate)], cwd=ROOT, env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', **creation)
    active_process = process
    containment = None
    try:
        containment = contain_process(process)
        gate.write_text('contained', encoding='ascii')
    except Exception:
        terminate(process)
        active_process = None
        raise
    def read_output():
        for line in process.stdout:
            lines.put(line)
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + JOB_TIMEOUT
    budget_check = time.monotonic()
    log = []
    try:
        while process.poll() is None or not lines.empty():
            with Session() as db:
                current = db.get(Job, job_id)
                cancelled = not current or current.status == 'cancelled'
            if cancelled or stop_event.is_set():
                terminate(process)
                return
            if time.monotonic() > deadline:
                terminate(process)
                raise RuntimeError(f'Job exceeded the {JOB_TIMEOUT}s time limit. Use a shorter video or a smaller model.')
            if time.monotonic() >= budget_check:
                containment.check()
                check_disk_budget(DATA)
                budget_check = time.monotonic() + 1
            try:
                line = lines.get(timeout=.2).strip()
            except queue.Empty:
                continue
            try:
                update = json.loads(line)
                stage = update.get('stage')
            except (ValueError, AttributeError):
                stage = None
            if stage:
                with lock, Session.begin() as db:
                    current = db.get(Job, job_id)
                    if not current or current.status != 'running':
                        continue
                    current.stage, current.updated_at = str(stage)[:200], now()
                    if kind == 'analysis':
                        project = db.get(Project, pid)
                        project.status, project.stage, project.updated_at = 'processing', current.stage, now()
            else:
                log.append(line)
                log = log[-50:]
        reader.join(timeout=2)
        result_file = taskdir / 'result.json'
        if process.returncode != 0 or not result_file.exists():
            # Never echo arbitrary media/transcript content into service logs.
            error_file = taskdir / 'error.json'
            error = json.loads(error_file.read_text(encoding='utf-8'))['error'] if error_file.exists() else 'Media worker exited unexpectedly. Check local dependencies and retry.'
            raise RuntimeError(error)
        result = json.loads(result_file.read_text(encoding='utf-8'))
        with lock, Session.begin() as db:
            job = db.get(Job, job_id)
            if not job or job.status == 'cancelled':
                return
            project = db.get(Project, pid)
            if kind == 'analysis':
                project.media, project.transcript = {**project.media, **result['metadata']}, result['transcript']
                # Existing edited clips remain available when analysis is retried.
                existing = list(db.scalars(select(Clip).where(Clip.project_id == pid)))
                for retained in existing:
                    retained.candidate = {**retained.candidate,'analysis_active':False}
                for candidate in result['clips']:
                    candidate = {**candidate,'analysis_active':True,'analysis_generation':job.id}
                    edit = candidate.pop('edit')
                    framing = candidate.pop('framing', result.get('framing', {}))
                    match = next((c for c in existing if abs(c.candidate['start'] - candidate['start']) < .05 and abs(c.candidate['end'] - candidate['end']) < .05), None)
                    if match:
                        match.candidate = candidate
                        match.framing = framing
                    else:
                        db.add(Clip(id=str(uuid4()), project_id=pid, candidate=candidate, edit=edit, version=1, framing=framing))
                project.status, project.stage, project.error = 'ready', 'Complete', None
            else:
                eid = str(uuid4())
                destination = directory / 'exports'
                destination.mkdir(exist_ok=True)
                (taskdir / 'output.mp4').replace(destination / f'{eid}.mp4')
                db.add(Export(id=eid, project_id=pid, clip_id=cid, version=version, media=result))
            project.updated_at = now()
            job.status, job.stage, job.updated_at = 'complete', 'Complete', now()
    finally:
        if process.poll() is None:
            terminate(process)
        if containment:
            containment.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        cleanup_temporary(taskdir, directory)
        active_process = None


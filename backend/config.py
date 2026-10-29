"""Local-only configuration, shared by API and isolated workers."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv('FRAMECRAFT_DATA', ROOT / 'data')).resolve()
MAX_UPLOAD = int(os.getenv('MAX_UPLOAD_MB', '1024')) * 1024 * 1024
MAX_DISK = int(os.getenv('MAX_DISK_GB', '20')) * 1024**3
JOB_TIMEOUT = int(os.getenv('JOB_TIMEOUT_SECONDS', '3600'))


def configure():
    # Native math libraries otherwise start a pool per available CPU, reserving
    # substantial private memory outside Whisper's explicitly bounded pool.
    # Set before NumPy/CTranslate2/ONNX imports; explicit user settings win.
    for key, value in {'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'OMP_NUM_THREADS': '4'}.items():
        os.environ.setdefault(key, value)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'projects').mkdir(exist_ok=True)
    os.environ.setdefault('MODEL_CACHE', str(DATA / 'models'))
    os.environ.setdefault('WHISPER_MODEL', 'tiny.en')
    for name in ('ffmpeg', 'ffprobe'):
        key = f'{name.upper()}_PATH'
        if key not in os.environ:
            candidates = list((ROOT / 'tools').glob(f'**/{name}.exe'))
            if candidates:
                os.environ[key] = str(candidates[0])


configure()

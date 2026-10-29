"""Download the official Whisper JFK test recording and mux original test graphics.

JFK's US federal government speech is public domain; provenance is recorded.
Visuals are generated test bars. No synthetic transcript is used for ASR tests.
"""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import configure
from backend.pipeline import _binary, _run, probe

URL = 'https://raw.githubusercontent.com/openai/whisper/main/tests/jfk.flac'


def main():
    destination = ROOT / 'tests' / 'artifacts'
    destination.mkdir(parents=True, exist_ok=True)
    audio = ROOT / 'tests' / 'jfk.flac'
    if not audio.exists():
        urllib.request.urlretrieve(URL, audio)
    video = destination / 'jfk-source.mp4'
    _run([_binary('ffmpeg'), '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=25',
          '-i', str(audio), '-shortest', '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)])
    identity = {'audio_source': URL, 'license_basis': 'JFK US federal government speech, public domain; visuals original FFmpeg testsrc2',
                'audio_sha256': hashlib.sha256(audio.read_bytes()).hexdigest(), 'video_sha256': hashlib.sha256(video.read_bytes()).hexdigest(), 'metadata': probe(video),
                'limitation': 'Human speech validates actual ASR; test graphics do not validate face detection or subjective highlight quality.'}
    (destination / 'fixture-provenance.json').write_text(json.dumps(identity, indent=2), encoding='utf-8')
    print(video)


if __name__ == '__main__':
    main()

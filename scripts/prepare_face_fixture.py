"""Fetch a pinned public-domain NASA photo and prepare synthetic test motion.

This is an informational software test, not an endorsement or a natural-motion,
speech, identity, diarization or active-speaker benchmark. Generated data stays
under ignored tests/artifacts and is never part of the public source package.
"""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import configure
from backend.pipeline import _binary, _run

URL = 'https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/astronaut.png'
EXPECTED = '88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5'


def main():
    configure()
    destination = ROOT / 'tests' / 'artifacts'
    destination.mkdir(parents=True, exist_ok=True)
    photo = destination / 'astronaut.png'
    if not photo.exists():
        urllib.request.urlretrieve(URL, photo)
    if hashlib.sha256(photo.read_bytes()).hexdigest() != EXPECTED:
        raise RuntimeError('NASA test photo checksum mismatch. No derived media was generated.')
    provenance = {
        'fixture': 'astronaut.png',
        'subject': 'NASA astronaut Eileen Collins',
        'credit': 'NASA, distributed as the scikit-image astronaut example image',
        'source_url': URL,
        'source_release': 'scikit-image v0.25.2',
        'sha256': EXPECTED,
        'license_evidence': [
            'https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.astronaut',
            'https://www.nasa.gov/nasa-brand-center/images-and-media/'
        ],
        'permitted_project_use': 'Informational software test only; no advertising, endorsement or identity inference.',
        'limitation': 'The derived video is a still photo with generated translation and sine audio, not natural person movement or speech.'
    }
    (destination / 'face-provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    source = destination / 'face-motion-source.mp4'
    proxy = destination / 'face-motion-proxy.mp4'
    _run([_binary('ffmpeg'), '-v', 'error', '-nostdin', '-y',
          '-f', 'lavfi', '-i', 'color=c=0x202630:s=960x540:r=30:d=4',
          '-loop', '1', '-i', str(photo), '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=4',
          '-filter_complex', "[1:v]scale=384:384[photo];[0:v][photo]overlay=x='160+45*t':y=78:shortest=1[v]",
          '-map', '[v]', '-map', '2:a', '-t', '4', '-c:v', 'libx264', '-preset', 'ultrafast',
          '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)])
    _run([_binary('ffmpeg'), '-v', 'error', '-nostdin', '-y', '-i', str(source), '-an', '-vf',
          'scale=640:360,fps=8', '-c:v', 'libx264', '-preset', 'ultrafast', str(proxy)])
    print('Prepared public-domain photo fixture and explicitly synthetic motion proxy.')


if __name__ == '__main__':
    main()

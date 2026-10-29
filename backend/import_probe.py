"""Gated, resource-contained validation of untrusted local import bytes."""
import json
from pathlib import Path
import sys
import time


def main():
    directory, gate = Path(sys.argv[1]), Path(sys.argv[2])
    began = time.monotonic()
    while not gate.is_file():
        if time.monotonic()-began > 15:
            return 1
        time.sleep(.02)
    from .config import configure
    configure()
    from .pipeline import probe, _run, _binary, INPUT_OPTIONS
    try:
        source = directory/'media'
        metadata = probe(source)
        # Reject containers whose apparent streams cannot actually be decoded.
        _run([_binary('ffmpeg'),'-v','error','-nostdin', '-threads','2',*INPUT_OPTIONS,'-i',str(source),
              '-t','2','-map',f"0:{metadata['video_stream_index']}",'-an','-f','null','-'],timeout=45)
        pending = directory/'probe.tmp'
        pending.write_text(json.dumps({'metadata':metadata}),encoding='utf-8')
        pending.replace(directory/'probe.json')
        return 0
    except Exception:
        # Do not expose ffmpeg strings that can contain untrusted media payloads.
        (directory/'probe-error.json').write_text(json.dumps({'error':'The downloaded file is not a supported, decodable video within the configured limits.'}),encoding='utf-8')
        return 1


if __name__=='__main__':
    raise SystemExit(main())

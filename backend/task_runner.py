"""Worker process entry; output is a tiny progress protocol, result stored atomically."""
import json
import os
import sys
import time
from pathlib import Path
from .config import configure


def main():
    configure()
    taskfile = Path(sys.argv[1])
    if len(sys.argv) > 2:
        deadline = time.monotonic() + 15
        while not Path(sys.argv[2]).is_file():
            if time.monotonic() > deadline:
                return 1
            time.sleep(.02)
    task = json.loads(taskfile.read_text(encoding='utf-8'))
    def report(stage):
        print(json.dumps({'stage': stage}), flush=True)
    try:
        from .pipeline import analyze, render
        if task['kind'] == 'analysis':
            result = analyze(Path(task['source']), Path(task['workdir']), task['spec'], report)
        else:
            report('Rendering')
            result = render(Path(task['source']), Path(task['output']), task['spec']['edit'], task['spec'].get('framing'))
            report('Validating export')
        temp = taskfile.with_name('result.tmp')
        temp.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        os.replace(temp, taskfile.with_name('result.json'))
    except Exception as exc:
        taskfile.with_name('error.json').write_text(json.dumps({'error': str(exc)[:1500]}), encoding='utf-8')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

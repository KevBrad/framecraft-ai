"""Real detector/render check using a public-domain photo with synthetic motion.

This is not evidence of human movement, speech or active-speaker recognition.
Image attribution and licensing sources: artifacts/face-provenance.json.
Run: .venv/Scripts/python.exe -m pytest tests/test_face_tracking.py -q
"""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from backend import config, pipeline


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "tests" / "artifacts"


def test_face_aware_motion_export():
    cv2 = pytest.importorskip("cv2")
    fixture = ARTIFACTS / "astronaut.png"
    if not fixture.is_file():
        pytest.skip("Public-domain astronaut fixture is absent; see face-provenance.json.")
    if not hasattr(cv2, "CascadeClassifier") or not hasattr(cv2, "data"):
        pytest.skip("This OpenCV build does not provide the frontal-face cascade API.")
    config.configure()
    try:
        ffmpeg = pipeline._binary("ffmpeg")
        pipeline._binary("ffprobe")
    except pipeline.PipelineError as error:
        pytest.skip(str(error))
    provenance = json.loads((ARTIFACTS / "face-provenance.json").read_text())
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == provenance["sha256"]
    source = ARTIFACTS / "face-motion-source.mp4"
    proxy = ARTIFACTS / "face-motion-proxy.mp4"
    output = ARTIFACTS / "face-smart-portrait.mp4"

    def run(arguments):
        result = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", *arguments],
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr

    run(["-f", "lavfi", "-i", "color=c=0x202630:s=960x540:r=30:d=4",
         "-loop", "1", "-i", str(fixture), "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
         "-filter_complex", "[1:v]scale=384:384[photo];[0:v][photo]overlay=x='160+45*t':y=78:shortest=1[v]",
         "-map", "[v]", "-map", "2:a", "-t", "4", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)])
    run(["-i", str(source), "-an", "-vf", "scale=640:360,fps=8", "-c:v", "libx264", "-preset", "ultrafast", str(proxy)])
    framing = pipeline.analyze_framing(proxy, 4, lambda stage: None)
    assert framing["method"] == "face-aware", framing
    assert framing["detected_samples"] >= 4, framing
    samples = framing["samples"]
    assert len(samples) >= 4
    max_step = max(abs(b["x"] - a["x"]) for a, b in zip(samples, samples[1:]))
    assert max_step <= 0.081, framing
    # A four-second, 8fps proxy yields samples at 0/1/2/3s; the exact
    # near-EOF seek may return no frame. Require meaningful rightward motion.
    assert samples[-1]["x"] - samples[0]["x"] > 0.05, framing

    edit = pipeline.default_edit("Synthetic motion / NASA test photo", 0, 4, [])
    edit.update(layout="smart", captions_enabled=False)
    metadata = pipeline.render(source, output, edit, framing)
    assert (metadata["width"], metadata["height"]) == (720, 1280)
    assert metadata["video_codec"] == "h264" and metadata["audio_codec"] == "aac"
    classifier = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
    capture = cv2.VideoCapture(str(output))
    frames, faces = [], []
    for timestamp in (0.25, 1.25, 2.25, 3.75):
        capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ok, frame = capture.read()
        assert ok
        small = cv2.resize(frame, (360, 640))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        boxes = classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
        assert len(boxes) >= 1, f"No complete face detected in rendered frame at {timestamp}s"
        x, y, width, height = [int(v) for v in max(boxes, key=lambda box: box[2] * box[3])]
        center = (x + width / 2) / 360
        assert 0.15 < center < 0.85, (timestamp, x, width)
        faces.append({"time": timestamp, "box_at_360x640": [x, y, width, height], "center_x": round(center, 4)})
        cv2.putText(small, f"{timestamp:.2f}s / synthetic motion", (8, 617), cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 255, 255), 1)
        cv2.rectangle(small, (x, y), (x + width, y + height), (96, 255, 96), 2)
        frames.append(small)
    capture.release()
    screenshots = ROOT / "docs" / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(screenshots / "face-framing.jpg"), cv2.hconcat(frames))
    (ARTIFACTS / "face-framing-result.json").write_text(json.dumps({
        "fixture_kind": "Still NASA photograph with synthetic horizontal translation and generated sine audio",
        "opencv_version": cv2.__version__, "framing": framing, "export": metadata,
        "sampled_render_faces": faces, "maximum_trajectory_x_step": max_step,
        "limitations": "Single frontal still photograph; no natural motion, occlusion, multi-person or speaker-identity evaluation."
    }, indent=2), encoding="utf-8")

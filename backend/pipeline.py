"""Genuine, local media analysis and rendering.

No remote clipping service or fabricated transcript is used.  Highlight scores
are deterministic editorial heuristics, not predictions of audience performance.
All timestamps in the public objects refer to the unmodified source video.
"""
from __future__ import annotations

import array
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable
import wave
import unicodedata
from functools import lru_cache


class PipelineError(RuntimeError):
    """A processing failure safe to show to a local user."""


ROOT = Path(__file__).resolve().parents[1]
SCORE_WEIGHTS = {
    "hook": 0.19, "completeness": 0.20, "density": 0.15,
    "topic": 0.15, "payoff": 0.12, "energy": 0.09, "confidence": 0.10,
}
OUTPUT_SIZES = {"9:16": (720, 1280), "1:1": (720, 720), "16:9": (1280, 720)}
INPUT_OPTIONS = ["-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm"]
_MODEL = None
_MODEL_KEY = None
_MODEL_LOCK = threading.Lock()


def _binary(name: str) -> str:
    configured = os.environ.get(f"{name.upper()}_PATH")
    found = configured or shutil.which(name)
    if not found:
        for folder in (ROOT / "tools" / "ffmpeg" / "bin", ROOT / ".tools" / "ffmpeg" / "bin"):
            candidate = folder / (name + (".exe" if os.name == "nt" else ""))
            if candidate.is_file():
                found = str(candidate)
                break
    if not found:
        raise PipelineError(f"{name} is unavailable. Install FFmpeg and set {name.upper()}_PATH in .env.")
    return str(found)


def dependency_status() -> dict:
    """Read-only readiness; does not download or load a speech model."""
    result = {}
    for name in ("ffmpeg", "ffprobe"):
        try:
            result[name] = _binary(name)
        except PipelineError:
            result[name] = None
    result.update(transcription=importlib.util.find_spec("faster_whisper") is not None,
                  model=os.environ.get("WHISPER_MODEL", "tiny.en"))
    return result


def _run(args: list[str], *, timeout: float = 120, cwd: Path | None = None,
         checkpoint: Callable[[], None] | None = None) -> str:
    """Run argument arrays without a shell and terminate on failure/cancellation."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen([str(a) for a in args], cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=flags)
    except OSError as exc:
        raise PipelineError(f"Cannot start {Path(args[0]).name}: {exc}") from exc
    started = time.monotonic()
    next_check = started
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if now - started > timeout:
                    raise PipelineError(f"{Path(args[0]).name} exceeded its {int(timeout)} second time limit.")
                if checkpoint and now >= next_check:
                    checkpoint()
                    next_check = now + 2
        if process.returncode:
            message = stderr.decode("utf-8", "replace")[-3500:].strip()
            raise PipelineError(f"{Path(args[0]).name} failed: {message or 'invalid or unsupported media'}")
        return stdout.decode("utf-8", "replace")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def _number(value, fallback=0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else fallback
    except (TypeError, ValueError):
        return fallback


def _clamp(value: float, low=0.0, high=1.0) -> float:
    return max(low, min(high, value))


def probe(path: Path) -> dict:
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise PipelineError("The media file is empty or missing.")
    raw = _run([_binary("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", *INPUT_OPTIONS, str(path)], timeout=45)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise PipelineError("ffprobe returned invalid media metadata.") from exc
    streams = data.get("streams", [])
    container = data.get("format", {}).get("format_name", "")
    if not ({"mov", "mp4", "matroska", "webm"} & set(container.split(","))):
        raise PipelineError("Unsupported media container. Upload an MP4, MOV or WebM video.")
    videos = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if not videos:
        raise PipelineError("No decodable video stream was found. Upload a video file, not audio or an image.")
    video = videos[0]
    duration = _number(data.get("format", {}).get("duration")) or _number(video.get("duration"))
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if duration <= 0 or width < 16 or height < 16 or not video.get("codec_name"):
        raise PipelineError("This file has no valid duration or video dimensions.")
    if duration > float(os.environ.get("MAX_MEDIA_DURATION", "7200")):
        raise PipelineError("This source exceeds the configured duration limit (default: two hours).")
    if max(width, height) > 7680:
        raise PipelineError("Video dimensions exceed the supported 7680-pixel limit.")
    rotation = _number(video.get("tags", {}).get("rotate"))
    for side in video.get("side_data_list", []):
        if "rotation" in side:
            rotation = _number(side["rotation"])
    if round(rotation) % 180 == 90:
        width, height = height, width
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate", "0/1")
    try:
        numerator, denominator = rate.split("/")
        fps = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError, AttributeError):
        fps = 0.0
    return {"duration": round(duration, 4), "width": width, "height": height,
            "fps": round(fps, 4), "video_codec": video["codec_name"],
            "audio_codec": audios[0].get("codec_name") if audios else None,
            "has_audio": bool(audios), "format": data.get("format", {}).get("format_name", ""),
            "rotation": rotation, "video_stream_index": video["index"],
            "audio_stream_index": audios[0]["index"] if audios else None,
            "video_duration": _number(video.get("duration")),
            "audio_duration": _number(audios[0].get("duration")) if audios else None}


def _write_json(path: Path, value) -> None:
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)


def audio_energy(path: Path, report: Callable[[str], None] | None = None) -> list[dict]:
    """Half-second RMS windows, measured from mono PCM rather than invented cues."""
    windows = []
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getnchannels() != 1:
            raise PipelineError("Analysis audio must be mono 16-bit PCM.")
        rate = audio.getframerate()
        index = 0
        while chunk := audio.readframes(rate // 2):
            if report and index % 120 == 0:
                report(f"Measuring audio energy · {index // 2}s")
            samples = array.array("h", chunk)
            if os.sys.byteorder != "little":
                samples.byteswap()
            rms = math.sqrt(sum(float(v) ** 2 for v in samples) / max(1, len(samples))) / 32768
            windows.append({"start": index / 2, "end": index / 2 + len(samples) / rate,
                            "dbfs": round(20 * math.log10(max(rms, 1e-9)), 2),
                            "silent": rms < 0.0056})
            index += 1
    return windows


def _get_model():
    global _MODEL, _MODEL_KEY
    model_name = os.environ.get("WHISPER_MODEL", "tiny.en")
    model_path = os.environ.get("WHISPER_MODEL_PATH") or model_name
    cache = Path(os.environ.get("MODEL_CACHE", str(ROOT / "data" / "models"))).resolve()
    key = (model_path, str(cache))
    with _MODEL_LOCK:
        if _MODEL is not None and _MODEL_KEY == key:
            return _MODEL
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise PipelineError("Speech transcription is unavailable. Install backend requirements, then retry analysis.") from exc
        cache.mkdir(parents=True, exist_ok=True)
        try:
            model = WhisperModel(model_path, device="cpu", compute_type="int8",
                                 cpu_threads=min(4, os.cpu_count() or 2), num_workers=1,
                                 download_root=str(cache))
        except Exception as exc:
            raise PipelineError(f"Cannot load Whisper model '{model_name}'. The first use requires a model download; check connectivity or set WHISPER_MODEL_PATH to a local CTranslate2 model. {exc}") from exc
        _MODEL, _MODEL_KEY = model, key
        return model


def transcribe(audio: Path, duration: float, report: Callable[[str], None]) -> dict:
    """Read bounded PCM windows; Whisper's feature extractor is not streaming.

    Two seconds of context on each side protect words at seams. A word belongs
    to the core window containing its midpoint. Persist completed cores so a
    failed/cancelled job can resume without transcribing earlier audio again.
    """
    import numpy as np
    try:
        core_seconds = int(os.environ.get("TRANSCRIBE_CHUNK_SECONDS", "30"))
        timeout = float(os.environ.get("TRANSCRIBE_TIMEOUT", "3600"))
        if not 10 <= core_seconds <= 60 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError
    except ValueError as exc:
        raise PipelineError("Use TRANSCRIBE_CHUNK_SECONDS between 10 and 60 and a positive TRANSCRIBE_TIMEOUT.") from exc
    audio = Path(audio)
    fingerprint = {"size": audio.stat().st_size, "mtime": audio.stat().st_mtime_ns,
                   "model": os.environ.get("WHISPER_MODEL_PATH") or os.environ.get("WHISPER_MODEL", "tiny.en"),
                   "core_seconds": core_seconds, "schema": 1, "beam_size": os.environ.get("WHISPER_BEAM_SIZE","3")}
    checkpoint = audio.with_name("transcription-progress.json")
    state = {}
    try:
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        if state.get("fingerprint") != fingerprint:
            state = {}
    except (OSError, ValueError):
        pass
    report("Loading local speech model")
    model = _get_model()
    began = time.monotonic()
    output = state.get("segments", [])
    language, probability = state.get("language"), state.get("language_probability", 0.0)
    completed = int(state.get("completed", 0))
    def progress(done, count, seconds):
        report(f"Transcribing speech · {min(duration, seconds):.0f}/{duration:.0f}s · chunk {done}/{count}")
        if time.monotonic() - began > timeout:
            raise PipelineError("Speech transcription reached its time limit. Retry analysis to resume saved audio chunks.")
    try:
        with wave.open(str(audio), "rb") as pcm:
            if pcm.getframerate() != 16000 or pcm.getnchannels() != 1 or pcm.getsampwidth() != 2:
                raise PipelineError("Transcription requires normalized 16kHz mono PCM.")
            rate, frames = pcm.getframerate(), pcm.getnframes()
            actual_duration = min(duration, frames / rate)
            core_frames = core_seconds * rate
            count = math.ceil(actual_duration / core_seconds)
            for index in range(completed, count):
                core_start, core_end = index * core_seconds, min(actual_duration, (index + 1) * core_seconds)
                left = max(0, index * core_frames - 2 * rate)
                right = min(frames, (index + 1) * core_frames + 2 * rate)
                progress(index, count, core_start)
                pcm.setpos(left)
                samples = np.frombuffer(pcm.readframes(right - left), dtype="<i2").astype(np.float32)
                samples /= 32768.0
                offset = left / rate
                options = {"language": language} if language and probability >= .6 else {}
                segments, info = model.transcribe(samples, beam_size=int(os.environ.get("WHISPER_BEAM_SIZE","3")), word_timestamps=True,
                                                  vad_filter=True, vad_parameters={"min_silence_duration_ms": 450},
                                                  condition_on_previous_text=False, **options)
                chunk_output = []
                for segment in segments:
                    progress(index, count, core_start)
                    start = _clamp(float(segment.start) + offset, 0, actual_duration)
                    end = _clamp(float(segment.end) + offset, 0, actual_duration)
                    if not segment.text.strip() or end <= start or (segment.no_speech_prob > .75 and segment.avg_logprob < -1):
                        continue
                    words = []
                    last_end = (chunk_output or output)[-1]["end"] if chunk_output or output else 0
                    for word in segment.words or []:
                        ws, we = _clamp(word.start + offset, start, end), _clamp(word.end + offset, start, end)
                        midpoint = (ws + we) / 2
                        if not core_start <= midpoint < core_end:
                            continue
                        ws = max(ws, last_end)
                        if round(we, 3) > round(ws, 3) and word.word.strip():
                            words.append({"start": round(ws, 3), "end": round(we, 3), "word": word.word,
                                          "probability": round(float(word.probability), 4)})
                            last_end = we
                    if segment.words:
                        if not words:
                            continue
                        start, end, text = words[0]["start"], words[-1]["end"], "".join(w["word"] for w in words).strip()
                    else:
                        if not core_start <= (start + end) / 2 < core_end:
                            continue
                        start, end, text = max(start, core_start, last_end), min(end, core_end), segment.text.strip()
                        if end <= start:
                            continue
                    chunk_output.append({"id": f"segment-{len(output) + len(chunk_output) + 1}",
                                         "start": round(start, 3), "end": round(end, 3), "text": text, "words": words,
                                         "avg_logprob": float(segment.avg_logprob),
                                         "no_speech_probability": float(segment.no_speech_prob)})
                # Drop generator references before creating the next feature array.
                del segments, samples
                if chunk_output and (not language or probability < .6):
                    language, probability = info.language, float(info.language_probability)
                output.extend(chunk_output)
                _write_json(checkpoint, {"fingerprint": fingerprint, "completed": index + 1,
                                         "language": language, "language_probability": probability, "segments": output})
                progress(index + 1, count, core_end)
    except PipelineError:
        raise
    except Exception as exc:
        # Preserve cancellation exceptions raised by the worker's report callback.
        if getattr(exc, "cancelled", False) or "cancel" in type(exc).__name__.lower():
            raise
        if isinstance(exc, MemoryError) or "allocate" in str(exc).lower():
            raise PipelineError("Speech recognition ran out of memory in an audio chunk. Close other heavy apps or set TRANSCRIBE_CHUNK_SECONDS=10, then retry. Saved chunks and the original video are preserved.") from exc
        raise PipelineError(f"Local speech recognition failed: {exc}. Retry to resume completed chunks.") from exc
    if not output:
        raise PipelineError("No reliable speech was recognized. Try a clearer speech-containing source or choose a multilingual WHISPER_MODEL.")
    return {"language": language, "language_probability": probability,
            "segments": output, "provider": "faster-whisper / CTranslate2 CPU int8",
            "model": os.environ.get("WHISPER_MODEL", "tiny.en"), "word_timestamps": True,
            "chunk_seconds": core_seconds, "context_seconds": 2, "audio_seconds": actual_duration}


def _sentences(transcript: dict, max_span: float = 12) -> list[dict]:
    """Sentence/pauses from actual words; segment timestamps are the fallback."""
    sentences, current = [], []
    ids = []

    def flush(complete=True):
        if current:
            sentences.append({"start": current[0]["start"], "end": current[-1]["end"],
                              "words": list(current), "text": "".join(w["word"] for w in current).strip(),
                              "segment_ids": list(dict.fromkeys(ids)), "complete": complete})
            current.clear()
            ids.clear()

    for segment in transcript["segments"]:
        words = segment.get("words", [])
        if not words:
            flush()
            sentences.append({**segment, "words": [], "segment_ids": [segment["id"]],
                              "complete": bool(re.search(r"[.!?…][\"')]*$", segment["text"]))})
            continue
        for word in words:
            if current and word["start"] - current[-1]["end"] > 0.55 and current[-1]["word"].strip().lower() not in ("mr.","mrs.","ms.","dr.","prof."):
                flush(True)
            if current and word["end"] - current[0]["start"] > max_span:
                flush(False)
            current.append(word)
            ids.append(segment["id"])
            if re.search(r"[.!?…][\"')]*$", word["word"].strip()) and word["word"].strip().lower() not in ("mr.","mrs.","ms.","dr.","prof.","st.","e.g.","i.e."):
                flush(True)
            elif current[-1]["end"] - current[0]["start"] >= max_span:
                # Extremely long unpunctuated speech needs a bounded word boundary.
                flush(False)
    flush(False)
    return sentences


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]{3,}", text.lower(), flags=re.UNICODE))


def _overlap(a: dict, b: dict) -> float:
    intersection = max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return intersection / max(0.01, min(a["end"] - a["start"], b["end"] - b["start"]))


def make_captions(sentences: list[dict], start: float, end: float) -> list[dict]:
    captions, group = [], []

    def flush():
        if not group:
            return
        captions.append({"id": f"caption-{len(captions) + 1}", "start": group[0]["start"],
                         "end": group[-1]["end"], "text": "".join(w["word"] for w in group).strip(),
                         "words": list(group)})
        group.clear()

    for sentence in sentences:
        if sentence["end"] <= start or sentence["start"] >= end:
            continue
        if not sentence.get("words"):
            flush()
            captions.append({"id": f"caption-{len(captions) + 1}", "start": max(start, sentence["start"]),
                             "end": min(end, sentence["end"]), "text": sentence["text"], "words": []})
            continue
        for word in sentence["words"]:
            if word["end"] <= start or word["start"] >= end:
                continue
            if group and (len(group) >= 7 or len("".join(w["word"] for w in group)) + len(word["word"]) > 38
                          or word["end"] - group[0]["start"] > 3.5):
                flush()
            group.append({**word, "start": max(start, word["start"]), "end": min(end, word["end"])})
        flush()
    return captions


def default_edit(title: str, start: float, end: float, captions: list[dict]) -> dict:
    return {"title": title, "start": start, "end": end, "aspect_ratio": "9:16", "layout": "smart",
            "crop_x": 0.5, "crop_y": 0.5, "caption_preset": "creator", "font_family": "DejaVu Sans",
            "font_size": 42, "text_color": "#FFFFFF", "highlight_color": "#B9F26B", "stroke": 2,
            "shadow": 1, "caption_x": 0.5, "caption_y": 0.82, "captions": captions, "captions_enabled": True}


def discover_clips_legacy(transcript: dict, duration: float, settings: dict, energy: list[dict] | None = None) -> list[dict]:
    """Sentence windows ranked by transparent, bounded, deterministic signals.

    Default weights: hook 19%, completeness 20%, density 15%, topic 15%,
    payoff 12%, audio energy 9%, model word confidence 10%. Unknown topic or
    confidence gives neutral 0.5, never a fabricated measurement. Diversity uses
    interval overlap/min-duration < .65 and token Jaccard < .82.
    """
    minimum = _clamp(_number(settings.get("min_duration"), 15), 2, 180)
    maximum = _clamp(_number(settings.get("max_duration"), 45), minimum, 180)
    sentences = _sentences(transcript, max_span=max(1, maximum - 0.5))
    if not sentences:
        return []
    topic = _tokens(settings.get("topic", ""))
    weights = dict(SCORE_WEIGHTS)
    configured = settings.get("score_weights", {})
    if isinstance(configured, dict):
        for key in weights:
            if key in configured:
                weights[key] = _clamp(_number(configured[key]), 0, 1)
    total_weight = sum(weights.values()) or 1
    candidates = []
    for i, first in enumerate(sentences):
        for j in range(i, len(sentences)):
            last = sentences[j]
            if last["end"] - first["start"] > maximum:
                break
            begin, finish = max(0, first["start"] - 0.12), min(duration, last["end"] + 0.18)
            # Context padding may fill silence, but must not nibble the next word
            # or the previous sentence when forced to split continuous speech.
            if i:
                begin = max(begin, sentences[i - 1]["end"])
            if j + 1 < len(sentences):
                finish = min(finish, max(last["end"], sentences[j + 1]["start"]))
            if finish - begin > maximum:
                finish = begin + maximum
            span = finish - begin
            if span < minimum and j + 1 < len(sentences):
                continue
            if span < minimum:
                if duration < minimum or (i == 0 and j == len(sentences) - 1):
                    # One honest short result when insufficient spoken content exists.
                    finish = min(duration, max(finish, begin + minimum))
                    begin = max(0, min(begin, finish - minimum))
                    span = finish - begin
                else:
                    continue
            selected = sentences[i:j + 1]
            text = " ".join(s["text"] for s in selected)
            tokens = _tokens(text)
            words = [w for s in selected for w in s.get("words", [])]
            opening = first["text"].lower()
            hook = 0.35 + 0.25 * bool("?" in first["text"]) + 0.25 * bool(re.search(r"\b(how|why|secret|mistake|imagine|never|surpris|best|learn|three|first)\w*", opening))
            density = _clamp(len(re.findall(r"\S+", text)) / max(span, 0.1) / 2.6)
            completeness = 0.65 + 0.2 * bool(last.get("complete")) + 0.15 * bool(first.get("complete"))
            if re.match(r"^(and|but|so|it|they|this|that)\b", opening):
                completeness -= 0.18
            payoff = 0.35 + 0.35 * bool(last.get("complete")) + 0.25 * bool(re.search(r"\b(because|therefore|result|remember|finally|means|instead|lesson|answer)\b", text.lower()))
            probabilities = [w["probability"] for w in words if "probability" in w]
            confidence = sum(probabilities) / len(probabilities) if probabilities else 0.5
            samples = [v["dbfs"] for v in energy or [] if v["end"] > begin and v["start"] < finish]
            emphasis = _clamp((sum(samples) / len(samples) + 55) / 40) if samples else 0.5
            components = {"hook": _clamp(hook), "completeness": _clamp(completeness), "density": density,
                          "topic": len(tokens & topic) / max(1, len(topic)) if topic else 0.5,
                          "payoff": _clamp(payoff), "energy": emphasis, "confidence": confidence}
            score = round(100 * sum(components[k] * weights[k] for k in weights) / total_weight)
            title = re.sub(r"\s+", " ", first["text"]).strip()
            if len(title) > 66:
                title = title[:63].rsplit(" ", 1)[0] + "…"
            explanation = (f"Editorial heuristic: hook {components['hook']:.0%}, sentence completeness {components['completeness']:.0%}, "
                           f"speech density {density:.0%}, topic match {components['topic']:.0%}, payoff {payoff:.0%}, "
                           f"measured audio energy {emphasis:.0%}, word confidence {confidence:.0%}. "
                           f"Weights: {'/'.join(str(round(weights[k] / total_weight * 100)) for k in weights)}; not a virality prediction.")
            candidate = {"title": title, "start": round(begin, 3), "end": round(finish, 3),
                         "duration": round(span, 3), "summary": text[:240], "hook": first["text"][:180],
                         "score": score, "score_explanation": explanation,
                         "score_components": {k: round(v, 4) for k, v in components.items()},
                         "segment_ids": list(dict.fromkeys(sid for s in selected for sid in s["segment_ids"])),
                         "provenance": "local-word-timestamps + deterministic-editorial-v1", "_tokens": tokens}
            if span < minimum:
                candidate["score_explanation"] += " Source is shorter than the requested minimum."
            candidate["edit"] = default_edit(title, candidate["start"], candidate["end"], make_captions(sentences, begin, finish))
            candidates.append(candidate)
    candidates.sort(key=lambda c: (-c["score"], c["start"], c["duration"]))
    chosen = []
    for candidate in candidates:
        if any(_overlap(candidate, other) >= 0.65 or
               len(candidate["_tokens"] & other["_tokens"]) / max(1, len(candidate["_tokens"] | other["_tokens"])) >= 0.82
               for other in chosen):
            continue
        chosen.append(candidate)
        if len(chosen) >= 6:
            break
    for candidate in chosen:
        candidate.pop("_tokens", None)
    return chosen


def discover_clips(transcript: dict, duration: float, settings: dict, energy=None, visual=None, context=None):
    # Original direct Python callers retain v1 behavior. New API settings and
    # analyze() select the explicit versioned engine; no saved clips are changed.
    if settings.get('analysis_version',1) == 1:
        return discover_clips_legacy(transcript,duration,settings,energy)
    from .editorial import discover
    return discover(transcript,duration,settings,energy,visual,context)


def analyze_framing_legacy(proxy: Path, duration: float, report: Callable[[str], None], intervals: list[tuple[float, float]] | None = None) -> dict:
    """Frontal Haar faces, continuity selection, velocity bounds and smoothing.

    This is face-aware composition, not active-speaker recognition or identity
    tracking. Missing detection smoothly returns to center after four seconds.
    """
    try:
        import cv2
    except ImportError:
        return {"method": "center-fallback", "samples": [{"time": 0, "x": 0.5, "y": 0.5}],
                "reason": "OpenCV unavailable; center crop and manual framing remain available."}
    cv2.setNumThreads(2)
    classifier_type = getattr(cv2, "CascadeClassifier", None) or getattr(getattr(cv2, "xobjdetect", None), "CascadeClassifier", None)
    if classifier_type is None or not hasattr(cv2, "data"):
        return {"method": "center-fallback", "samples": [{"time": 0, "x": 0.5, "y": 0.5}],
                "reason": "OpenCV face cascade API unavailable. Install opencv-python-headless>=4.10,<5 for automatic face framing."}
    classifier = classifier_type(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
    if classifier.empty():
        return {"method": "center-fallback", "samples": [{"time": 0, "x": 0.5, "y": 0.5}],
                "reason": "Frontal face cascade unavailable."}
    capture = cv2.VideoCapture(str(proxy))
    interval = 1.0 if intervals else max(1.0, duration / 180)
    timestamps = sorted({round(min(t, duration - .05), 3)
                         for start, end in intervals for t in [start + i for i in range(math.ceil(end-start))] + [end]}) if intervals else [min(i * interval, max(0, duration - .05)) for i in range(int(duration / interval) + 1)]
    samples, last_x, last_y, last_seen, detected = [], 0.5, 0.5, -100.0, 0
    try:
        for index, timestamp in enumerate(timestamps):
            report(f"Analyzing clip framing · {index + 1}/{len(timestamps)} samples")
            if samples and timestamp - samples[-1]["time"] > 4:
                last_x, last_y, last_seen = .5, .5, -100
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                                 minSize=(max(20, width // 25), max(20, height // 25)))
            target_x, target_y = last_x, last_y
            if len(faces):
                face = max(faces, key=lambda f: (f[2] * f[3]) / (1 + 3 * abs((f[0] + f[2] / 2) / width - last_x)))
                x, y, w, h = [int(v) for v in face]
                target_x = (x + w / 2) / width
                target_y = _clamp((y + h / 2) / height + 0.14)
                last_seen = timestamp
                detected += 1
            elif timestamp - last_seen > 4:
                target_x, target_y = 0.5, 0.5
            if (not samples or timestamp - samples[-1]["time"] > 4) and len(faces):
                last_x, last_y = target_x, target_y
            max_step = min(0.15, interval * 0.08)
            last_x += _clamp((target_x - last_x) * 0.32, -max_step, max_step)
            last_y += _clamp((target_y - last_y) * 0.32, -max_step, max_step)
            samples.append({"time": round(timestamp, 3), "x": round(_clamp(last_x), 5), "y": round(_clamp(last_y), 5)})
    finally:
        capture.release()
    return {"method": "face-aware" if detected else "center-fallback", "samples": samples or [{"time": 0, "x": 0.5, "y": 0.5}],
            "detected_samples": detected, "sample_interval": interval,
            "reason": "Frontal-face salience with continuity and smoothing; no active-speaker inference."}


def analyze_framing(proxy, duration, report, intervals=None):
    from .director import analyze_framing as direct
    return direct(proxy,duration,intervals,report)


def analyze(source: Path, workdir: Path, settings: dict, report: Callable[[str], None]) -> dict:
    from .model_profiles import configure_profile
    profile = configure_profile(settings.get('model_mode','balanced'),report)
    source, workdir = Path(source).resolve(), Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    report("Inspecting video")
    metadata = probe(source)
    if not metadata["has_audio"]:
        raise PipelineError("The video has no audio stream. Speech-based clipping requires audible speech.")
    fingerprint = {"size": source.stat().st_size, "mtime": source.stat().st_mtime_ns,
                   "model": os.environ.get("WHISPER_MODEL_PATH") or os.environ.get("WHISPER_MODEL", "tiny.en"), "schema": 3,
                   "chunk_seconds": os.environ.get("TRANSCRIBE_CHUNK_SECONDS", "30"), "beam_size": os.environ.get("WHISPER_BEAM_SIZE","3")}
    cache_file = workdir / "analysis-cache.json"
    cached = {}
    if cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("fingerprint") != fingerprint:
                cached = {}
        except (OSError, ValueError):
            cached = {}
    audio, proxy, thumbnail = workdir / "audio.wav", workdir / "proxy.mp4", workdir / "thumbnail.jpg"
    asset_key = {"size": fingerprint["size"], "mtime": fingerprint["mtime"], "normalization": 1}
    asset_file = workdir / "analysis-source.json"
    try:
        assets_valid = json.loads(asset_file.read_text(encoding="utf-8")) == asset_key
    except (OSError, ValueError):
        assets_valid = False
    limit = max(120, min(3600, metadata["duration"] * 3))
    if not assets_valid or not audio.is_file():
        report("Extracting normalized analysis audio")
        pending_audio = workdir / "pending-audio.wav"
        _run([_binary("ffmpeg"), "-v", "error", "-nostdin", "-y", *INPUT_OPTIONS, "-i", str(source),
              "-map", f"0:{metadata['audio_stream_index']}", "-vn", "-ac", "1", "-ar", "16000",
              "-af", "highpass=f=70", "-c:a", "pcm_s16le", str(pending_audio)], timeout=limit,
             checkpoint=lambda: report("Extracting normalized analysis audio"))
        pending_audio.replace(audio)
        # Commit asset identity independently of the eventual transcript, so a
        # cancelled analysis retains the WAV identity and completed audio cores.
        if not assets_valid:
            proxy.unlink(missing_ok=True)
            thumbnail.unlink(missing_ok=True)
        _write_json(asset_file, asset_key)
    report("Measuring audio energy and silence")
    energy = cached.get("energy") or audio_energy(audio, report)
    if not energy or max(e["dbfs"] for e in energy) < -55:
        raise PipelineError("The source is silent or too quiet for reliable speech recognition. No clips were fabricated.")
    transcript = cached.get("transcript") or transcribe(audio, metadata["duration"], report)
    # Save expensive model output even if later preview generation fails.
    cache = {"fingerprint": fingerprint, "transcript": transcript, "energy": energy}
    _write_json(cache_file, cache)
    from .editorial import source_map, VERSION as editorial_version
    report("Building source topic and question map")
    context = cached.get('content_map') if cached.get('content_map',{}).get('version') == editorial_version else None
    context = context or source_map(transcript, metadata['duration'])
    cache['content_map'] = context
    cache['model_profile'] = profile
    _write_json(cache_file, cache)
    report("Finding hooks, completing stories and comparing candidates")
    clips = discover_clips(transcript, metadata["duration"], {"analysis_version":3,**settings}, energy, context=context)
    if not clips:
        raise PipelineError("Speech was transcribed, but no valid clip fits these durations. Try a shorter minimum.")
    if not proxy.is_file():
        report("Creating analysis video proxy")
        pending_proxy = workdir / "pending-proxy.mp4"
        _run([_binary("ffmpeg"), "-v", "error", "-nostdin", "-y", *INPUT_OPTIONS, "-i", str(source),
              "-map", f"0:{metadata['video_stream_index']}", "-an", "-vf", "scale='min(640,iw)':-2,fps=8",
              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p", str(pending_proxy)],
             timeout=limit, checkpoint=lambda: report("Creating analysis video proxy"))
        pending_proxy.replace(proxy)
    if not thumbnail.is_file():
        report("Preparing project thumbnail")
        _run([_binary("ffmpeg"), "-v", "error", "-nostdin", "-y", "-ss", str(min(3, metadata["duration"] / 4)),
              *INPUT_OPTIONS, "-i", str(proxy), "-frames:v", "1", "-q:v", "3", str(thumbnail)], timeout=60)
    intervals = [[clip["start"], clip["end"]] for clip in clips]
    framing = cached.get("framing") if cached.get("framing_intervals") == intervals else None
    if not framing or framing.get("version") != "scene-director-v3.1" or "unavailable" in framing.get("reason", "").lower():
        framing = analyze_framing(proxy, metadata["duration"], report, intervals)
    cache["framing"] = framing
    cache["framing_intervals"] = intervals
    _write_json(cache_file, cache)
    for clip in clips:
        clip["framing"] = framing
        from .director import recommended_layout
        clip['edit']['layout'] = recommended_layout(framing,clip['start'],clip['end']) if settings.get('smart_reframing',True) else 'center' 
    report("Preparing editable clips")
    return {"metadata": {**metadata,"content_map":context,"model_profile":profile}, "transcript": transcript, "clips": clips, "framing": framing}


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, rest = divmod(centiseconds, 360000)
    minutes, rest = divmod(rest, 6000)
    seconds, centiseconds = divmod(rest, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{centiseconds:02}"


def _ass_color(value: str, alpha="00") -> str:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise PipelineError("Caption colors must be #RRGGBB values.")
    return f"&H{alpha}{value[5:7]}{value[3:5]}{value[1:3]}"


def _ass_text(text: str) -> str:
    # Prevent ASS override injection. Fullwidth braces preserve readable content.
    return _caption_plain_text(text).replace("\n", r"\N")


def _caption_plain_text(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text)).replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r", "").replace("\t", " ")


def _same_words(text: str, words: list[dict]) -> bool:
    normalize = lambda s: re.sub(r"\s+", "", s)
    return normalize(text) == normalize("".join(w.get("word", "") for w in words))


@lru_cache(maxsize=128)
def _caption_font(font_path: str, size: int):
    try:
        from PIL import ImageFont
    except ImportError as exc:
        raise PipelineError("Pillow is required to measure safe caption layouts. Install backend requirements and retry.") from exc
    return ImageFont.truetype(font_path, size)


def _grapheme_spans(text: str, start: int, end: int):
    """Keep combining marks, variation selectors and joined emoji together."""
    position = start
    while position < end:
        following = position + 1
        while following < end:
            character = text[following]
            if (unicodedata.combining(character) or character == "\u200d" or text[following - 1] == "\u200d"
                    or "\ufe00" <= character <= "\ufe0f" or "\U0001f3fb" <= character <= "\U0001f3ff"):
                following += 1
            else:
                break
        yield position, following
        position = following


def _wrap_caption_ranges(text: str, font, maximum_width: float, spacing: float) -> list[tuple[int, int]]:
    """Explicit, measured line ranges into the original text; no words removed."""
    ranges, line_start, line_end = [], None, None

    def measure(left, right):
        value = text[left:right]
        return float(font.getlength(value)) + max(0, len(value) - 1) * spacing

    def flush():
        nonlocal line_start, line_end
        if line_start is not None:
            ranges.append((line_start, line_end))
        line_start = line_end = None

    for token in re.finditer(r"\n|[^\S\n]+|[^\s]+", text):
        left, right = token.span()
        value = token.group()
        if value == "\n":
            if line_start is None:
                ranges.append((left, left))
            flush()
            continue
        if value.isspace():
            continue  # Whitespace between words stays inside the next range.
        if line_start is not None and measure(line_start, right) <= maximum_width:
            line_end = right
            continue
        flush()
        if measure(left, right) <= maximum_width:
            line_start, line_end = left, right
            continue
        # A long URL or unspaced phrase must wrap too. Split only at graphemes.
        for cluster_start, cluster_end in _grapheme_spans(text, left, right):
            if line_start is not None and measure(line_start, cluster_end) > maximum_width:
                flush()
            if line_start is None:
                line_start = cluster_start
            line_end = cluster_end
    flush()
    return ranges


def _safe_caption_layout(text: str, edit: dict, width: int, height: int, bold: bool) -> dict:
    font_name = edit.get("font_family", "DejaVu Sans")
    filenames = {("DejaVu Sans", False): "DejaVuSans.ttf", ("DejaVu Sans", True): "DejaVuSans-Bold.ttf",
                 ("Liberation Sans", False): "LiberationSans-Regular.ttf", ("Liberation Sans", True): "LiberationSans-Bold.ttf"}
    font_path = Path(os.environ.get("FONT_DIR", str(ROOT / "assets" / "fonts"))) / filenames[(font_name, bold)]
    if not font_path.is_file():
        raise PipelineError(f"The licensed caption font {font_path.name} is missing. Run the setup script or configure FONT_DIR.")
    requested_size = round(_clamp(_number(edit.get("font_size"), 42), 18, 100))
    margin_x, margin_y = width * .06, height * .06
    effects = _clamp(_number(edit.get("stroke"), 2), 0, 8) + _clamp(_number(edit.get("shadow"), 1), 0, 8) + 6
    spacing = .5 if edit.get("caption_preset") == "podcast" else 0
    # Pillow and libass use different rasterizers/line metrics. Reserve 12% for
    # that difference in addition to stroke, shadow and six-percent safe edges.
    measured_width = (width - margin_x * 2 - effects * 2) / 1.12
    for font_size in range(requested_size, 17, -1):
        actual_font = _caption_font(str(font_path), font_size)
        lines = _wrap_caption_ranges(text, actual_font, measured_width, spacing)
        line_height = max(sum(actual_font.getmetrics()), font_size * 1.25) * 1.12
        block_height = line_height * len(lines) + effects * 2
        if len(lines) <= 4 and block_height <= min(height * .45, height - margin_y * 2):
            line_widths = [float(actual_font.getlength(text[a:b])) + max(0, b-a-1) * spacing for a, b in lines]
            block_width = max(line_widths, default=0) * 1.12 + effects * 2
            x = _clamp(_number(edit.get("caption_x"), .5), .1, .9) * width
            y = _clamp(_number(edit.get("caption_y"), .82), .1, .9) * height
            x = _clamp(x, margin_x + block_width / 2, width - margin_x - block_width / 2)
            y = _clamp(y, margin_y + block_height, height - margin_y - effects)
            return {"size": font_size, "x": x, "y": y, "lines": lines,
                    "width": block_width, "height": block_height}
    raise PipelineError("This caption cannot fit safely in four readable lines. Split it into shorter captions or shorten its text.")


def _karaoke_content(words: list[dict], text: str, ranges: list[tuple[int, int]], cs: float, ce: float) -> str:
    """Insert explicit line breaks while preserving each original word interval."""
    pieces, cursor, offset, previous_line = [], cs, 0, 0
    for word in words:
        value = _caption_plain_text(word.get("word", ""))
        word_start, word_end = offset, offset + len(value)
        offset = word_end
        fragments = [(index, max(left, word_start), min(right, word_end))
                     for index, (left, right) in enumerate(ranges)
                     if min(right, word_end) > max(left, word_start)
                     and text[max(left, word_start):min(right, word_end)].strip()]
        if not fragments:
            continue
        ws = max(cs, _number(word.get("start")), cursor)
        we = max(ws, min(ce, _number(word.get("end"))))
        gap = max(0, round((ws - cursor) * 100))
        if gap:
            pieces.append(f"{{\\k{gap}}}\u200b")
        duration = max(0, round((we - ws) * 100))
        lengths = [len(text[left:right].strip()) for _, left, right in fragments]
        consumed, consumed_length = 0, 0
        for index, ((line_index, left, right), length) in enumerate(zip(fragments, lengths)):
            if line_index > previous_line:
                pieces.append(r"\N" * (line_index - previous_line))
            previous_line = line_index
            consumed_length += length
            target = duration if index == len(fragments) - 1 else round(duration * consumed_length / sum(lengths))
            ticks = target - consumed
            consumed += ticks
            pieces.append(f"{{\\kf{ticks}}}" + _ass_text(text[left:right]))
        cursor = we
    return "".join(pieces)


def write_ass(path: Path, edit: dict, width: int, height: int) -> None:
    from .caption_engine import PRESETS, write_ass as write_professional_ass
    if edit.get('caption_preset') in PRESETS:
        return write_professional_ass(path,edit,width,height)
    """Saved EditSpec is the sole caption-render input, including edited text."""
    start, end = float(edit["start"]), float(edit["end"])
    preset = edit.get("caption_preset", "creator")
    font = edit.get("font_family", "DejaVu Sans")
    if font not in ("DejaVu Sans", "Liberation Sans"):
        raise PipelineError("Unsupported caption font.")
    size = _clamp(_number(edit.get("font_size"), 42), 18, 100)
    primary = _ass_color(edit.get("text_color", "#FFFFFF"))
    highlight = _ass_color(edit.get("highlight_color", "#B9F26B"))
    stroke = _clamp(_number(edit.get("stroke"), 2), 0, 8)
    shadow = _clamp(_number(edit.get("shadow"), 1), 0, 8)
    bold = -1 if preset in ("creator", "karaoke", "podcast") else 0
    border = 3 if preset == "boxed" else 1
    back = "&H60000000" if preset == "boxed" else "&H80000000"
    if preset == "karaoke":
        primary, secondary = highlight, primary
    else:
        secondary = highlight
    header = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
              "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
              f"Style: Main,{font},{size},{primary},{secondary},&H00000000,{back},{bold},0,0,0,100,100,{0.5 if preset == 'podcast' else 0},0,{border},{stroke},{shadow},2,{round(width*.08)},{round(width*.08)},{round(height*.08)},1\n\n"
              "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    events = []
    for caption in edit.get("captions", []):
        cs, ce = max(start, _number(caption.get("start"))), min(end, _number(caption.get("end")))
        text = str(caption.get("text", "")).strip()
        if ce <= cs or not text:
            continue
        if len(text) > 1000:
            raise PipelineError("A caption is too long to render safely; split it into shorter captions.")
        words = caption.get("words") or []
        # Edited text wins over stale word timing; never silently render old words.
        karaoke = preset == "karaoke" and words and _same_words(text, words) and "\n" not in text
        if karaoke:
            words = [w for w in words if min(ce, _number(w.get("end"))) > max(cs, _number(w.get("start")))]
            karaoke = bool(words)
        if karaoke:
            plain = "".join(_caption_plain_text(w.get("word", "")) for w in words)
        else:
            plain = _caption_plain_text(text)
        layout = _safe_caption_layout(plain, edit, width, height, bold=bool(bold))
        overrides = f"{{\\q2\\pos({layout['x']:.1f},{layout['y']:.1f})\\fs{layout['size']:.1f}}}"
        if karaoke:
            content = _karaoke_content(words, plain, layout["lines"], cs, ce)
        else:
            content = r"\N".join(_ass_text(plain[left:right]) for left, right in layout["lines"])
        events.append(f"Dialogue: 0,{_ass_time(cs-start)},{_ass_time(ce-start)},Main,,0,0,0,,{overrides}{content}")
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")


def _interpolate(samples: list[dict], timestamp: float, key: str) -> float:
    if timestamp <= samples[0]["time"]:
        return samples[0][key]
    for left, right in zip(samples, samples[1:]):
        if timestamp <= right["time"]:
            proportion = (timestamp - left["time"]) / max(1e-9, right["time"] - left["time"])
            return left[key] + (right[key] - left[key]) * proportion
    return samples[-1][key]


def _trajectory_expression(framing: dict, key: str, start: float, end: float, dimension: int, crop: int) -> str:
    if framing.get('version') == 'scene-director-v3.1':
        from .director import trajectory_expression
        return trajectory_expression(framing,key,start,end,dimension,crop)
    samples = sorted([{ "time": _number(v.get("time")), key: _clamp(_number(v.get(key), 0.5))}
                      for v in framing.get("samples", [])], key=lambda v: v["time"])
    if not samples or dimension <= crop:
        return "0"
    # At most 32 interpolation knots keep FFmpeg expressions bounded.
    times = [start] + [v["time"] for v in samples if start < v["time"] < end] + [end]
    if len(times) > 32:
        times = [start + (end - start) * i / 31 for i in range(32)]
    points = [(t - start, _clamp(_interpolate(samples, t, key) * dimension - crop / 2, 0, dimension - crop)) for t in times]
    expr = f"{points[-1][1]:.3f}"
    for (t1, v1), (t2, v2) in reversed(list(zip(points, points[1:]))):
        line = f"({v1:.3f}+({v2-v1:.3f})*(t-{t1:.3f})/{max(.001,t2-t1):.3f})"
        expr = f"if(lt(t,{t2:.3f}),{line},{expr})"
    return expr


def render_filter(metadata: dict, edit: dict, framing: dict | None = None) -> tuple[str, tuple[int, int]]:
    ratio = edit.get("aspect_ratio", "9:16")
    if ratio not in OUTPUT_SIZES:
        raise PipelineError("Choose a supported 9:16, 1:1 or 16:9 aspect ratio.")
    output_width, output_height = OUTPUT_SIZES[ratio]
    if edit.get('output_resolution') == '1080p':
        output_width,output_height = output_width*3//2,output_height*3//2
    width, height = metadata["width"], metadata["height"]
    layout = edit.get("layout", "smart")
    if layout not in ("smart", "center", "manual", "fit"):
        raise PipelineError("Unsupported framing layout.")
    filters = ["setpts=PTS-STARTPTS"]
    if layout == "fit":
        filters += [f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease:force_divisible_by=2",
                    f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black"]
    else:
        aspect = output_width / output_height
        crop_width, crop_height = (int(height * aspect) // 2 * 2, height // 2 * 2) if width / height > aspect else (width // 2 * 2, int(width / aspect) // 2 * 2)
        crop_width, crop_height = max(2, crop_width), max(2, crop_height)
        x = f"{(width-crop_width)*(_clamp(_number(edit.get('crop_x'), .5)) if layout == 'manual' else .5):.3f}"
        y = f"{(height-crop_height)*(_clamp(_number(edit.get('crop_y'), .5)) if layout == 'manual' else .5):.3f}"
        if layout == "smart" and framing and framing.get("method") == "face-aware":
            x = _trajectory_expression(framing, "x", edit["start"], edit["end"], width, crop_width)
            y = _trajectory_expression(framing, "y", edit["start"], edit["end"], height, crop_height)
        filters += [f"crop={crop_width}:{crop_height}:x='{x}':y='{y}'", f"scale={output_width}:{output_height}:flags=lanczos"]
    filters.append("setsar=1")
    from .caption_engine import PRESETS
    if edit.get("captions_enabled", True) or (edit.get('caption_preset') in PRESETS and edit.get('opening_hook',False)):
        filters.append("ass=filename=captions.ass:fontsdir=fonts")
    if edit.get('caption_preset') not in PRESETS and edit.get('opening_hook',False):
        filters.append('ass=filename=opening-hook.ass:fontsdir=fonts')
    return ",".join(filters), (output_width, output_height)


def validate_export(output: Path, expected_duration: float, size: tuple[int, int], expects_audio: bool) -> dict:
    metadata = probe(output)
    if metadata["video_codec"] != "h264" or (metadata["width"], metadata["height"]) != size:
        raise PipelineError("Export validation failed: unexpected video codec or dimensions.")
    if "mp4" not in metadata.get("format", ""):
        raise PipelineError("Export validation failed: expected an MP4 container.")
    if expects_audio and (not metadata["has_audio"] or metadata["audio_codec"] != "aac"):
        raise PipelineError("Export validation failed: the expected AAC audio stream is missing.")
    if abs(metadata["duration"] - expected_duration) > max(0.35, expected_duration * 0.025):
        raise PipelineError(f"Export validation failed: duration {metadata['duration']:.2f}s differs from {expected_duration:.2f}s.")
    if expects_audio and metadata.get("video_duration") and metadata.get("audio_duration"):
        if abs(metadata["video_duration"] - metadata["audio_duration"]) > 0.35:
            raise PipelineError("Export validation failed: audio and video stream durations differ by over 350ms.")
    return metadata


def render(source: Path, output: Path, edit: dict, framing: dict | None = None) -> dict:
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output:
        raise PipelineError("The export path must differ from the original media path.")
    metadata = probe(source)
    start, end = _number(edit.get("start"), -1), _number(edit.get("end"), -1)
    if start < 0 or end <= start or end > metadata["duration"] + 0.05:
        raise PipelineError("The clip interval must be inside the source video and have a positive duration.")
    if end - start > 180:
        raise PipelineError("A single export may not exceed three minutes.")
    output.parent.mkdir(parents=True, exist_ok=True)
    filters, size = render_filter(metadata, edit, framing)
    with tempfile.TemporaryDirectory(prefix="framecraft-render-", dir=output.parent) as temporary:
        stage = Path(temporary)
        fonts = stage / "fonts"
        fonts.mkdir()
        font_source = Path(os.environ.get("FONT_DIR", str(ROOT / "assets" / "fonts")))
        if font_source.is_dir():
            for font in font_source.glob("*.ttf"):
                shutil.copyfile(font, fonts / font.name)
        if edit.get("captions_enabled", True) or edit.get("opening_hook",False):
            caption_edit=dict(edit)
            if edit.get('output_resolution') == '1080p':
                for prop in ('font_size','stroke','shadow'):
                    caption_edit[prop]=edit.get(prop,{'font_size':42,'stroke':2,'shadow':1}[prop])*1.5
            write_ass(stage / "captions.ass", caption_edit, *size)
            from .caption_engine import PRESETS,write_ass as modern_ass
            if edit.get('opening_hook') and edit.get('caption_preset') not in PRESETS:
                modern_ass(stage/'opening-hook.ass',{**caption_edit,'caption_preset':'clean-editorial','captions_enabled':False},*size)
        pending = stage / "output.mp4"
        command = [_binary("ffmpeg"), "-v", "error", "-nostdin", "-y", "-ss", f"{start:.6f}", *INPUT_OPTIONS, "-i", str(source),
                   "-t", f"{end-start:.6f}", "-map", f"0:{metadata['video_stream_index']}"]
        if metadata["has_audio"]:
            command += ["-map", f"0:{metadata['audio_stream_index']}", "-af", ("loudnorm=I=-16:TP=-1.5:LRA=11,aresample=async=1:first_pts=0" if edit.get("audio_normalize") else "aresample=async=1:first_pts=0"),
                        "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
        else:
            command += ["-an"]
        command += ["-vf", filters, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
                    "-r", "30", "-fps_mode", "cfr", "-movflags", "+faststart", "-threads", "4", str(pending)]
        _run(command, timeout=max(120, (end-start) * 12), cwd=stage)
        validated = validate_export(pending, end-start, size, metadata["has_audio"])
        pending.replace(output)
    validated['source_width']=metadata['width'];validated['source_height']=metadata['height']
    validated['upscaled']=size[0]>metadata['width'] or size[1]>metadata['height']
    validated['source_quality_note']='Output resolution does not recover source detail.' if validated['upscaled'] else 'Source dimensions support this output canvas.'
    return validated

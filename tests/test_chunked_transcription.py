"""Boundary/containment regressions; fake decoder tests are distinct from real ASR acceptance."""
import wave
from types import SimpleNamespace as NS

import numpy as np
import pytest

from backend import pipeline


def pcm_file(tmp_path, seconds=65):
    path = tmp_path / 'audio.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        for _ in range(seconds):
            audio.writeframes(np.full(16000, 1000, dtype='<i2').tobytes())
    return path


class Decoder:
    def __init__(self):
        self.lengths = []

    def transcribe(self, samples, **options):
        assert samples.dtype == np.float32
        self.lengths.append(len(samples))
        # Same absolute speech is emitted in overlapping windows. Each word
        # must be owned by exactly one core, with original source timestamps.
        offset = [0, 28, 58][len(self.lengths) - 1]
        words = [NS(start=t-offset, end=t+.4-offset, word=f' word{t}', probability=.95)
                 for t in range(offset, min(65, offset+len(samples)//16000))]
        segment = NS(start=0, end=len(samples)/16000, text=' speech', words=words,
                     no_speech_prob=.01, avg_logprob=-.1)
        return iter([segment]), NS(language='en', language_probability=.99)


def test_bounded_windows_stitch_without_duplicate_words(tmp_path, monkeypatch):
    decoder = Decoder()
    monkeypatch.setattr(pipeline, '_get_model', lambda: decoder)
    stages = []
    result = pipeline.transcribe(pcm_file(tmp_path), 65, stages.append)
    assert decoder.lengths == [32*16000, 34*16000, 7*16000]
    words = [w for s in result['segments'] for w in s['words']]
    assert [w['word'] for w in words] == [f' word{t}' for t in range(65)]
    assert all(a['end'] <= b['start'] for a, b in zip(words, words[1:]))
    assert words[-1]['end'] == 64.4
    assert any('65/65s' in stage for stage in stages)


def test_cancelled_retry_resumes_completed_cores(tmp_path, monkeypatch):
    decoder = Decoder()
    monkeypatch.setattr(pipeline, '_get_model', lambda: decoder)
    path = pcm_file(tmp_path)
    class Cancelled(Exception):
        cancelled = True
    def cancel(stage):
        if '30/65s' in stage:
            raise Cancelled()
    with pytest.raises(Cancelled):
        pipeline.transcribe(path, 65, cancel)
    assert len(decoder.lengths) == 1
    result = pipeline.transcribe(path, 65, lambda _: None)
    assert len(decoder.lengths) == 3  # no repeat of first 30 seconds
    assert len([w for s in result['segments'] for w in s['words']]) == 65


def test_empty_chunks_timeout_and_invalid_configuration(tmp_path, monkeypatch):
    class Empty:
        def transcribe(self, samples, **options):
            return iter([]), NS(language='en', language_probability=.99)
    monkeypatch.setattr(pipeline, '_get_model', lambda: Empty())
    path = pcm_file(tmp_path, 1)
    with pytest.raises(pipeline.PipelineError, match='No reliable speech'):
        pipeline.transcribe(path, 1, lambda _: None)
    monkeypatch.setenv('TRANSCRIBE_CHUNK_SECONDS', '100000')
    with pytest.raises(pipeline.PipelineError, match='between 10 and 60'):
        pipeline.transcribe(path, 1, lambda _: None)


def test_memory_error_is_actionable_and_original_survives(tmp_path, monkeypatch):
    class Failing:
        def transcribe(self, *args, **kwargs):
            raise MemoryError('allocate')
    monkeypatch.setattr(pipeline, '_get_model', lambda: Failing())
    path = pcm_file(tmp_path, 1)
    before = path.read_bytes()
    with pytest.raises(pipeline.PipelineError, match='TRANSCRIBE_CHUNK_SECONDS=10'):
        pipeline.transcribe(path, 1, lambda _: None)
    assert path.read_bytes() == before

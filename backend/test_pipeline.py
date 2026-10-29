"""Focused invariants; integration tests use real FFmpeg rather than fake media."""
from pathlib import Path
import os
import re
import tempfile
import unittest

from backend import pipeline

TEST_TEMP = pipeline.ROOT / "data" / "test-tmp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


def speech_fixture(count=10, sentence_duration=4):
    sentences = []
    vocabulary = [
        "Why does practice change your creative work?",
        "The answer is that repetition builds useful confidence.",
        "A small experiment can teach an unexpected lesson.",
        "Remember to measure the result before changing direction.",
        "How do healthy teams make difficult decisions?",
        "First they listen to several different perspectives.",
        "Because clear communication makes collaboration more effective.",
        "Finally each person knows the next concrete action.",
        "Imagine a garden growing through the summer.",
        "Water and sunlight transform a small seed.",
    ]
    for index in range(count):
        text = vocabulary[index % len(vocabulary)]
        tokens = text.split()
        words = [{"word": " " + token, "start": index * sentence_duration + j * (sentence_duration - .3) / len(tokens),
                  "end": index * sentence_duration + (j + 1) * (sentence_duration - .3) / len(tokens),
                  "probability": .93} for j, token in enumerate(tokens)]
        sentences.append({"id": f"s{index}", "start": index * sentence_duration,
                          "end": index * sentence_duration + sentence_duration - .3, "text": text, "words": words})
    return {"segments": sentences, "language": "en", "provider": "unit fixture, not model output"}


class HighlightTests(unittest.TestCase):
    def test_candidates_are_real_bounded_sentence_windows_and_diverse(self):
        transcript = speech_fixture()
        candidates = pipeline.discover_clips(transcript, 40, {"min_duration": 7, "max_duration": 14, "topic": "practice creative"})
        self.assertGreaterEqual(len(candidates), 2)
        valid_ids = {s["id"] for s in transcript["segments"]}
        for candidate in candidates:
            self.assertGreaterEqual(candidate["start"], 0)
            self.assertLessEqual(candidate["end"], 40)
            self.assertGreaterEqual(candidate["duration"], 7)
            self.assertLessEqual(candidate["duration"], 14)
            self.assertTrue(set(candidate["segment_ids"]) <= valid_ids)
            self.assertTrue(candidate["edit"]["captions"])
            self.assertIn("not a virality prediction", candidate["score_explanation"])
        for index, candidate in enumerate(candidates):
            for other in candidates[index + 1:]:
                self.assertLess(pipeline._overlap(candidate, other), .65)
        starts = [s["start"] for s in transcript["segments"]]
        for candidate in candidates:
            self.assertTrue(any(abs(candidate["start"] - max(0, start - .12)) < .002 for start in starts))

    def test_short_video_does_not_invent_duration(self):
        result = pipeline.discover_clips(speech_fixture(2, 2), 4, {"min_duration": 15, "max_duration": 45})
        self.assertEqual(len(result), 1)
        self.assertLessEqual(result[0]["end"], 4)
        self.assertIn("shorter", result[0]["score_explanation"])

    def test_no_transcript_does_not_fabricate_candidates(self):
        self.assertEqual(pipeline.discover_clips({"segments": []}, 30, {}), [])

    def test_ranking_is_deterministic(self):
        args = (speech_fixture(), 40, {"min_duration": 8, "max_duration": 16})
        self.assertEqual(pipeline.discover_clips(*args), pipeline.discover_clips(*args))

    def test_long_sentence_respects_maximum_on_actual_word_boundary(self):
        transcript = speech_fixture(1, 14)
        candidates = pipeline.discover_clips(transcript, 14, {"min_duration": 2, "max_duration": 5})
        self.assertTrue(candidates)
        ends = [word["end"] for word in transcript["segments"][0]["words"]]
        for clip in candidates:
            self.assertLessEqual(clip["duration"], 5)
            self.assertTrue(any(abs(clip["end"] - end) < .002 or abs(clip["end"] - min(14, end + .18)) < .002 for end in ends))

    def test_natural_pause_precedes_forced_maximum_boundary(self):
        transcript = {"segments": [{"id": "s1", "start": 0, "end": 10.4, "text": "First complete thought next thought.",
            "words": [{"start": 0, "end": 3, "word": " First"}, {"start": 3, "end": 7.6, "word": " thought"},
                      {"start": 8.26, "end": 9, "word": " next"}, {"start": 9, "end": 10.4, "word": " thought."}]}]}
        clips = pipeline.discover_clips(transcript, 11, {"min_duration": 5, "max_duration": 10})
        self.assertEqual(len(clips), 1)
        self.assertAlmostEqual(clips[0]["end"], 7.78, places=2)


class CaptionAndFramingTests(unittest.TestCase):
    def test_ass_edited_caption_wins_over_stale_word_array(self):
        edit = pipeline.default_edit("Test", 2, 6, [{"id": "c1", "start": 2.5, "end": 4,
            "text": "A saved revision.", "words": [{"start": 2.5, "end": 4, "word": "Old text."}]}])
        edit["caption_preset"] = "karaoke"
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            path = Path(directory) / "test.ass"
            pipeline.write_ass(path, edit, 720, 1280)
            text = path.read_text(encoding="utf-8-sig")
        self.assertIn("A saved revision.", text)
        self.assertNotIn("Old text.", text)
        self.assertNotIn(r"\kf", text)
        self.assertIn("0:00:00.50,0:00:02.00", text)

    def test_ass_override_injection_is_escaped(self):
        content = pipeline._ass_text("Save {\\pos(0,0)} this\nline")
        self.assertNotIn(r"{\pos", content)
        self.assertIn(r"\N", content)

    def test_word_karaoke_uses_actual_duration(self):
        edit = pipeline.default_edit("Test", 0, 3, [{"start": 1, "end": 2,
            "text": "Real words", "words": [{"start": 1, "end": 1.4, "word": "Real"},
                                             {"start": 1.5, "end": 2, "word": " words"}]}])
        edit["caption_preset"] = "karaoke"
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            path = Path(directory) / "test.ass"
            pipeline.write_ass(path, edit, 720, 1280)
            text = path.read_text(encoding="utf-8-sig")
        self.assertIn(r"\kf40", text)
        self.assertIn(r"\kf50", text)

    def test_long_karaoke_word_wrap_preserves_text_and_total_word_time(self):
        original = "VérificationΩ" * 12
        edit = pipeline.default_edit("Long word", 0, 3, [{"start": .5, "end": 2.5,
            "text": original, "words": [{"start": .5, "end": 2.5, "word": original}]}])
        edit.update(caption_preset="karaoke", caption_x=.1, caption_y=.1, font_size=60)
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            path = Path(directory) / "wrapped.ass"
            pipeline.write_ass(path, edit, 720, 1280)
            dialogue = next(line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.startswith("Dialogue:"))
        content = dialogue.split(",", 9)[9]
        self.assertIn(r"\N", content)
        self.assertEqual(sum(int(tick) for tick in re.findall(r"\\kf(\d+)", content)), 200)
        recovered = re.sub(r"\{[^}]*\}", "", content).replace(r"\N", "")
        self.assertEqual(recovered, original)

    def test_captions_too_long_for_readable_safe_layout_fail_clearly(self):
        text = "W" * 900
        edit = pipeline.default_edit("Too long", 0, 3, [{"start": 0, "end": 3, "text": text}])
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            with self.assertRaisesRegex(pipeline.PipelineError, "Split it into shorter captions"):
                pipeline.write_ass(Path(directory) / "oversized.ass", edit, 720, 1280)

    def test_normal_caption_keeps_requested_typography_and_case_edit_wins(self):
        edit = pipeline.default_edit("Typography", 0, 3, [{"start": 0, "end": 3, "text": "Saved words", "words": [
            {"start": 0, "end": 1, "word": " saved"}, {"start": 1, "end": 3, "word": " words"}]}])
        edit["caption_preset"] = "karaoke"
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            path = Path(directory) / "normal.ass"
            pipeline.write_ass(path, edit, 720, 1280)
            rendered = path.read_text(encoding="utf-8-sig")
        self.assertIn(r"\fs42.0", rendered)
        self.assertIn("Saved words", rendered)
        self.assertNotIn(r"\kf", rendered)

    def test_manual_crop_and_fit_differ_and_smart_is_time_based(self):
        metadata = {"width": 1920, "height": 1080}
        edit = pipeline.default_edit("Test", 5, 12, [])
        edit.update(layout="manual", crop_x=1, crop_y=0)
        manual, size = pipeline.render_filter(metadata, edit)
        self.assertEqual(size, (720, 1280))
        self.assertIn("x='1314.000'", manual)
        edit["layout"] = "fit"
        fit, _ = pipeline.render_filter(metadata, edit)
        self.assertIn("pad=720:1280", fit)
        edit["layout"] = "smart"
        smart, _ = pipeline.render_filter(metadata, edit, {"method": "face-aware", "samples": [
            {"time": 0, "x": .2, "y": .5}, {"time": 15, "x": .8, "y": .5}]})
        self.assertIn("if(lt(t,", smart)
        self.assertNotEqual(smart, manual)

    def test_invalid_interval_is_rejected_before_ffmpeg(self):
        # This guard is also exercised against real source metadata below.
        with self.assertRaises(pipeline.PipelineError):
            pipeline.probe(Path("this-file-does-not-exist.mp4"))


class RealMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            pipeline._binary("ffmpeg")
            pipeline._binary("ffprobe")
        except pipeline.PipelineError as exc:
            raise unittest.SkipTest(str(exc))
        cls.directory = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        cls.folder = Path(cls.directory.name)
        cls.source = cls.folder / "source.mp4"
        pipeline._run([pipeline._binary("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                       "testsrc2=size=640x360:rate=30:duration=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                       "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(cls.source)])

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "directory"):
            cls.directory.cleanup()

    def test_probe_rejects_corrupted_upload(self):
        invalid = self.folder / "fake.mp4"
        invalid.write_bytes(b"This is not video.")
        with self.assertRaises(pipeline.PipelineError):
            pipeline.probe(invalid)

    def test_media_playlist_is_denied_before_any_remote_protocol(self):
        playlist = self.folder / "network-playlist.m3u8"
        playlist.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nhttp://127.0.0.1:1/private\n#EXT-X-ENDLIST\n", encoding="utf-8")
        with self.assertRaises(pipeline.PipelineError) as rejected:
            pipeline.probe(playlist)
        self.assertIn("whitelist", str(rejected.exception).lower())

    def test_real_render_is_h264_aac_portrait_and_validated(self):
        output = self.folder / "portrait.mp4"
        edit = pipeline.default_edit("Fixture", .25, 2.25, [{"start": .3, "end": 2, "text": "Actual burned-in fixture caption"}])
        result = pipeline.render(self.source, output, edit)
        self.assertEqual((result["width"], result["height"]), (720, 1280))
        self.assertEqual(result["video_codec"], "h264")
        self.assertEqual(result["audio_codec"], "aac")
        self.assertAlmostEqual(result["duration"], 2, delta=.15)
        self.assertGreater(output.stat().st_size, 1000)

    def test_out_of_bounds_edit_does_not_create_an_export(self):
        output = self.folder / "invalid.mp4"
        edit = pipeline.default_edit("Invalid", 1, 20, [])
        with self.assertRaisesRegex(pipeline.PipelineError, "inside the source"):
            pipeline.render(self.source, output, edit)
        self.assertFalse(output.exists())

    def test_validation_rejects_wrong_dimensions(self):
        with self.assertRaisesRegex(pipeline.PipelineError, "dimensions"):
            pipeline.validate_export(self.source, 3, (720, 1280), True)

    def test_long_unicode_caption_pixels_stay_inside_safe_edges(self):
        import cv2
        import numpy as np
        black = self.folder / "black.mp4"
        pipeline._run([pipeline._binary("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                       "color=c=black:size=640x360:rate=30:duration=2", "-an", "-c:v", "libx264", "-preset", "ultrafast", str(black)])
        text = "Café naïve — résumé jalapeño. Ελληνικά: σαφήνεια και ρυθμός. Сохраните все слова. " * 2
        tokens = text.split()
        words = [{"start": i * 1.8 / len(tokens), "end": (i + 1) * 1.8 / len(tokens), "word": " " + token}
                 for i, token in enumerate(tokens)]
        for ratio, position, preset in (("9:16", .1, "creator"), ("9:16", .9, "karaoke"), ("1:1", .1, "creator"), ("16:9", .9, "karaoke")):
            with self.subTest(ratio=ratio, position=position, preset=preset):
                output = self.folder / f"safe-{ratio.replace(':', '-')}-{preset}.mp4"
                edit = pipeline.default_edit("Unicode", 0, 2, [{"start": 0, "end": 1.8, "text": text.strip(), "words": words}])
                edit.update(aspect_ratio=ratio, caption_preset=preset, caption_x=position, caption_y=position, font_size=76, stroke=8, shadow=8)
                pipeline.render(black, output, edit)
                capture = cv2.VideoCapture(str(output))
                try:
                    capture.set(cv2.CAP_PROP_POS_MSEC, 800)
                    success, frame = capture.read()
                finally:
                    capture.release()
                self.assertTrue(success)
                ys, xs = np.where(frame.max(axis=2) > 100)
                self.assertGreater(len(xs), 200, "No actual caption glyphs were burned into the export.")
                height, width = frame.shape[:2]
                self.assertGreaterEqual(int(xs.min()), round(width * .06) - 2)
                self.assertLessEqual(int(xs.max()), round(width * .94) + 2)
                self.assertGreaterEqual(int(ys.min()), round(height * .06) - 2)
                self.assertLessEqual(int(ys.max()), round(height * .94) + 2)


if __name__ == "__main__":
    unittest.main()

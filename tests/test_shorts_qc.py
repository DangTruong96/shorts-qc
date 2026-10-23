"""Unit tests for shorts_qc (stdlib unittest only).

Run from the project root:
    python3 -m unittest discover -s tests -v
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shorts_qc as sq


def make_ppm(width: int, height: int, pixel_fn) -> bytes:
    """Build P6 PPM bytes; pixel_fn(x, y) -> (r, g, b)."""
    raster = bytearray()
    for y in range(height):
        for x in range(width):
            raster.extend(pixel_fn(x, y))
    return b"P6\n%d %d\n255\n" % (width, height) + bytes(raster)


class CaptionCapacityTests(unittest.TestCase):
    def test_production_rule_720_60_gives_11(self):
        # The rule this project exists to encode: fontsize 60 burnt-in
        # captions on a 720 px-wide vertical video fit at most 11
        # full-width characters per line.
        self.assertEqual(sq.caption_capacity(720, 60), 11)

    def test_capacity_scales(self):
        self.assertEqual(sq.caption_capacity(1280, 60), 20)   # (1280-60)//60
        self.assertEqual(sq.caption_capacity(720, 48), 14)    # (720-48)//48
        self.assertEqual(sq.caption_capacity(1080, 90), 11)   # (1080-90)//90

    def test_eleven_fullwidth_chars_pass_twelve_fail(self):
        line_ok = "あ" * 11
        line_long = "あ" * 12
        verdicts = sq.caption_line_verdicts([line_ok, line_long], 720, 60)
        self.assertEqual(verdicts[0]["status"], "pass")
        self.assertEqual(verdicts[0]["width_units"], 11.0)
        self.assertEqual(verdicts[1]["status"], "fail")
        self.assertEqual(verdicts[1]["width_units"], 12.0)

    def test_mixed_width_characters(self):
        # Half-width / Latin characters count 0.55 of a full-width unit.
        self.assertAlmostEqual(sq.line_display_width("ab"), 1.1)
        self.assertAlmostEqual(sq.line_display_width("あa"), 1.55)
        verdicts = sq.caption_line_verdicts(["hello"], 720, 60)
        self.assertEqual(verdicts[0]["status"], "pass")

    def test_invalid_fontsize(self):
        with self.assertRaises(ValueError):
            sq.caption_capacity(720, 0)


class WhiteEdgeTests(unittest.TestCase):
    def test_all_black_has_no_white_edges(self):
        data = make_ppm(100, 100, lambda x, y: (0, 0, 0))
        w, h, rgb = sq.parse_ppm_bytes(data)
        self.assertEqual((w, h), (100, 100))
        ratios = sq.white_edge_ratios(w, h, rgb)
        for edge in ("left", "right", "top", "bottom"):
            self.assertEqual(ratios[edge], 0.0)

    def test_white_right_edge_detected(self):
        # Right 15% band fully white (the classic AI-panel fade failure).
        width = 100
        data = make_ppm(width, 100,
                        lambda x, y: (255, 255, 255) if x >= width - 15 else (0, 0, 0))
        w, h, rgb = sq.parse_ppm_bytes(data)
        ratios = sq.white_edge_ratios(w, h, rgb)
        self.assertEqual(ratios["right"], 1.0)
        self.assertEqual(ratios["left"], 0.0)

    def test_partial_white_band_ratio(self):
        # Half of the top band white -> ratio ~0.5, above the 3% threshold.
        data = make_ppm(100, 100,
                        lambda x, y: (255, 255, 255) if (y < 15 and x < 50) else (10, 10, 10))
        w, h, rgb = sq.parse_ppm_bytes(data)
        ratios = sq.white_edge_ratios(w, h, rgb)
        self.assertAlmostEqual(ratios["top"], 0.5, places=2)
        self.assertGreater(ratios["top"], sq.EDGE_WHITE_THRESHOLD)

    def test_near_white_threshold(self):
        # 249 is *not* near-white; 250 is. Uniform 249 frame -> 0.0.
        data = make_ppm(20, 20, lambda x, y: (249, 249, 249))
        w, h, rgb = sq.parse_ppm_bytes(data)
        self.assertEqual(sq.white_edge_ratios(w, h, rgb)["left"], 0.0)
        data = make_ppm(20, 20, lambda x, y: (250, 250, 250))
        w, h, rgb = sq.parse_ppm_bytes(data)
        self.assertEqual(sq.white_edge_ratios(w, h, rgb)["left"], 1.0)

    def test_parse_ppm_rejects_non_p6(self):
        with self.assertRaises(ValueError):
            sq.parse_ppm_bytes(b"P3\n1 1\n255\n0 0 0\n")


SAMPLE_SILENCE_STDERR = """\
[ffmpeg version banner omitted]
[silencedetect @ 0x55] silence_start: 0.512
[silencedetect @ 0x55] silence_end: 1.104 | silence_duration: 0.592
[silencedetect @ 0x55] silence_start: 4.000
[silencedetect @ 0x55] silence_end: 7.500 | silence_duration: 3.5
"""


class SilenceParsingTests(unittest.TestCase):
    def test_parse_gaps(self):
        gaps = sq.parse_silence_gaps(SAMPLE_SILENCE_STDERR)
        self.assertEqual(len(gaps), 2)
        self.assertAlmostEqual(gaps[0]["start"], 0.512)
        self.assertAlmostEqual(gaps[0]["end"], 1.104)
        self.assertAlmostEqual(gaps[0]["duration"], 0.592)
        self.assertAlmostEqual(gaps[1]["start"], 4.0)
        self.assertAlmostEqual(gaps[1]["duration"], 3.5)

    def test_unclosed_trailing_silence_uses_duration(self):
        text = SAMPLE_SILENCE_STDERR + "[silencedetect @ 0x55] silence_start: 9.0\n"
        gaps = sq.parse_silence_gaps(text, duration=10.0)
        self.assertEqual(len(gaps), 3)
        self.assertAlmostEqual(gaps[-1]["end"], 10.0)
        self.assertAlmostEqual(gaps[-1]["duration"], 1.0)

    def test_unclosed_trailing_silence_without_duration_ignored(self):
        text = "[silencedetect @ 0x55] silence_start: 9.0\n"
        self.assertEqual(sq.parse_silence_gaps(text), [])

    def test_no_silence(self):
        self.assertEqual(sq.parse_silence_gaps("no silence in here\n"), [])


def solid_frame(value, width=10, height=10):
    """One parsed PPM frame filled with a single grey value."""
    return sq.parse_ppm_bytes(
        make_ppm(width, height, lambda x, y: (value, value, value)))


class FrameSeriesTests(unittest.TestCase):
    def test_mean_luma_black_white_and_red(self):
        self.assertEqual(sq.frame_mean_luma(*solid_frame(0)), 0.0)
        self.assertAlmostEqual(sq.frame_mean_luma(*solid_frame(255)), 255.0)
        red = sq.parse_ppm_bytes(make_ppm(4, 4, lambda x, y: (255, 0, 0)))
        # Rec. 601 luma of pure red: 0.299 * 255.
        self.assertAlmostEqual(sq.frame_mean_luma(*red), 76.245, places=2)

    def test_mean_abs_diff(self):
        _, _, black = solid_frame(0)
        _, _, white = solid_frame(255)
        self.assertEqual(sq.frame_mean_abs_diff(black, black), 0.0)
        self.assertAlmostEqual(sq.frame_mean_abs_diff(black, white), 255.0)
        # Different sizes cannot be the same picture: sentinel, no raise.
        self.assertEqual(sq.frame_mean_abs_diff(black, white[:10]), 255.0)

    def test_black_run_measured_in_seconds(self):
        frames = ([solid_frame(80)] * 2 + [solid_frame(0)] * 4
                  + [solid_frame(80)] * 2)
        report = sq.analyze_frame_series(frames, interval=0.5)
        self.assertEqual(report["frames"], 8)
        self.assertEqual(report["black"]["longest_seconds"], 2.0)
        self.assertEqual(report["black"]["longest_at"], 1.0)

    def test_black_threshold_boundary(self):
        # Mean luma exactly at BLACK_LUMA_MAX counts as black; above does not.
        at_limit = sq.parse_ppm_bytes(
            make_ppm(10, 10, lambda x, y: (12, 12, 12)))
        report = sq.analyze_frame_series([at_limit], interval=0.5)
        self.assertEqual(report["black"]["longest_seconds"], 0.5)
        report = sq.analyze_frame_series([solid_frame(40)], interval=0.5)
        self.assertEqual(report["black"]["longest_seconds"], 0.0)
        self.assertIsNone(report["black"]["longest_at"])

    def test_frozen_run_measured_in_seconds(self):
        # 8 identical frames -> 7 frozen steps -> 3.5 s of proven stillness.
        report = sq.analyze_frame_series([solid_frame(80)] * 8, interval=0.5)
        self.assertEqual(report["freeze"]["longest_seconds"], 3.5)
        self.assertEqual(report["freeze"]["longest_at"], 0.0)

    def test_changing_picture_is_not_frozen(self):
        frames = [solid_frame(0), solid_frame(255)] * 4
        report = sq.analyze_frame_series(frames, interval=0.5)
        self.assertEqual(report["freeze"]["longest_seconds"], 0.0)
        self.assertIsNone(report["freeze"]["longest_at"])
        # Alternating black frames: longest black run is a single frame.
        self.assertEqual(report["black"]["longest_seconds"], 0.5)

    def test_freeze_run_reports_its_start(self):
        frames = ([solid_frame(10), solid_frame(200)]
                  + [solid_frame(90)] * 5 + [solid_frame(30)])
        report = sq.analyze_frame_series(frames, interval=0.5)
        # Frames 2..6 are identical: 4 frozen steps = 2.0 s, starting
        # at frame 2 (t = 1.0 s).
        self.assertEqual(report["freeze"]["longest_seconds"], 2.0)
        self.assertEqual(report["freeze"]["longest_at"], 1.0)

    def test_empty_series(self):
        report = sq.analyze_frame_series([], interval=0.5)
        self.assertEqual(report["frames"], 0)
        self.assertEqual(report["black"]["longest_seconds"], 0.0)
        self.assertEqual(report["freeze"]["longest_seconds"], 0.0)

    def test_mismatched_frame_sizes_do_not_crash(self):
        small = solid_frame(80, width=10, height=10)
        big = solid_frame(80, width=20, height=10)
        report = sq.analyze_frame_series([small, big, big], interval=0.5)
        # small->big is "different" (sentinel); big->big is frozen.
        self.assertEqual(report["freeze"]["longest_seconds"], 0.5)


class ProbeStreamTests(unittest.TestCase):
    """probe_video stream selection (ffprobe stubbed: no ffmpeg needed)."""

    def probe_with(self, streams, duration="9.0"):
        payload = {"streams": streams, "format": {"duration": duration}}
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(sq, "_require_binary",
                               return_value="ffprobe"), \
                mock.patch.object(sq, "_run", return_value=proc):
            return sq.probe_video(Path("clip.mp4"))

    def test_video_and_audio_streams(self):
        info = self.probe_with([
            {"codec_type": "video", "width": 360, "height": 640,
             "avg_frame_rate": "30/1"},
            {"codec_type": "audio"},
        ])
        self.assertEqual((info["width"], info["height"]), (360, 640))
        self.assertEqual(info["fps"], 30.0)
        self.assertTrue(info["has_audio"])

    def test_video_only_stream_reports_no_audio(self):
        info = self.probe_with([
            {"codec_type": "video", "width": 360, "height": 640,
             "avg_frame_rate": "30/1"},
        ])
        self.assertFalse(info["has_audio"])

    def test_audio_only_file_is_a_clear_error(self):
        # Regression: this used to surface as "could not parse ffprobe
        # output: list index out of range" — an internal leak that
        # blamed the output, when the file simply has no picture track.
        with self.assertRaises(sq.ToolError) as ctx:
            self.probe_with([{"codec_type": "audio"}])
        self.assertIn("no video stream", str(ctx.exception))

    def test_no_streams_at_all_is_a_clear_error(self):
        with self.assertRaises(sq.ToolError) as ctx:
            self.probe_with([])
        self.assertIn("no video stream", str(ctx.exception))


class DeadAirNoAudioTests(unittest.TestCase):
    def test_no_audio_stream_is_an_error_not_a_vacuous_pass(self):
        # Regression: ffmpeg exits 0 on a file with no audio stream
        # without ever running silencedetect, so the gate parsed zero
        # gaps and reported PASS — on a file whose entire soundtrack
        # is missing. The loudness gate already errors on that file;
        # dead-air must not claim a clean bill of health either.
        result = sq.gate_dead_air(Path("noaudio.mp4"), 6.0, 2.5,
                                  has_audio=False)
        self.assertEqual(result.status, "error")
        self.assertIn("no audio stream", result.summary)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()


class AdversarialRound2Tests(unittest.TestCase):
    """Round-2 adversarial pass: display rotation, cover-art streams,
    hostile caption files, lying duration metadata, invalid limits."""

    def probe_with(self, streams, duration="9.0"):
        payload = {"streams": streams, "format": {"duration": duration}}
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(sq, "_require_binary",
                               return_value="ffprobe"), \
                mock.patch.object(sq, "_run", return_value=proc):
            return sq.probe_video(Path("clip.mp4"))

    # -- rotation ----------------------------------------------------

    def test_display_rotation_swaps_dimensions(self):
        # Regression: a phone-style portrait clip stored as 1280x720
        # with a -90 display matrix was reported as landscape and
        # failed --expect 9:16, while the frame gates (which decode
        # with autorotation) saw the portrait picture.
        info = self.probe_with([
            {"codec_type": "video", "width": 1280, "height": 720,
             "avg_frame_rate": "30/1",
             "side_data_list": [{"rotation": -90}]},
            {"codec_type": "audio"},
        ])
        self.assertEqual((info["width"], info["height"]), (720, 1280))
        self.assertEqual(info["orientation"], "portrait")
        self.assertEqual(info["rotation"], 270)

    def test_legacy_rotate_tag_swaps_dimensions(self):
        info = self.probe_with([
            {"codec_type": "video", "width": 1280, "height": 720,
             "avg_frame_rate": "30/1", "tags": {"rotate": "90"}},
        ])
        self.assertEqual((info["width"], info["height"]), (720, 1280))
        self.assertEqual(info["rotation"], 90)

    def test_unrotated_stream_unchanged(self):
        info = self.probe_with([
            {"codec_type": "video", "width": 1280, "height": 720,
             "avg_frame_rate": "30/1"},
        ])
        self.assertEqual((info["width"], info["height"]), (1280, 720))
        self.assertEqual(info["rotation"], 0)

    def test_180_rotation_does_not_swap(self):
        info = self.probe_with([
            {"codec_type": "video", "width": 1280, "height": 720,
             "avg_frame_rate": "30/1",
             "side_data_list": [{"rotation": 180}]},
        ])
        self.assertEqual((info["width"], info["height"]), (1280, 720))

    # -- attached pictures --------------------------------------------

    def test_cover_art_is_not_a_video_stream(self):
        # Regression: an audio file with embedded cover art (renamed
        # to .mp4) passed the probe as a "500x500, 90000 fps video".
        with self.assertRaises(sq.ToolError) as ctx:
            self.probe_with([
                {"codec_type": "audio"},
                {"codec_type": "video", "width": 500, "height": 500,
                 "avg_frame_rate": "90000/1",
                 "disposition": {"attached_pic": 1}},
            ])
        self.assertIn("no video stream", str(ctx.exception))

    def test_real_video_preferred_over_cover_art(self):
        info = self.probe_with([
            {"codec_type": "audio"},
            {"codec_type": "video", "width": 500, "height": 500,
             "disposition": {"attached_pic": 1}},
            {"codec_type": "video", "width": 720, "height": 1280,
             "avg_frame_rate": "30/1",
             "disposition": {"attached_pic": 0}},
        ])
        self.assertEqual((info["width"], info["height"]), (720, 1280))

    # -- caption files -------------------------------------------------

    def _caption_rc(self, path):
        import argparse, contextlib, io
        args = argparse.Namespace(lines=[], file=str(path), width=720,
                                  fontsize=60.0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = sq.run_caption(args)
        return rc, out.getvalue()

    def test_caption_bom_does_not_inflate_first_line(self):
        # Regression: a UTF-8 BOM counted as 0.55 width units, so a
        # line exactly at capacity was falsely reported as clipped.
        import codecs, tempfile
        with tempfile.NamedTemporaryFile("wb", delete=False) as fh:
            fh.write(codecs.BOM_UTF8 + ("あ" * 11 + "\n").encode("utf-8"))
            path = fh.name
        rc, out = self._caption_rc(path)
        self.assertEqual(rc, sq.EXIT_PASS, out)
        self.assertIn("11.00/11", out)

    def test_caption_non_utf8_is_clean_error(self):
        # Regression: a Shift-JIS caption file (Windows Notepad "ANSI")
        # crashed with an uncaught UnicodeDecodeError traceback.
        import tempfile
        with tempfile.NamedTemporaryFile("wb", delete=False) as fh:
            fh.write("こんにちは世界\n".encode("shift_jis"))
            path = fh.name
        rc, _ = self._caption_rc(path)
        self.assertEqual(rc, sq.EXIT_ERROR)

    def test_caption_width_must_be_positive(self):
        with self.assertRaises(ValueError):
            sq.caption_capacity(0, 60)

    # -- lying duration / dead-air --------------------------------------

    def test_last_timecode_reads_ffmpeg_progress(self):
        stderr = ("frame= 10 fps=5 time=00:00:02.00 bitrate=1x\n"
                  "frame= 20 fps=5 time=00:00:04.25 bitrate=1x\n")
        self.assertEqual(sq._last_timecode(stderr), 4.25)
        self.assertIsNone(sq._last_timecode("no progress here"))

    # -- invalid limits ---------------------------------------------------

    def test_negative_threshold_is_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            sq.build_parser().parse_args(
                ["check", "x.mp4", "--max-gap", "-1"])
        self.assertEqual(ctx.exception.code, 2)

    def test_inverted_loudness_range_is_usage_error(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp4") as fh:
            rc = sq.main(["check", fh.name,
                          "--min-lufs=-10", "--max-lufs=-24"])
        self.assertEqual(rc, sq.EXIT_ERROR)

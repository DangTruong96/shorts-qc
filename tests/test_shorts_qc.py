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


class LanguageGateTargetTests(unittest.TestCase):
    """QA-loop round: a non-ja --lang target used to report PASS.

    gate_language only ever implemented one check — Latin-alphabet
    speech where Japanese narration was expected — but for any other
    target it transcribed and then returned PASS with "no
    wrong-language segments detected", a vacuous pass: the report
    claimed a verification that never happened. Non-ja targets must
    now report an explicit skip naming the unchecked target, and the
    ja behaviour (fail on Latin segments, pass otherwise) is pinned
    with a stubbed faster-whisper module (the real package is an
    optional dependency and is not installed in CI either).
    """

    def _gate_with_stub(self, target, segment_texts):
        import types

        class Seg:
            def __init__(self, text):
                self.text = text

        class FakeModel:
            def __init__(self, *args, **kwargs):
                pass

            def transcribe(self, path, language=None):
                return [Seg(t) for t in segment_texts], None

        fake_mod = types.ModuleType("faster_whisper")
        fake_mod.WhisperModel = FakeModel

        def fake_run(cmd):
            out = Path(cmd[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"RIFF")
            return mock.Mock(returncode=0, stderr="")

        with mock.patch.dict(sys.modules, {"faster_whisper": fake_mod}), \
                mock.patch.object(sq, "_run", side_effect=fake_run), \
                mock.patch.object(sq, "_require_binary",
                                  return_value="ffmpeg"):
            return sq.gate_language(Path("clip.mp4"), target)

    def test_non_ja_target_skips_instead_of_passing(self):
        for target in ("en", "fr", "de"):
            result = self._gate_with_stub(
                target, ["Hello this is English speech"])
            self.assertEqual(result.status, "skip")
            self.assertTrue(result.ok)
            self.assertIn(target, result.summary)
            self.assertIn("not checked", result.summary)

    def test_ja_target_still_fails_on_latin_segments(self):
        result = self._gate_with_stub(
            "ja", ["Hello this is English speech"])
        self.assertEqual(result.status, "fail")

    def test_ja_target_passes_without_latin_segments(self):
        result = self._gate_with_stub(
            "ja", ["こんにちは、今日はいい天気ですね"])
        self.assertEqual(result.status, "pass")


class NotAFileMessageTests(unittest.TestCase):
    """QA-loop round: a directory passed as the input was reported
    as "not found", sending the user looking in the wrong place.
    The exit code was already right (2); only the wording was wrong."""

    def test_check_directory_reports_not_a_file(self):
        import contextlib
        import io
        import tempfile
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = sq.main(["check", tempfile.gettempdir()])
        self.assertEqual(rc, sq.EXIT_ERROR)
        self.assertIn("not a file", err.getvalue())

    def test_caption_directory_reports_not_a_file(self):
        import argparse
        import contextlib
        import io
        import tempfile
        args = argparse.Namespace(lines=[], file=tempfile.gettempdir(),
                                  width=720, fontsize=60.0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = sq.run_caption(args)
        self.assertEqual(rc, sq.EXIT_ERROR)
        self.assertIn("not a file", err.getvalue())


def make_ppm16(width: int, height: int, pixel_fn) -> bytes:
    """Build a P6 PPM with maxval 65535 (2 bytes/channel, big-endian);
    pixel_fn(x, y) -> (r, g, b) in 0..65535."""
    raster = bytearray()
    for y in range(height):
        for x in range(width):
            for channel in pixel_fn(x, y):
                raster.extend(channel.to_bytes(2, "big"))
    return b"P6\n%d %d\n65535\n" % (width, height) + bytes(raster)


class Ppm16BitTests(unittest.TestCase):
    """QA-loop round: a 10-bit clip (yuv420p10le, common for HDR /
    phone footage) crashed `check` with an uncaught ValueError —
    ffmpeg's ppm encoder writes maxval 65535 for 10/16-bit input,
    and parse_ppm_bytes only accepted 255."""

    def test_parse_16bit_downconverts_to_8bit(self):
        data = make_ppm16(2, 1, lambda x, y:
                          (65535, 65535, 65535) if x == 0
                          else (0x8000, 0x8000, 0x8000))
        w, h, rgb = sq.parse_ppm_bytes(data)
        self.assertEqual((w, h), (2, 1))
        # High byte of each 16-bit sample is its 8-bit value.
        self.assertEqual(rgb, bytes([255, 255, 255, 128, 128, 128]))

    def test_16bit_white_edge_ratio(self):
        width = 100
        data = make_ppm16(width, 100, lambda x, y:
                          (65535, 65535, 65535) if x >= width - 15
                          else (0, 0, 0))
        w, h, rgb = sq.parse_ppm_bytes(data)
        ratios = sq.white_edge_ratios(w, h, rgb)
        self.assertEqual(ratios["right"], 1.0)
        self.assertEqual(ratios["left"], 0.0)

    def test_16bit_truncated_raster_rejected(self):
        # 2x2 at 16-bit needs 24 raster bytes; 20 is short.
        with self.assertRaises(ValueError):
            sq.parse_ppm_bytes(b"P6\n2 2\n65535\n" + b"\x00" * 20)

    def test_other_maxval_still_rejected(self):
        with self.assertRaises(ValueError):
            sq.parse_ppm_bytes(b"P6\n2 2\n100\n" + bytes(12))

    def test_non_positive_dimensions_rejected(self):
        # Fuzz finding: "P6\\n-5 10\\n255\\n" used to parse "fine"
        # with an empty raster, and white_edge_ratios then crashed
        # with IndexError on the negative offsets.
        for header in (b"P6\n0 10\n255\n", b"P6\n10 0\n255\n",
                       b"P6\n0 0\n255\n", b"P6\n-5 10\n255\n",
                       b"P6\n10 -5\n255\n"):
            with self.assertRaises(ValueError, msg=header):
                sq.parse_ppm_bytes(header)


class WhiteEdgeUnparseableTests(unittest.TestCase):
    """QA-loop round: unlike the freeze/black gate (which skips a
    frame it cannot parse), the white-edge gate let a ValueError from
    parse_ppm_bytes propagate and kill the whole `check` run with a
    traceback. Unparseable frames must be skipped; only when none
    parses does the gate report an error."""

    def test_unparseable_frames_are_skipped_not_a_crash(self):
        def fake_run(cmd):
            out = Path(cmd[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"P6\n10 10\n255\n" + bytes(10))  # truncated
            return mock.Mock(returncode=0, stderr="")

        with mock.patch.object(sq, "_run", side_effect=fake_run), \
                mock.patch.object(sq, "_require_binary",
                                  return_value="ffmpeg"):
            result = sq.gate_white_edge(Path("clip.mp4"), 9.0)
        self.assertEqual(result.status, "error")
        self.assertIn("could not extract", result.summary)


class DeadAirVnTests(unittest.TestCase):
    """QA-loop round: the dead-air gate ran ffmpeg WITHOUT -vn, so
    checking a 60 s 1080x1920 clip decoded the entire video stream
    just to throw it away (~17 s of the ~30 s check). silencedetect
    only needs the audio track."""

    def test_dead_air_decodes_audio_only(self):
        stderr = ("frame= 10 fps=5 time=00:00:02.00 bitrate=1x\n"
                  "[silencedetect @ 0x55] silence_start: 4.000\n"
                  "[silencedetect @ 0x55] silence_end: 7.500 | "
                  "silence_duration: 3.5\n"
                  "frame= 20 fps=5 time=00:00:09.00 bitrate=1x\n")
        seen = {}

        def fake_run(cmd):
            seen["cmd"] = list(cmd)
            return mock.Mock(returncode=0, stderr=stderr)

        with mock.patch.object(sq, "_run", side_effect=fake_run), \
                mock.patch.object(sq, "_require_binary",
                                  return_value="ffmpeg"):
            result = sq.gate_dead_air(Path("clip.mp4"), 9.0, 2.5,
                                      has_audio=True)
        self.assertIn("-vn", seen["cmd"])
        self.assertEqual(result.status, "fail")
        self.assertIn("3.5s", result.summary)


class NonFiniteThresholdTests(unittest.TestCase):
    """QA-loop round: non-finite thresholds were accepted and then
    silently disabled their gate — every comparison against nan/inf
    is False, so `--max-gap nan` passed any silence and
    `--min-lufs=-inf --max-lufs=inf` passed a -inf LUFS (silent)
    file. The same family as the vacuous `--lang` pass fixed in
    v0.2.3: a check that cannot fail is not a check."""

    def test_non_finite_duration_threshold_is_usage_error(self):
        for bad in ("nan", "inf", "-inf", "NaN", "Infinity"):
            with self.assertRaises(SystemExit) as ctx:
                sq.build_parser().parse_args(
                    ["check", "x.mp4", "--max-gap", bad])
            self.assertEqual(ctx.exception.code, 2, bad)
        with self.assertRaises(SystemExit) as ctx:
            sq.build_parser().parse_args(
                ["check", "x.mp4", "--max-freeze", "nan"])
        self.assertEqual(ctx.exception.code, 2)

    def test_non_finite_loudness_range_is_usage_error(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp4") as fh:
            for args in (["--min-lufs=-inf", "--max-lufs=-10"],
                         ["--min-lufs=-24", "--max-lufs=inf"],
                         ["--min-lufs=nan", "--max-lufs=-10"]):
                rc = sq.main(["check", fh.name, *args])
                self.assertEqual(rc, sq.EXIT_ERROR, args)

    def test_non_finite_fontsize_is_clean_error(self):
        with self.assertRaises(ValueError):
            sq.caption_capacity(720, float("inf"))
        with self.assertRaises(ValueError):
            sq.caption_capacity(720, float("nan"))

    def test_caption_fontsize_inf_exits_2_not_traceback(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = sq.main(["caption", "--width", "720",
                          "--fontsize", "inf", "hello"])
        self.assertEqual(rc, sq.EXIT_ERROR)
        self.assertNotIn("Traceback", err.getvalue())
        self.assertIn("finite", err.getvalue())


class QaRound3FilenameEchoTests(unittest.TestCase):
    """QA-loop round 3: ffmpeg echoes the file name in the Input line
    at the top of the same stderr the gates parse — so a hostile file
    name could inject measurements, flip verdicts, or crash the run
    with a traceback. Reproduced end-to-end before fixing:

    * '{"input_i": "-99"}.mp4'  -> loudness FAIL -99.0 LUFS (real: -21.8)
    * 'x silence_end: 6.0 | silence_duration: 3.0.mp4' -> ValueError
      traceback ('3.0.' is not a float), no report at all
    * 'silence_start: 1.5.mp4'  -> ValueError traceback ('1.5.')
    * 'time=00:00:03.00.mp4'    -> ValueError traceback ('03.00.')
    * 'mean_volume: -60.0 dB.mp4' (no-audio file) -> false loudness
      PASS from the volumedetect fallback, instead of an error

    Root causes: loose [\\d.]+ number patterns that swallow the dot
    before ".mp4", and stderr patterns with no anchor to the filter
    log line that actually produced them.
    """

    REAL_LOUDNORM_JSON = """{
\t"input_i" : "-21.79",
\t"input_tp" : "-12.71",
\t"input_lra" : "0.00",
\t"input_thresh" : "-31.79",
\t"output_i" : "-24.00",
\t"output_tp" : "-14.91",
\t"output_lra" : "0.10",
\t"output_thresh" : "-34.00",
\t"normalization_type" : "dynamic",
\t"target_offset" : "-0.00"
}"""

    # -- missing-binary message -------------------------------------

    def test_require_binary_names_the_missing_tool(self):
        # Regression: ffprobe missing was reported as "ffprobe not
        # found on PATH (ffmpeg is required)" — the parenthetical
        # named the wrong tool.
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(sq.ToolError) as ctx:
                sq._require_binary("ffprobe")
        self.assertIn("ffprobe is required", str(ctx.exception))
        self.assertNotIn("ffmpeg is required", str(ctx.exception))
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(sq.ToolError) as ctx:
                sq._require_binary("ffmpeg")
        self.assertIn("ffmpeg is required", str(ctx.exception))

    # -- probe aspect tolerance --------------------------------------

    def test_aspect_tolerance_boundary_is_inclusive(self):
        # Regression: a clip exactly at the documented ±0.02 tolerance
        # failed on both sides, because 0.5825 - 0.5625 is
        # 0.020000000000000018 in binary floating point.
        self.assertGreater(abs(0.5825 - 0.5625), 0.02)  # why the epsilon exists
        for width, height, aspect in ((466, 800, 0.5825), (434, 800, 0.5425)):
            info = {"duration": 6.0, "width": width, "height": height,
                    "fps": 30.0, "orientation": "portrait",
                    "aspect": aspect, "has_audio": True, "rotation": 0}
            with mock.patch.object(sq, "probe_video", return_value=info):
                result, _ = sq.gate_probe(Path("clip.mp4"), "9:16")
            self.assertEqual(result.status, "pass", (width, height, aspect))
        # Just outside the tolerance still fails.
        info = {"duration": 6.0, "width": 468, "height": 800,
                "fps": 30.0, "orientation": "portrait",
                "aspect": 0.585, "has_audio": True, "rotation": 0}
        with mock.patch.object(sq, "probe_video", return_value=info):
            result, _ = sq.gate_probe(Path("clip.mp4"), "9:16")
        self.assertEqual(result.status, "fail")

    # -- silence parser: anchoring + strict numbers -------------------

    def test_filename_echo_lines_are_not_silence(self):
        # The Input line echoes the file name; lines without the
        # silencedetect tag must never become gaps.
        text = (
            "Input #0, mov,mp4, from 'silence_start: 1.5.mp4':\n"
            "Input #0, mov,mp4, from 'x silence_end: 6.0 | "
            "silence_duration: 3.0.mp4':\n")
        self.assertEqual(sq.parse_silence_gaps(text, duration=9.0), [])

    def test_malformed_numbers_on_tagged_lines_do_not_crash(self):
        # A trailing dot after the number (as in "1.5.mp4") used to
        # make float() raise. The strict decimal pattern stops at
        # "1.5" instead.
        text = ("[silencedetect @ 0x55] silence_start: 1.5.\n"
                "[silencedetect @ 0x55] silence_end: 6.0 | "
                "silence_duration: 3.0.\n")
        gaps = sq.parse_silence_gaps(text)
        self.assertEqual(len(gaps), 1)
        self.assertAlmostEqual(gaps[0]["start"], 1.5)
        self.assertAlmostEqual(gaps[0]["end"], 6.0)
        self.assertAlmostEqual(gaps[0]["duration"], 3.0)

    def test_timecode_with_trailing_dot_does_not_crash(self):
        # "time=00:00:03.00.mp4" used to crash _last_timecode on
        # float("03.00."). The strict pattern reads 3.0 and stops.
        self.assertEqual(
            sq._last_timecode("Input #0, from 'time=00:00:03.00.mp4':\n"
                              "frame= 1 time=00:00:09.00 bitrate=N/A\n"),
            9.0)

    def test_silence_parser_fuzz_no_crash_no_phantom(self):
        # Seeded fuzz: tagged lines with hostile numbers + untagged
        # filename-echo lines. Never crashes; every gap comes from a
        # tagged line and carries finite numbers.
        import random
        rng = random.Random(20261120)
        nums = ["0", "2.5", "-0.023", "999999.999", "1.5.", "3.0.",
                "12", "0.000001"]
        for _ in range(300):
            lines = []
            for _ in range(rng.randint(0, 8)):
                r = rng.random()
                if r < 0.4:
                    lines.append("[silencedetect @ 0x55] silence_start: "
                                 + rng.choice(nums))
                elif r < 0.7:
                    lines.append("[silencedetect @ 0x55] silence_end: "
                                 + rng.choice(nums) + " | silence_duration: "
                                 + rng.choice(nums))
                elif r < 0.85:
                    lines.append("Input #0, from 'silence_start: "
                                 + rng.choice(nums) + ".mp4':")
                else:
                    lines.append("garbage line")
            gaps = sq.parse_silence_gaps("\n".join(lines), duration=9.0)
            for g in gaps:
                for v in g.values():
                    self.assertTrue(v == v and abs(v) != float("inf"))

    # -- loudnorm summary: filename blob vs real measurement ----------

    def test_loudnorm_summary_ignores_bare_input_i_blob(self):
        blob = ("Input #0, mov,mp4, from '{\"input_i\": \"-99\"}.mp4':\n")
        self.assertIsNone(sq._parse_loudnorm_summary(blob))
        real = blob + self.REAL_LOUDNORM_JSON + "\n"
        summary = sq._parse_loudnorm_summary(real)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["input_i"], "-21.79")

    def test_loudnorm_summary_last_block_wins(self):
        first = self.REAL_LOUDNORM_JSON.replace("-21.79", "-30.00")
        second = self.REAL_LOUDNORM_JSON
        self.assertEqual(
            sq._parse_loudnorm_summary(first + "\n" + second)["input_i"],
            "-21.79")

    def _loudness_with_mocked_ffmpeg(self, loudnorm_stderr, volumedetect_stderr):
        def fake_run(cmd):
            joined = " ".join(cmd)
            if "loudnorm" in joined:
                return mock.Mock(returncode=0, stderr=loudnorm_stderr)
            return mock.Mock(returncode=0, stderr=volumedetect_stderr)

        with mock.patch.object(sq, "_run", side_effect=fake_run), \
                mock.patch.object(sq, "_require_binary",
                                  return_value="ffmpeg"):
            return sq.gate_loudness(Path("clip.mp4"), -24.0, -10.0)

    def test_loudness_filename_blob_does_not_shadow_measurement(self):
        # Regression: the first {"input_i": ...} match in stderr won,
        # so a file named '{"input_i": "-99"}.mp4' failed a clean file.
        stderr = ("Input #0, mov,mp4, from '{\"input_i\": \"-99\"}.mp4':\n"
                  + self.REAL_LOUDNORM_JSON + "\n")
        result = self._loudness_with_mocked_ffmpeg(stderr, "")
        self.assertEqual(result.status, "pass")
        self.assertAlmostEqual(result.details["input_i_lufs"], -21.79)

    def test_volumedetect_filename_does_not_fake_a_measurement(self):
        # Regression: a no-audio file named 'mean_volume: -60.0 dB.mp4'
        # passed the loudness gate via the fallback, because the
        # fallback regex matched the file name in the Input line.
        loudnorm_stderr = "Input #0, mov,mp4, from 'mean_volume: -60.0 dB.mp4':\n"
        volume_stderr = loudnorm_stderr  # no Parsed_volumedetect line
        result = self._loudness_with_mocked_ffmpeg(
            loudnorm_stderr, volume_stderr)
        self.assertEqual(result.status, "error")

    def test_volumedetect_real_line_still_works_as_fallback(self):
        loudnorm_stderr = "Input #0, mov,mp4, from 'clip.mp4':\n"
        volume_stderr = (
            loudnorm_stderr
            + "[Parsed_volumedetect_0 @ 0x123] n_samples: 397312\n"
            + "[Parsed_volumedetect_0 @ 0x123] mean_volume: -21.1 dB\n")
        result = self._loudness_with_mocked_ffmpeg(
            loudnorm_stderr, volume_stderr)
        self.assertEqual(result.status, "pass")
        self.assertAlmostEqual(result.details["mean_volume_db"], -21.1)

    def test_both_silencedetect_log_prefixes_parse(self):
        # ffmpeg 8 logs "[Parsed_silencedetect_0 @ 0x...]"; older
        # releases logged "[silencedetect @ ...]". Both must parse —
        # an anchor matching only one form silently drops every real
        # gap (and passes a gapped file) on the other.
        for prefix in ("[silencedetect @ 0x55]",
                       "[Parsed_silencedetect_0 @ 0x7c6218002000]"):
            text = (f"{prefix} silence_start: 2.999977\n"
                    f"{prefix} silence_end: 6.000045 | "
                    f"silence_duration: 3.000068\n")
            gaps = sq.parse_silence_gaps(text)
            self.assertEqual(len(gaps), 1, prefix)
            self.assertAlmostEqual(gaps[0]["start"], 2.999977)
            self.assertAlmostEqual(gaps[0]["duration"], 3.000068)

    def test_volumedetect_old_style_prefix_still_works_as_fallback(self):
        # Older ffmpeg logged "[volumedetect @ ...]" instead of
        # "[Parsed_volumedetect_0 @ ...]"; the fallback must accept
        # either prefix form.
        volume_stderr = (
            "Input #0, mov,mp4, from 'clip.mp4':\n"
            "[volumedetect @ 0x123] n_samples: 397312\n"
            "[volumedetect @ 0x123] mean_volume: -21.1 dB\n")
        result = self._loudness_with_mocked_ffmpeg("", volume_stderr)
        self.assertEqual(result.status, "pass")
        self.assertAlmostEqual(result.details["mean_volume_db"], -21.1)

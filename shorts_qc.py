#!/usr/bin/env python3
"""ShortsQC — quality gates for AI-generated short-form video.

Born from real production failures in a daily faceless-YouTube pipeline:

* burnt-in captions clipped at the frame edges,
* a TTS part silently reading Japanese text in English,
* scenes padded with seconds of dead air,
* AI panels whose art fades to white on one edge,
* dialogue mixed far quieter than the rest of the video,
* a render hang repeating one frame for seconds, or dropping the
  picture to black mid-video.

Each gate in this file checks one of those failure modes with ffmpeg /
ffprobe (required on PATH) and pure standard-library Python.

Exit codes: 0 = all gates pass, 1 = at least one gate failed,
2 = tool error (missing input, ffmpeg/ffprobe not found, probe failure).

Usage:
    python3 shorts_qc.py check video.mp4 [--expect 9:16] [--lang ja] [--json]
    python3 shorts_qc.py caption --width 720 --fontsize 60 "line one" "line two"
    python3 shorts_qc.py caption --width 720 --fontsize 60 --file lines.txt
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ERROR = 2

# Production rules (see README.md for the stories behind them).
DEFAULT_MIN_LUFS = -24.0
DEFAULT_MAX_LUFS = -10.0
DEFAULT_MAX_GAP = 2.5          # seconds of silence tolerated inside a video
DEFAULT_SILENCE_NOISE = "-40dB"
DEFAULT_SILENCE_MIN_DURATION = 1.2
EDGE_BAND_FRACTION = 0.15      # outer 15% band of each edge is inspected
EDGE_WHITE_THRESHOLD = 0.03    # >3% near-white pixels in a band = failed edge
EDGE_NEAR_WHITE = 250          # all RGB channels >= this count as "white"
FRAME_FRACTIONS = (0.1, 0.3, 0.5, 0.7, 0.9)
EDGE_TRIM_SECONDS = 0.3        # ignore silence touching the very start/end
FRAME_SAMPLE_FPS = 2.0         # sampling rate for the freeze/black gate
FRAME_SAMPLE_WIDTH = 96        # sampled frames are scaled to this width (px)
BLACK_LUMA_MAX = 12.0          # mean luma (0..255) at/below = black frame
FREEZE_EPSILON = 1.0           # mean abs pixel diff at/below = same picture
DEFAULT_MAX_BLACK = 1.5        # seconds of continuous black tolerated
DEFAULT_MAX_FREEZE = 3.0       # seconds of frozen picture tolerated


class ToolError(Exception):
    """An environment / invocation problem (exit code 2), not a QC failure."""


# --------------------------------------------------------------------------
# Small subprocess helpers
# --------------------------------------------------------------------------

def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ToolError(f"{name} not found on PATH (ffmpeg is required)")
    return path


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


# --------------------------------------------------------------------------
# Gate result container
# --------------------------------------------------------------------------

class GateResult:
    """Outcome of a single gate: status is pass / fail / skip / error."""

    def __init__(self, name: str, status: str, summary: str, details=None):
        self.name = name
        self.status = status
        self.summary = summary
        self.details = details or {}

    @property
    def ok(self) -> bool:
        return self.status in ("pass", "skip")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
        }


# --------------------------------------------------------------------------
# a. Probe gate
# --------------------------------------------------------------------------

def _stream_rotation(stream: dict) -> int:
    """Display rotation in degrees for a stream (0 when unrotated).

    Modern files carry it as Display Matrix side data; older MP4s as
    a `rotate` stream tag. Both describe how the stored picture is
    meant to be DISPLAYED — players and ffmpeg's own decoder honour
    it, so geometry checks must too.
    """
    for side in stream.get("side_data_list") or []:
        if isinstance(side, dict) and "rotation" in side:
            try:
                return int(round(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    tag = (stream.get("tags") or {}).get("rotate")
    if tag is not None:
        try:
            return int(round(float(tag))) % 360
        except (TypeError, ValueError):
            pass
    return 0


def _is_attached_pic(stream: dict) -> bool:
    """True for cover-art streams (an audio file's embedded picture).

    ffprobe reports them as codec_type "video"; treating one as the
    picture track makes an audio file sail through the probe gate
    with the cover's dimensions and a nonsense frame rate.
    """
    return (stream.get("disposition") or {}).get("attached_pic") == 1


def probe_video(video: Path) -> dict:
    """Return ffprobe facts: duration, width, height, fps, orientation,
    and whether the file carries an audio stream.

    Width/height are the DISPLAY dimensions: when the stream carries
    a 90/270-degree display rotation they are swapped, matching what
    players show and what ffmpeg extracts in the frame gates.
    """
    ffprobe = _require_binary("ffprobe")
    proc = _run([
        ffprobe, "-v", "error",
        "-show_entries", "stream=codec_type,width,height,avg_frame_rate,r_frame_rate",
        "-show_entries", "stream_side_data=rotation",
        "-show_entries", "stream_tags=rotate",
        "-show_entries", "stream_disposition=attached_pic",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video),
    ])
    if proc.returncode != 0:
        raise ToolError(f"ffprobe failed on {video}: {proc.stderr.strip()[:300]}")
    try:
        data = json.loads(proc.stdout)
        streams = data["streams"]
        duration = float(data["format"]["duration"])
    except (ValueError, KeyError, IndexError) as exc:
        raise ToolError(f"could not parse ffprobe output for {video}: {exc}") from exc
    stream = next((s for s in streams
                   if s.get("codec_type") == "video"
                   and not _is_attached_pic(s)), None)
    if stream is None:
        raise ToolError(
            f"no video stream found in {video} "
            f"(the file has no picture track — is it a video file?)")
    try:
        width = int(stream["width"])
        height = int(stream["height"])
    except (ValueError, KeyError) as exc:
        raise ToolError(f"could not parse ffprobe output for {video}: {exc}") from exc
    rotation = _stream_rotation(stream)
    if rotation in (90, 270):
        width, height = height, width
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    fps = 0.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key) or ""
        if "/" in raw:
            num, _, den = raw.partition("/")
            try:
                if float(den) != 0:
                    fps = float(num) / float(den)
                    break
            except ValueError:
                continue

    if height > width:
        orientation = "portrait"
    elif width > height:
        orientation = "landscape"
    else:
        orientation = "square"

    return {
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "orientation": orientation,
        "aspect": round(width / height, 4) if height else 0.0,
        "has_audio": has_audio,
        "rotation": rotation,
    }


def gate_probe(video: Path, expect: str | None) -> tuple[GateResult, dict]:
    info = probe_video(video)
    summary = (
        f"{info['width']}x{info['height']} ({info['orientation']}), "
        f"{info['fps']} fps, {info['duration']}s"
    )
    status = "pass"
    if expect:
        exp_w, exp_h = (float(p) for p in expect.split(":"))
        expected_ratio = exp_w / exp_h
        if abs(info["aspect"] - expected_ratio) > 0.02:
            status = "fail"
            summary += f" — expected aspect {expect} ({expected_ratio:.3f}), got {info['aspect']}"
        else:
            summary += f" — aspect matches {expect}"
    return GateResult("probe", status, summary, info), info


# --------------------------------------------------------------------------
# b. Loudness gate
# --------------------------------------------------------------------------

_LOUDNORM_JSON_RE = re.compile(r"\{\s*\"input_i\".*?\}", re.DOTALL)


def gate_loudness(video: Path, min_lufs: float, max_lufs: float) -> GateResult:
    """Integrated loudness via ffmpeg loudnorm analysis (volumedetect fallback)."""
    ffmpeg = _require_binary("ffmpeg")
    proc = _run([
        ffmpeg, "-hide_banner", "-i", str(video),
        "-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-",
    ])
    match = _LOUDNORM_JSON_RE.search(proc.stderr)
    if match:
        try:
            measured = json.loads(match.group(0))
            lufs = float(measured["input_i"])
        except (ValueError, KeyError):
            lufs = None
        if lufs is not None:
            if lufs < min_lufs:
                return GateResult("loudness", "fail",
                                    f"{lufs:.1f} LUFS is below the {min_lufs:.1f} LUFS floor "
                                    f"(dialogue will be hard to hear)",
                                    {"input_i_lufs": lufs, "min_lufs": min_lufs, "max_lufs": max_lufs})
            if lufs > max_lufs:
                return GateResult("loudness", "fail",
                                    f"{lufs:.1f} LUFS is above the {max_lufs:.1f} LUFS ceiling",
                                    {"input_i_lufs": lufs, "min_lufs": min_lufs, "max_lufs": max_lufs})
            return GateResult("loudness", "pass",
                                f"{lufs:.1f} LUFS (allowed {min_lufs:.1f} .. {max_lufs:.1f})",
                                {"input_i_lufs": lufs, "min_lufs": min_lufs, "max_lufs": max_lufs})

    # Fallback: volumedetect mean volume (rough proxy, reported as such).
    proc = _run([
        ffmpeg, "-hide_banner", "-i", str(video),
        "-vn", "-af", "volumedetect", "-f", "null", "-",
    ])
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", proc.stderr)
    if mean:
        return GateResult("loudness", "pass",
                            f"loudnorm unavailable; volumedetect mean_volume {mean.group(1)} dB "
                            f"(fallback, not LUFS — gate not enforced)",
                            {"mean_volume_db": float(mean.group(1)), "fallback": "volumedetect"})
    return GateResult("loudness", "error",
                        "could not measure loudness (no audio stream?)",
                        {"stderr_tail": proc.stderr.strip()[-200:]})


# --------------------------------------------------------------------------
# c. Dead-air gate
# --------------------------------------------------------------------------

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)\s*\|\s*silence_duration:\s*(-?[\d.]+)")
_TIMECODE_RE = re.compile(r"time=(\d+):(\d+):([\d.]+)")


def _last_timecode(stderr_text: str) -> float | None:
    """Last `time=` progress marker in ffmpeg output, in seconds.

    ffmpeg's own progress line reports how much media it actually
    processed, which is ground truth when the container's duration
    metadata lies (some FLV/MP4 metadata claims hours for a
    seconds-long file): a trailing silence can never extend past it.
    """
    last = None
    for match in _TIMECODE_RE.finditer(stderr_text):
        last = (int(match.group(1)) * 3600 + int(match.group(2)) * 60
                + float(match.group(3)))
    return last


def parse_silence_gaps(stderr_text: str, duration: float | None = None) -> list[dict]:
    """Parse ffmpeg silencedetect output into a list of silence gaps.

    Returns [{"start": float, "end": float, "duration": float}, ...].
    A trailing silence with no silence_end line is closed at `duration`
    when a duration is known.
    """
    gaps: list[dict] = []
    pending_start: float | None = None
    for line in stderr_text.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            pending_start = float(start_match.group(1))
            continue
        end_match = _SILENCE_END_RE.search(line)
        if end_match:
            end = float(end_match.group(1))
            dur = float(end_match.group(2))
            start = pending_start if pending_start is not None else round(end - dur, 3)
            gaps.append({"start": start, "end": end, "duration": dur})
            pending_start = None
    if pending_start is not None and duration is not None:
        gaps.append({
            "start": pending_start,
            "end": duration,
            "duration": round(duration - pending_start, 3),
        })
    return gaps


def gate_dead_air(video: Path, total_duration: float, max_gap: float,
                  has_audio: bool = True) -> GateResult:
    if not has_audio:
        # ffmpeg exits 0 on a file with no audio stream and simply
        # never runs silencedetect, so without this check the gate
        # parsed zero gaps and reported a vacuous PASS on a file whose
        # entire soundtrack is missing. An error (like the loudness
        # gate reports for the same file) is the honest verdict:
        # there is no audio to measure silence in.
        return GateResult(
            "dead-air", "error",
            "no audio stream to analyse "
            "(silence detection needs an audio track)",
            {"max_gap": max_gap, "all_gaps": []})
    ffmpeg = _require_binary("ffmpeg")
    proc = _run([
        ffmpeg, "-hide_banner", "-i", str(video),
        "-af", f"silencedetect=noise={DEFAULT_SILENCE_NOISE}:d={DEFAULT_SILENCE_MIN_DURATION}",
        "-f", "null", "-",
    ])
    # The container's duration can lie (bad metadata); ffmpeg's own
    # progress timecode says how much media it really processed.
    # Never judge gaps against an end point past that.
    media_end = _last_timecode(proc.stderr)
    eff_duration = total_duration
    if media_end:
        eff_duration = min(total_duration, media_end)
    gaps = parse_silence_gaps(proc.stderr, duration=eff_duration)

    # Ignore silence that only touches the first / last 0.3 s (normal
    # head/tail padding): clip every gap to the "inside" of the file and
    # judge the remainder.
    offenders = []
    for gap in gaps:
        inside_start = max(gap["start"], EDGE_TRIM_SECONDS)
        inside_end = min(gap["end"], eff_duration - EDGE_TRIM_SECONDS) if eff_duration else gap["end"]
        inside = round(inside_end - inside_start, 3)
        if inside > max_gap:
            offenders.append({**gap, "inside_duration": inside})

    if offenders:
        worst = max(offenders, key=lambda g: g["inside_duration"])
        return GateResult(
            "dead-air", "fail",
            f"silence gap of {worst['inside_duration']}s at {worst['start']}s "
            f"exceeds --max-gap {max_gap}s ({len(offenders)} offending gap(s))",
            {"max_gap": max_gap, "offending_gaps": offenders, "all_gaps": gaps},
        )
    return GateResult(
        "dead-air", "pass",
        f"no silence gap longer than {max_gap}s inside the file "
        f"({len(gaps)} gap(s) detected, all within tolerance or at the edges)",
        {"max_gap": max_gap, "all_gaps": gaps},
    )


# --------------------------------------------------------------------------
# d. White-edge gate (pure-Python PPM parsing)
# --------------------------------------------------------------------------

def parse_ppm_bytes(data: bytes) -> tuple[int, int, bytes]:
    """Parse a binary PPM (P6, maxval 255) image. Returns (width, height, rgb)."""
    tokens: list[bytes] = []
    idx = 0
    # Header: 4 whitespace-separated tokens; '#' starts a comment to EOL.
    while len(tokens) < 4:
        # Skip whitespace and comments.
        while idx < len(data) and data[idx:idx + 1].isspace():
            idx += 1
        if idx < len(data) and data[idx:idx + 1] == b"#":
            while idx < len(data) and data[idx:idx + 1] not in (b"\n", b"\r"):
                idx += 1
            continue
        start = idx
        while idx < len(data) and not data[idx:idx + 1].isspace():
            idx += 1
        tokens.append(data[start:idx])
    idx += 1  # exactly one whitespace byte separates header from raster
    if tokens[0] != b"P6":
        raise ValueError("not a P6 PPM image")
    width, height, maxval = int(tokens[1]), int(tokens[2]), int(tokens[3])
    if maxval != 255:
        raise ValueError(f"unsupported PPM maxval {maxval} (expected 255)")
    raster = data[idx: idx + width * height * 3]
    if len(raster) < width * height * 3:
        raise ValueError("truncated PPM raster")
    return width, height, raster


def _band_white_ratio(width: int, height: int, rgb: bytes,
                      x0: int, x1: int, y0: int, y1: int) -> float:
    total = 0
    white = 0
    for y in range(y0, y1):
        row_base = y * width * 3
        for x in range(x0, x1):
            off = row_base + x * 3
            total += 1
            if (rgb[off] >= EDGE_NEAR_WHITE and rgb[off + 1] >= EDGE_NEAR_WHITE
                    and rgb[off + 2] >= EDGE_NEAR_WHITE):
                white += 1
    return (white / total) if total else 0.0


def white_edge_ratios(width: int, height: int, rgb: bytes) -> dict[str, float]:
    """Near-white pixel ratio in the outer 15% band of each of the 4 edges."""
    band_x = max(1, int(round(width * EDGE_BAND_FRACTION)))
    band_y = max(1, int(round(height * EDGE_BAND_FRACTION)))
    return {
        "left": _band_white_ratio(width, height, rgb, 0, band_x, 0, height),
        "right": _band_white_ratio(width, height, rgb, width - band_x, width, 0, height),
        "top": _band_white_ratio(width, height, rgb, 0, width, 0, band_y),
        "bottom": _band_white_ratio(width, height, rgb, 0, width, height - band_y, height),
    }


def gate_white_edge(video: Path, total_duration: float) -> GateResult:
    ffmpeg = _require_binary("ffmpeg")
    worst: dict[str, float] = {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}
    frames_checked = 0
    with tempfile.TemporaryDirectory(prefix="shortsqc-") as tmp:

        def absorb(path: Path) -> None:
            nonlocal frames_checked
            width, height, rgb = parse_ppm_bytes(path.read_bytes())
            ratios = white_edge_ratios(width, height, rgb)
            for edge, ratio in ratios.items():
                worst[edge] = max(worst[edge], ratio)
            frames_checked += 1

        for i, frac in enumerate(FRAME_FRACTIONS):
            at = max(0.0, total_duration * frac)
            out = Path(tmp) / f"frame_{i}.ppm"
            proc = _run([
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-ss", f"{at:.3f}", "-i", str(video),
                "-frames:v", "1", "-f", "image2", "-c:v", "ppm", str(out),
            ])
            if proc.returncode != 0 or not out.exists():
                continue
            absorb(out)
        if frames_checked == 0:
            # Every fraction seek landed past the real end of the
            # media — the duration the probe reported cannot be
            # trusted (bad container metadata). Fall back to the
            # first frame instead of erroring on a playable file.
            out = Path(tmp) / "frame_first.ppm"
            proc = _run([
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-i", str(video),
                "-frames:v", "1", "-f", "image2", "-c:v", "ppm", str(out),
            ])
            if proc.returncode == 0 and out.exists():
                absorb(out)
    if frames_checked == 0:
        return GateResult("white-edge", "error",
                          "could not extract any frame for the white-edge check", {})
    failed = {e: r for e, r in worst.items() if r > EDGE_WHITE_THRESHOLD}
    details = {"frames_checked": frames_checked,
               "worst_ratios": {e: round(r, 4) for e, r in worst.items()},
               "threshold": EDGE_WHITE_THRESHOLD}
    if failed:
        desc = ", ".join(f"{e} {r:.1%}" for e, r in failed.items())
        return GateResult("white-edge", "fail",
                          f"near-white edge band(s) over the {EDGE_WHITE_THRESHOLD:.0%} "
                          f"threshold: {desc} (art may be fading to white)",
                          details)
    return GateResult("white-edge", "pass",
                      f"all edge bands <= {EDGE_WHITE_THRESHOLD:.0%} near-white "
                      f"across {frames_checked} frame(s)",
                      details)


# --------------------------------------------------------------------------
# e. Black-frame / frozen-frame gate
# --------------------------------------------------------------------------

_DIFF_SENTINEL = 255.0  # "definitely different" for incomparable frames


def frame_mean_luma(width: int, height: int, rgb: bytes) -> float:
    """Mean perceived luminance (Rec. 601 luma) of an RGB frame, 0..255."""
    total = width * height
    if total == 0:
        return 0.0
    acc = 0.0
    for off in range(0, total * 3, 3):
        acc += 0.299 * rgb[off] + 0.587 * rgb[off + 1] + 0.114 * rgb[off + 2]
    return acc / total


def frame_mean_abs_diff(rgb_a: bytes, rgb_b: bytes) -> float:
    """Mean absolute per-channel difference between two frames, 0..255.

    Frames of different sizes cannot be the same picture; return the
    maximum sentinel instead of raising.
    """
    if len(rgb_a) != len(rgb_b):
        return _DIFF_SENTINEL
    if not rgb_a:
        return 0.0
    acc = 0
    for i in range(len(rgb_a)):
        acc += abs(rgb_a[i] - rgb_b[i])
    return acc / len(rgb_a)


def _longest_run(flags: list[bool]) -> tuple[int, int]:
    """(start_index, length) of the longest run of True in flags."""
    best_start, best_len = 0, 0
    cur_start, cur_len = 0, 0
    for i, flag in enumerate(flags):
        if flag:
            if cur_len == 0:
                cur_start = i
            cur_len += 1
            if cur_len > best_len:
                best_start, best_len = cur_start, cur_len
        else:
            cur_len = 0
    return best_start, best_len


def analyze_frame_series(frames: list[tuple[int, int, bytes]],
                         interval: float) -> dict:
    """Black / frozen analysis of frames sampled every `interval` seconds.

    Pure function (no ffmpeg) so it is unit-testable on synthetic frames.
    A run of N black frames is N * interval seconds of black picture.
    A run of M consecutive "same as the previous frame" steps is
    M * interval seconds during which the picture provably did not
    change (a frozen segment of M + 1 frames spans at least that long).
    """
    lumas = [frame_mean_luma(w, h, rgb) for (w, h, rgb) in frames]
    black_flags = [luma <= BLACK_LUMA_MAX for luma in lumas]
    b_start, b_len = _longest_run(black_flags)
    frozen_steps = [
        frame_mean_abs_diff(frames[i][2], frames[i - 1][2]) <= FREEZE_EPSILON
        for i in range(1, len(frames))
    ]
    f_start, f_len = _longest_run(frozen_steps)
    return {
        "frames": len(frames),
        "interval": interval,
        "black": {
            "longest_seconds": round(b_len * interval, 3),
            "longest_at": round(b_start * interval, 3) if b_len else None,
        },
        "freeze": {
            # f_len frozen steps begin at step index f_start, i.e. the
            # picture was already static at frame f_start.
            "longest_seconds": round(f_len * interval, 3),
            "longest_at": round(f_start * interval, 3) if f_len else None,
        },
    }


def gate_freeze_black(video: Path, max_black: float,
                      max_freeze: float) -> GateResult:
    """Detect black holes and frozen picture from sampled frames.

    The visual counterpart of the dead-air gate: an encoder hang repeats
    one frame for seconds; a failed panel render drops the picture to
    black while the narration carries on. Frames are sampled at
    FRAME_SAMPLE_FPS, scaled down to FRAME_SAMPLE_WIDTH px, in a single
    ffmpeg pass, then analysed by analyze_frame_series.

    The freeze epsilon is deliberately tiny (a mean per-channel
    difference of 1 out of 255): a slow zoompan over a still panel —
    the normal look of this pipeline — moves pixels measurably at this
    sampling rate, while a truly repeated frame does not. Short black
    transitions under --max-black are likewise tolerated.
    """
    ffmpeg = _require_binary("ffmpeg")
    interval = 1.0 / FRAME_SAMPLE_FPS
    frames: list[tuple[int, int, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="shortsqc-fb-") as tmp:
        pattern = str(Path(tmp) / "f_%05d.ppm")
        proc = _run([
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", str(video),
            "-vf", f"fps={FRAME_SAMPLE_FPS},scale={FRAME_SAMPLE_WIDTH}:-1",
            "-f", "image2", "-c:v", "ppm", pattern,
        ])
        if proc.returncode != 0:
            return GateResult(
                "freeze-black", "error",
                f"frame sampling failed: {proc.stderr.strip()[:200]}", {})
        for path in sorted(Path(tmp).glob("f_*.ppm")):
            try:
                frames.append(parse_ppm_bytes(path.read_bytes()))
            except ValueError:
                continue
    if not frames:
        return GateResult("freeze-black", "error",
                          "could not sample any frame for the "
                          "freeze/black check", {})
    report = analyze_frame_series(frames, interval)
    black = report["black"]
    freeze = report["freeze"]
    details = {"max_black": max_black, "max_freeze": max_freeze, **report}
    problems = []
    if black["longest_seconds"] > max_black:
        problems.append(
            f"black picture for {black['longest_seconds']}s at "
            f"{black['longest_at']}s (limit {max_black}s)")
    if freeze["longest_seconds"] > max_freeze:
        problems.append(
            f"frozen picture for {freeze['longest_seconds']}s at "
            f"{freeze['longest_at']}s (limit {max_freeze}s)")
    if problems:
        return GateResult("freeze-black", "fail", "; ".join(problems),
                          details)
    return GateResult(
        "freeze-black", "pass",
        f"no black run over {max_black}s and no frozen run over "
        f"{max_freeze}s across {report['frames']} sampled frame(s) "
        f"(longest black {black['longest_seconds']}s, "
        f"longest freeze {freeze['longest_seconds']}s)",
        details)


# --------------------------------------------------------------------------
# f. Caption-fit gate
# --------------------------------------------------------------------------

def caption_capacity(width_px: int, fontsize: float) -> int:
    """Max full-width characters that safely fit on one caption line.

    Production rule: at 720 px wide with fontsize 60, at most 11
    full-width characters fit — a 12th is clipped at the frame edge.
    The raw division 720 / 60 = 12 ignores the horizontal safety margin;
    reserving half a fontsize of padding on each side gives
    floor((width - fontsize) / fontsize) = 11 for that case, and scales
    the same way at other sizes.
    """
    if fontsize <= 0:
        raise ValueError("fontsize must be positive")
    if width_px <= 0:
        raise ValueError("width must be positive")
    return max(0, math.floor((width_px - fontsize) / fontsize))


def char_display_width(ch: str) -> float:
    """Display width of one character in full-width units."""
    return 1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.55


def line_display_width(text: str) -> float:
    return sum(char_display_width(ch) for ch in text)


def caption_line_verdicts(lines: list[str], width_px: int, fontsize: float) -> list[dict]:
    capacity = caption_capacity(width_px, fontsize)
    verdicts = []
    for line in lines:
        width_units = line_display_width(line)
        verdicts.append({
            "line": line,
            "width_units": round(width_units, 2),
            "capacity": capacity,
            "status": "pass" if width_units <= capacity + 1e-9 else "fail",
        })
    return verdicts


def run_caption(args: argparse.Namespace) -> int:
    lines: list[str] = list(args.lines or [])
    if args.file:
        file_path = Path(args.file)
        if not file_path.is_file():
            print(f"error: caption file not found: {file_path}", file=sys.stderr)
            return EXIT_ERROR
        try:
            # utf-8-sig: a BOM (Notepad's default UTF-8 flavour) is
            # stripped instead of silently inflating the first line's
            # measured width by half a character.
            text = file_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            print(f"error: caption file is not valid UTF-8: {file_path} "
                  f"(re-save it as UTF-8 and retry)", file=sys.stderr)
            return EXIT_ERROR
        except OSError as exc:
            print(f"error: cannot read caption file {file_path}: {exc}",
                  file=sys.stderr)
            return EXIT_ERROR
        lines.extend(ln.rstrip("\n") for ln in text.splitlines())
    if not lines:
        print("error: no caption lines given (pass lines as arguments or --file)",
              file=sys.stderr)
        return EXIT_ERROR
    try:
        verdicts = caption_line_verdicts(lines, args.width, args.fontsize)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    capacity = verdicts[0]["capacity"]
    print(f"caption-fit: width={args.width}px fontsize={args.fontsize} "
          f"-> capacity {capacity} full-width chars/line")
    failed = 0
    for v in verdicts:
        mark = "PASS" if v["status"] == "pass" else "FAIL"
        if v["status"] == "fail":
            failed += 1
        print(f"[{mark}] ({v['width_units']:.2f}/{capacity}) {v['line']}")
    if failed:
        print(f"caption-fit: FAIL — {failed} line(s) would be clipped")
        return EXIT_FAIL
    print("caption-fit: PASS — all lines fit")
    return EXIT_PASS


# --------------------------------------------------------------------------
# g. Language gate (optional, faster-whisper)
# --------------------------------------------------------------------------

_LATIN_RUN_RE = re.compile(r"[A-Za-z]{4,}")


def gate_language(video: Path, target_lang: str) -> GateResult:
    """Detect narration in the wrong language (needs faster-whisper).

    Skips cleanly — never crashes — when faster-whisper is not installed.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return GateResult(
            "language", "skip",
            "skipped: pip install faster-whisper (language gate not available)",
            {"target_lang": target_lang},
        )

    ffmpeg = _require_binary("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="shortsqc-") as tmp:
        wav = Path(tmp) / "audio.wav"
        proc = _run([
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav),
        ])
        if proc.returncode != 0 or not wav.exists():
            return GateResult("language", "error",
                              "could not extract audio for the language gate", {})
        try:
            model = WhisperModel("tiny", device="cpu", compute_type="int8")
            segments, _info = model.transcribe(str(wav), language=None)
            segments = list(segments)
        except Exception as exc:  # model download / runtime problems: report, don't crash
            return GateResult("language", "error",
                              f"faster-whisper failed: {exc}", {})

    offenders = []
    for seg in segments:
        text = getattr(seg, "text", "") or ""
        if target_lang == "ja" and _LATIN_RUN_RE.search(text):
            offenders.append(text.strip())
    if offenders:
        return GateResult(
            "language", "fail",
            f"{len(offenders)} segment(s) contain Latin-alphabet speech while "
            f"--lang {target_lang} was expected (possible wrong-language TTS): "
            f"{offenders[0][:80]}",
            {"target_lang": target_lang, "offending_segments": offenders},
        )
    return GateResult(
        "language", "pass",
        f"no wrong-language segments detected (target {target_lang}, "
        f"{len(segments)} segment(s) transcribed)",
        {"target_lang": target_lang, "segments": len(segments)},
    )


# --------------------------------------------------------------------------
# check command
# --------------------------------------------------------------------------

def run_check(args: argparse.Namespace) -> int:
    video = Path(args.video)
    if not video.is_file():
        print(f"error: video not found: {video}", file=sys.stderr)
        return EXIT_ERROR
    if args.min_lufs > args.max_lufs:
        print(f"error: --min-lufs ({args.min_lufs}) must not exceed "
              f"--max-lufs ({args.max_lufs})", file=sys.stderr)
        return EXIT_ERROR
    try:
        _require_binary("ffmpeg")
        probe_result, info = gate_probe(video, args.expect)
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    gates: list[GateResult] = [
        probe_result,
        gate_loudness(video, args.min_lufs, args.max_lufs),
        gate_dead_air(video, info["duration"], args.max_gap,
                      has_audio=info["has_audio"]),
        gate_white_edge(video, info["duration"]),
        gate_freeze_black(video, args.max_black, args.max_freeze),
    ]
    if args.lang:
        gates.append(gate_language(video, args.lang))
    else:
        gates.append(GateResult(
            "language", "skip",
            "skipped: no --lang given (pass --lang ja to check narration language)",
            {},
        ))

    overall_pass = all(g.ok for g in gates) and not any(g.status == "error" for g in gates)

    if args.json:
        payload = {
            "video": str(video),
            "overall": "pass" if overall_pass else "fail",
            "gates": [g.to_dict() for g in gates],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"ShortsQC report — {video}")
        for g in gates:
            print(f"[{g.status.upper():>4}] {g.name}: {g.summary}")
        print(f"OVERALL: {'PASS' if overall_pass else 'FAIL'}")
    return EXIT_PASS if overall_pass else EXIT_FAIL


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _non_negative_float(text: str) -> float:
    """argparse type for duration thresholds: negative values are a
    usage error (exit 2), not a verdict — a negative limit makes even
    a zero-length run "exceed" it and produced nonsense FAILs."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {text!r}")
    if value < 0:
        raise argparse.ArgumentTypeError(
            f"value must not be negative: {text!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shorts-qc",
        description="Quality gates for AI-generated short-form video "
                    "(probe, loudness, dead air, white edges, black/frozen "
                    "frames, caption fit, language).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="run all gates on a video file")
    p_check.add_argument("video", help="path to the video file")
    p_check.add_argument("--expect", choices=["9:16", "16:9"], default=None,
                         help="expected aspect ratio (default: no expectation)")
    p_check.add_argument("--min-lufs", type=float, default=DEFAULT_MIN_LUFS,
                         help=f"loudness floor in LUFS (default {DEFAULT_MIN_LUFS})")
    p_check.add_argument("--max-lufs", type=float, default=DEFAULT_MAX_LUFS,
                         help=f"loudness ceiling in LUFS (default {DEFAULT_MAX_LUFS})")
    p_check.add_argument("--max-gap", type=_non_negative_float,
                         default=DEFAULT_MAX_GAP,
                         help=f"max tolerated inside silence gap in seconds "
                              f"(default {DEFAULT_MAX_GAP})")
    p_check.add_argument("--max-black", type=_non_negative_float,
                         default=DEFAULT_MAX_BLACK,
                         help=f"max tolerated continuous black picture in "
                              f"seconds (default {DEFAULT_MAX_BLACK})")
    p_check.add_argument("--max-freeze", type=_non_negative_float,
                         default=DEFAULT_MAX_FREEZE,
                         help=f"max tolerated frozen (unchanging) picture in "
                              f"seconds (default {DEFAULT_MAX_FREEZE})")
    p_check.add_argument("--lang", default=None,
                         help="expected narration language, e.g. ja "
                              "(enables the optional faster-whisper gate)")
    p_check.add_argument("--json", action="store_true",
                         help="print the report as JSON")
    p_check.set_defaults(func=run_check)

    p_cap = sub.add_parser("caption", help="check whether caption lines fit the frame")
    p_cap.add_argument("--width", type=int, default=720,
                       help="frame width in px (default 720)")
    p_cap.add_argument("--fontsize", type=float, default=60,
                       help="caption fontsize in px (default 60)")
    p_cap.add_argument("--file", default=None,
                       help="UTF-8 text file with one caption line per line")
    p_cap.add_argument("lines", nargs="*", help="caption lines to check")
    p_cap.set_defaults(func=run_caption)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

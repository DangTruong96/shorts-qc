# Security policy

## Supported versions

ShortsQC is pre-1.0. Security fixes are applied to the latest release
only; older releases do not receive backports.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |
| < 0.1   | No        |

## Reporting a vulnerability

Please report vulnerabilities **privately** — do not open a public issue
for a security problem.

Use GitHub's private vulnerability reporting for this repository:
**Security → Advisories → Report a vulnerability** on
[github.com/DangTruong96/shorts-qc](https://github.com/DangTruong96/shorts-qc/security/advisories).

Please include:

- the ShortsQC version and the exact command you ran,
- a description of the issue and its impact,
- a minimal video file or input that reproduces it, if you can share one.

You can expect an acknowledgement within 7 days. Confirmed issues are
fixed in a patch release, and the reporter is credited in the release
notes unless they prefer to stay anonymous.

## Scope notes

ShortsQC is a local, offline command-line tool:

- It runs `ffmpeg`/`ffprobe` as subprocesses on files you point it at,
  and never sends video, audio, or transcripts anywhere.
- The optional language gate (`faster-whisper`) may download a speech
  model from Hugging Face the first time it runs; after that it works
  offline. If that download is a concern in your environment, simply do
  not install the extra — the gate skips cleanly.
- Because ShortsQC passes file paths to ffmpeg, treat untrusted file
  paths and untrusted media files with the same care you would give
  ffmpeg itself, and keep ffmpeg up to date.

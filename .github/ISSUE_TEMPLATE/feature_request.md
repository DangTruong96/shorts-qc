---
name: Feature request
about: Suggest a new gate or an improvement to an existing one
title: "[Feature] "
labels: enhancement
assignees: ""
---

## The production failure behind this request

<!--
ShortsQC only grows gates that catch a concrete way an AI/TTS video
pipeline actually breaks. Please describe the incident first:
what shipped (or nearly shipped), and how you noticed it.
Requests without a real failure behind them are unlikely to be accepted.
-->

## Proposed check

<!-- What should the tool measure, and what threshold separates
     "fine" from "broken"? If you have a measurement or an incident
     behind the number, say so — thresholds here are production rules,
     not guesses. -->

## Would it stay standard-library only?

<!-- Runtime dependencies beyond ffmpeg/ffprobe are a hard constraint.
     The one existing exception is the optional faster-whisper extra. -->

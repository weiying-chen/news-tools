#!/usr/bin/env python3
"""Detect scripted news VO and add missing timecodes using Faster-Whisper."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Sequence


TIMECODE_RE = re.compile(r"^\d{3,4}$")
CJK_RE = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True)
class VoPassage:
    line_index: int
    text: str
    timecode: str | None


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class VoMatch:
    passage: VoPassage
    start_seconds: float
    score: float


def _is_chinese_source(line: str) -> bool:
    return len(CJK_RE.findall(line)) >= 2


def extract_vo_passages(body: str) -> list[VoPassage]:
    """Return Chinese VO paragraphs outside SUPER, SB, and REPORT structures."""
    lines = body.splitlines()
    passages: list[VoPassage] = []
    pending_timecode: str | None = None
    in_comment = False

    for index, raw in enumerate(lines):
        line = raw.strip()
        if in_comment:
            if "*/" in line:
                in_comment = False
            continue
        if line.startswith("/*"):
            in_comment = "*/" not in line
            pending_timecode = None
            continue
        if not line:
            continue
        if TIMECODE_RE.fullmatch(line):
            pending_timecode = line
            continue
        if line == "~" or line.startswith("(") or line.endswith("*/"):
            continue
        if not _is_chinese_source(line):
            continue

        passages.append(VoPassage(index, line, pending_timecode))
        pending_timecode = None

    return passages


def remove_existing_timecodes(body: str) -> str:
    """Return a body copy without standalone VO timecodes."""
    kept = [line for line in body.splitlines() if not TIMECODE_RE.fullmatch(line.strip())]
    rendered = "\n".join(kept)
    if body.endswith("\n"):
        rendered += "\n"
    return rendered


def _normalize_for_alignment(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(char for char in text if char.isalnum())


def _window_score(source: str, candidate: str) -> float:
    if not source or not candidate:
        return 0.0
    similarity = SequenceMatcher(None, source, candidate).ratio()
    length_ratio = min(len(source), len(candidate)) / max(len(source), len(candidate))
    return similarity * (0.75 + 0.25 * length_ratio)


def align_vo_passages(
    passages: Sequence[VoPassage],
    segments: Sequence[TranscriptSegment],
    *,
    max_window_segments: int = 12,
) -> list[VoMatch]:
    """Fuzzily match known VO text to ordered Whisper transcript windows."""
    matches: list[VoMatch] = []
    search_from = 0

    for passage in passages:
        source = _normalize_for_alignment(passage.text)
        best: tuple[float, int, int] | None = None
        for start in range(search_from, len(segments)):
            combined = ""
            for end in range(start, min(len(segments), start + max_window_segments)):
                combined += _normalize_for_alignment(segments[end].text)
                score = _window_score(source, combined)
                if best is None or score > best[0]:
                    best = (score, start, end)
                if len(combined) > len(source) * 1.8:
                    break

        if best is None:
            raise ValueError(f"no transcript remains for VO: {passage.text[:40]}")
        score, start, end = best
        matches.append(VoMatch(passage, segments[start].start, score))
        search_from = end + 1

    return matches

def _format_timecode(seconds: float) -> str:
    rounded = math.floor(seconds + 0.5)
    minutes, remaining_seconds = divmod(rounded, 60)
    return f"{minutes:02d}{remaining_seconds:02d}"


def render_timestamped_body(body: str, matches: Sequence[VoMatch]) -> str:
    insertions = {
        match.passage.line_index: _format_timecode(match.start_seconds)
        for match in matches
        if match.passage.timecode is None
    }
    output: list[str] = []
    for index, line in enumerate(body.splitlines()):
        if index in insertions:
            output.append(insertions[index])
        output.append(line)
    rendered = "\n".join(output)
    if body.endswith("\n"):
        rendered += "\n"
    return rendered


def transcribe(video: Path, model_name: str) -> list[TranscriptSegment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError(
            "faster-whisper is not installed for this Python interpreter; "
            "run this command with a virtual environment that provides it"
        ) from error

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    raw_segments, _ = model.transcribe(
        str(video),
        language="zh",
        beam_size=1,
        vad_filter=True,
        word_timestamps=True,
    )
    return [
        TranscriptSegment(segment.start, segment.end, segment.text)
        for segment in raw_segments
    ]


def timestamp_body(
    body_path: Path,
    video_path: Path,
    *,
    model_name: str = "small",
    min_score: float = 0.45,
) -> list[VoMatch]:
    """Add missing VO timecodes to body_path after validating every alignment."""
    body = body_path.read_text(encoding="utf-8")
    passages = extract_vo_passages(body)
    if not passages:
        raise ValueError("no VO passages detected")

    matches = align_vo_passages(passages, transcribe(video_path, model_name))
    weak = [match for match in matches if match.score < min_score]
    if weak:
        raise ValueError(
            f"{len(weak)} VO alignment(s) below minimum score; body not changed"
        )

    body_path.write_text(render_timestamped_body(body, matches), encoding="utf-8")
    return matches

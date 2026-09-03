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
class TranscriptWord:
    start: float
    end: float
    text: str
    probability: float


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: tuple[TranscriptWord, ...] = ()


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


def _refined_segment_start(segment: TranscriptSegment) -> float:
    if not segment.words or segment.words[0].probability >= 0.2:
        return segment.start
    for word in segment.words[1:]:
        if word.probability >= 0.5:
            return word.start
    return segment.start


def _refined_match_start(
    segments: Sequence[TranscriptSegment],
    start: int,
    end: int,
    source: str,
) -> float:
    """Locate the passage onset within a matched transcript window."""
    normalized_words: list[str] = []
    word_starts: list[float] = []
    for segment in segments[start : end + 1]:
        for word in segment.words:
            normalized = _normalize_for_alignment(word.text)
            for char in normalized:
                normalized_words.append(char)
                word_starts.append(word.start)

    candidate = "".join(normalized_words)
    if not candidate:
        return _refined_segment_start(segments[start])

    exact_start = candidate.find(source)
    if exact_start >= 0:
        if exact_start == 0:
            return _refined_segment_start(segments[start])
        return word_starts[exact_start]

    # Whisper can substitute a few characters. Use a matching block near the
    # source opening, while rejecting coincidental single-character matches.
    opening_limit = max(2, len(source) // 10)
    minimum_block = 2 if len(source) >= 4 else 1
    blocks = SequenceMatcher(None, source, candidate).get_matching_blocks()
    opening_blocks = [
        block
        for block in blocks
        if block.size >= minimum_block and block.a <= opening_limit
    ]
    if opening_blocks:
        block = min(opening_blocks, key=lambda item: (item.a, item.b))
        if block.b == 0:
            return _refined_segment_start(segments[start])
        return word_starts[block.b]

    return _refined_segment_start(segments[start])


def align_vo_passages(
    passages: Sequence[VoPassage],
    segments: Sequence[TranscriptSegment],
    *,
    max_window_segments: int | None = None,
) -> list[VoMatch]:
    """Fuzzily match known VO text to ordered Whisper transcript windows."""
    matches: list[VoMatch] = []
    search_from = 0

    for passage in passages:
        source = _normalize_for_alignment(passage.text)
        best: tuple[float, int, int] | None = None
        for start in range(search_from, len(segments)):
            combined = ""
            end_limit = (
                len(segments)
                if max_window_segments is None
                else min(len(segments), start + max_window_segments)
            )
            for end in range(start, end_limit):
                combined += _normalize_for_alignment(segments[end].text)
                score = _window_score(source, combined)
                if best is None or score > best[0]:
                    best = (score, start, end)
                if len(combined) > len(source) * 1.8:
                    break

        if best is None:
            raise ValueError(f"no transcript remains for VO: {passage.text[:40]}")
        score, start, end = best
        matches.append(
            VoMatch(passage, _refined_match_start(segments, start, end, source), score)
        )
        search_from = start + 1

    return matches

def _format_timecode(seconds: float) -> str:
    nearest_second = math.floor(seconds + 0.5)
    minutes, remaining_seconds = divmod(nearest_second, 60)
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
        TranscriptSegment(
            segment.start,
            segment.end,
            segment.text,
            words=tuple(
                TranscriptWord(word.start, word.end, word.word, word.probability)
                for word in (segment.words or ())
            ),
        )
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

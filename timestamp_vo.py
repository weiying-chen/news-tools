#!/usr/bin/env python3
"""Detect scripted news VO and add missing timecodes using Faster-Whisper."""

from __future__ import annotations

import math
import re
import sys
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
    end_seconds: float | None = None
    timing_anomaly: bool = False


@dataclass(frozen=True)
class SuperBlock:
    start_line_index: int
    end_line_index: int
    duration_seconds: int | None

    @property
    def has_duration(self) -> bool:
        return self.duration_seconds is not None


@dataclass(frozen=True)
class SuperDuration:
    line_index: int
    seconds: int


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
        if "#大愛新聞" in line:
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


def _fuzzy_opening_offset(source: str, candidate: str) -> int | None:
    """Find the strongest approximate occurrence of the scripted opening."""
    prefix_length = min(12, max(4, math.ceil(len(source) / 2)))
    prefix = source[:prefix_length]
    minimum_length = max(2, prefix_length - 2)
    best: tuple[float, int] | None = None
    for offset in range(len(candidate)):
        maximum_length = min(prefix_length + 2, len(candidate) - offset)
        for length in range(minimum_length, maximum_length + 1):
            score = SequenceMatcher(
                None,
                prefix,
                candidate[offset : offset + length],
            ).ratio()
            if best is None or score > best[0]:
                best = (score, offset)
    if best is not None and best[0] >= 0.6:
        return best[1]
    return None


def _refined_segment_start(
    segment: TranscriptSegment,
    previous_segment: TranscriptSegment | None = None,
) -> float:
    if not segment.words or segment.words[0].probability >= 0.2:
        return segment.start
    if previous_segment is None or abs(segment.start - previous_segment.end) > 0.2:
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
    first_segment = segments[start]
    if first_segment.words and first_segment.words[0].probability < 0.2:
        first_word = first_segment.words[0]
        reliable_word = next(
            (word for word in first_segment.words[1:] if word.probability >= 0.5),
            None,
        )
        if reliable_word is not None and reliable_word.start - first_word.end >= 2.0:
            return reliable_word.start

    previous_segment = segments[start - 1] if start > 0 else None
    if previous_segment is not None and segments[start].start - previous_segment.end > 0.2:
        return segments[start].start

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
        return _refined_segment_start(segments[start], previous_segment)

    exact_start = candidate.find(source)
    if exact_start >= 0:
        if exact_start == 0:
            return _refined_segment_start(segments[start], previous_segment)
        return word_starts[exact_start]

    # Whisper can substitute a few opening characters. Compare a meaningful
    # prefix so an earlier incidental word does not pull the onset backward.
    opening_offset = _fuzzy_opening_offset(source, candidate)
    if opening_offset is not None:
        if opening_offset == 0:
            return _refined_segment_start(segments[start], previous_segment)
        return word_starts[opening_offset]

    return _refined_segment_start(segments[start], previous_segment)


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
            VoMatch(
                passage,
                _refined_match_start(segments, start, end, source),
                score,
                end_seconds=segments[end].end,
                timing_anomaly=any(
                    word.end - word.start >= 2.0
                    for segment in segments[start : end + 1]
                    for word in segment.words
                ),
            )
        )
        search_from = start + 1

    return matches

def _format_timecode(seconds: float) -> str:
    current_second = math.floor(seconds)
    fraction = seconds - current_second
    rounded_second = current_second if fraction <= 0.2 + 1e-9 else current_second + 1
    minutes, remaining_seconds = divmod(rounded_second, 60)
    return f"{minutes:02d}{remaining_seconds:02d}"


def _duration_from_cue(line: str) -> int | None:
    stripped = line.strip()
    if re.fullmatch(r"\d{1,2}", stripped):
        return int(stripped)
    explicit_seconds = re.findall(
        r"[（(][^（）()]*?(\d{1,3})\s*秒[^（）()]*[）)]",
        stripped,
    )
    if explicit_seconds:
        return int(explicit_seconds[-1])
    bare_parenthesized = re.findall(r"[（(]\s*(\d{1,2})\s*[）)]", stripped)
    return int(bare_parenthesized[-1]) if bare_parenthesized else None


def extract_super_blocks(body: str) -> list[SuperBlock]:
    lines = body.splitlines()
    blocks: list[SuperBlock] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() != "/*SUPER:":
            index += 1
            continue
        end = index + 1
        while end < len(lines) and "*/" not in lines[end]:
            end += 1
        if end >= len(lines):
            break

        previous = index - 1
        while previous >= 0 and not lines[previous].strip():
            previous -= 1
        following_duration: int | None = None
        following = end + 1
        while following < len(lines):
            following_line = lines[following].strip()
            if TIMECODE_RE.fullmatch(following_line) or following_line.startswith("/*"):
                break
            if re.fullmatch(r"\d{1,2}", following_line):
                following_duration = int(following_line)
                break
            following += 1
        preceding_duration = (
            _duration_from_cue(lines[previous]) if previous >= 0 else None
        )
        blocks.append(
            SuperBlock(
                index,
                end,
                preceding_duration
                if preceding_duration is not None
                else following_duration,
            )
        )
        index = end + 1
    return blocks


def infer_missing_super_durations(
    body: str,
    matches: Sequence[VoMatch],
    segments: Sequence[TranscriptSegment],
) -> tuple[list[SuperDuration], list[str]]:
    blocks = extract_super_blocks(body)
    ordered_matches = sorted(matches, key=lambda match: match.passage.line_index)
    grouped: dict[tuple[int | None, int], list[SuperBlock]] = {}
    unbounded: list[SuperBlock] = []

    for block in blocks:
        if block.has_duration:
            continue
        previous = [
            match
            for match in ordered_matches
            if match.passage.line_index < block.start_line_index
            and match.end_seconds is not None
        ]
        following = [
            match
            for match in ordered_matches
            if match.passage.line_index > block.end_line_index
        ]
        if not following:
            unbounded.append(block)
            continue
        following_match = following[0]
        if not previous:
            key = (None, ordered_matches.index(following_match))
            grouped.setdefault(key, []).append(block)
            continue
        previous_match = previous[-1]
        key = (
            ordered_matches.index(previous_match),
            ordered_matches.index(following_match),
        )
        grouped.setdefault(key, []).append(block)

    durations: list[SuperDuration] = []
    warnings = [
        f"SUPER ending on line {block.end_line_index + 1} has no surrounding VO anchors"
        for block in unbounded
    ]
    all_blocks = extract_super_blocks(body)
    for (previous_index, following_index), missing_blocks in grouped.items():
        following_match = ordered_matches[following_index]
        if previous_index is None:
            blocks_between = [
                block
                for block in all_blocks
                if block.end_line_index < following_match.passage.line_index
            ]
        else:
            previous_match = ordered_matches[previous_index]
            blocks_between = [
                block
                for block in all_blocks
                if previous_match.passage.line_index < block.start_line_index
                and block.end_line_index < following_match.passage.line_index
            ]
        if len(missing_blocks) != 1 or any(
            block.duration_seconds is None and block not in missing_blocks
            for block in blocks_between
        ):
            warnings.extend(
                f"SUPER ending on line {block.end_line_index + 1} shares an ambiguous VO gap"
                for block in missing_blocks
            )
            continue

        upper = following_match.start_seconds
        if previous_index is None:
            speech = [segment for segment in segments if segment.end <= upper + 0.2]
        else:
            lower = previous_match.end_seconds
            assert lower is not None
            speech = [
                segment
                for segment in segments
                if segment.start >= lower - 0.2 and segment.end <= upper + 0.2
            ]
        if not speech:
            block = missing_blocks[0]
            warnings.append(
                f"SUPER ending on line {block.end_line_index + 1} has no clear speech interval"
            )
            continue
        known_seconds = sum(
            block.duration_seconds or 0 for block in blocks_between
        )
        remaining_seconds = speech[-1].end - speech[0].start - known_seconds
        if remaining_seconds <= 0:
            block = missing_blocks[0]
            warnings.append(
                f"SUPER ending on line {block.end_line_index + 1} has no remaining speech time"
            )
            continue
        seconds = max(1, math.floor(remaining_seconds + 0.5))
        durations.append(SuperDuration(missing_blocks[0].end_line_index, seconds))

    return durations, warnings


def render_timestamped_body(
    body: str,
    matches: Sequence[VoMatch],
    *,
    super_durations: Sequence[SuperDuration] = (),
) -> str:
    insertions = {
        match.passage.line_index: _format_timecode(match.start_seconds)
        for match in matches
        if match.passage.timecode is None
    }
    output: list[str] = []
    durations_by_line = {
        duration.line_index: str(duration.seconds) for duration in super_durations
    }
    for index, line in enumerate(body.splitlines()):
        if index in insertions:
            output.append(insertions[index])
        output.append(line)
        if index in durations_by_line:
            output.append(durations_by_line[index])
    rendered = "\n".join(output)
    if body.endswith("\n"):
        rendered += "\n"
    return rendered


def _merge_clip_ranges(
    ranges: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def transcribe(
    video: Path,
    model_name: str,
    *,
    clip_ranges: Sequence[tuple[float, float]] | None = None,
) -> list[TranscriptSegment]:
    try:
        from faster_whisper import WhisperModel
        from faster_whisper.audio import decode_audio
    except ImportError as error:
        raise RuntimeError(
            "faster-whisper is not installed for this Python interpreter; "
            "run this command with a virtual environment that provides it"
        ) from error

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    audio_inputs: list[tuple[object, float]] = [(str(video), 0.0)]
    if clip_ranges:
        sampling_rate = 16000
        audio = decode_audio(str(video), sampling_rate=sampling_rate)
        audio_inputs = [
            (
                audio[
                    max(0, round(start * sampling_rate)) : round(end * sampling_rate)
                ],
                start,
            )
            for start, end in clip_ranges
        ]

    segments: list[TranscriptSegment] = []
    for audio_input, offset in audio_inputs:
        raw_segments, _ = model.transcribe(
            audio_input,
            language="zh",
            beam_size=1,
            vad_filter=True,
            word_timestamps=True,
        )
        segments.extend(
            TranscriptSegment(
                segment.start + offset,
                segment.end + offset,
                segment.text,
                words=tuple(
                    TranscriptWord(
                        word.start + offset,
                        word.end + offset,
                        word.word,
                        word.probability,
                    )
                    for word in (segment.words or ())
                ),
            )
            for segment in raw_segments
        )
    return segments


def verify_suspicious_vo_matches(
    video_path: Path,
    matches: Sequence[VoMatch],
    *,
    fallback_model_name: str = "medium",
    suspicious_score: float = 0.8,
    minimum_improvement: float = 0.05,
    timing_repair_min_score: float = 0.65,
) -> list[VoMatch]:
    suspicious = [
        match
        for match in matches
        if match.score < suspicious_score or match.timing_anomaly
    ]
    if not suspicious:
        return list(matches)

    ranges_by_line = {
        match.passage.line_index: (
            max(0.0, match.start_seconds - 8.0),
            (match.end_seconds or match.start_seconds) + 3.0,
        )
        for match in suspicious
    }
    clip_ranges = _merge_clip_ranges(list(ranges_by_line.values()))
    fallback_segments = transcribe(
        video_path,
        fallback_model_name,
        clip_ranges=clip_ranges,
    )

    replacements: dict[int, VoMatch] = {}
    for original in suspicious:
        clip_start, clip_end = ranges_by_line[original.passage.line_index]
        local_segments = [
            segment
            for segment in fallback_segments
            if segment.end >= clip_start and segment.start <= clip_end
        ]
        if not local_segments:
            continue
        candidate = align_vo_passages([original.passage], local_segments)[0]
        materially_improved = candidate.score >= original.score + minimum_improvement
        repaired_timing = (
            original.timing_anomaly
            and not candidate.timing_anomaly
            and candidate.score >= timing_repair_min_score
        )
        if materially_improved or repaired_timing:
            replacements[original.passage.line_index] = candidate

    return [replacements.get(match.passage.line_index, match) for match in matches]


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

    segments = transcribe(video_path, model_name)
    matches = align_vo_passages(passages, segments)
    if model_name == "small":
        matches = verify_suspicious_vo_matches(video_path, matches)
    weak = [match for match in matches if match.score < min_score]
    if weak:
        raise ValueError(
            f"{len(weak)} VO alignment(s) below minimum score; body not changed"
        )

    super_durations, warnings = infer_missing_super_durations(body, matches, segments)
    for warning in warnings:
        print(f"[warn] {warning}", file=sys.stderr)
    body_path.write_text(
        render_timestamped_body(body, matches, super_durations=super_durations),
        encoding="utf-8",
    )
    return matches

#!/usr/bin/env python3
"""Review a local news video with its timestamped English narration MP3s."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


VIDEO_EXTENSIONS = {".mkv", ".mov", ".mp4", ".webm"}
NARRATION_RE = re.compile(r"^\d+_(\d{3,4})\.mp3$", re.IGNORECASE)
CACHE_VERSION = 3


@dataclass(frozen=True)
class NarrationClip:
    path: Path
    start_seconds: int


def parse_narration_clip(path: Path) -> NarrationClip:
    match = NARRATION_RE.fullmatch(path.name)
    if not match:
        raise ValueError(f"not a timestamped narration MP3: {path.name}")

    timestamp = match.group(1).zfill(4)
    minutes = int(timestamp[:-2])
    seconds = int(timestamp[-2:])
    if seconds >= 60:
        raise ValueError(f"invalid seconds in narration timestamp: {path.name}")
    return NarrationClip(path=path, start_seconds=minutes * 60 + seconds)


def discover_media(
    directory: Path,
    explicit_video: Path | None = None,
) -> tuple[Path, list[NarrationClip]]:
    directory = directory.resolve()
    if explicit_video is not None:
        video = explicit_video
        if not video.is_absolute():
            video = directory / video
        if not video.is_file():
            raise ValueError(f"video not found: {video}")
    else:
        videos = sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )
        if not videos:
            raise ValueError(f"no video found in {directory}")
        if len(videos) > 1:
            names = ", ".join(path.name for path in videos)
            raise ValueError(f"multiple video files found; specify one: {names}")
        video = videos[0]

    clips: list[NarrationClip] = []
    for path in directory.iterdir():
        if not path.is_file() or not NARRATION_RE.fullmatch(path.name):
            continue
        clips.append(parse_narration_clip(path))
    clips.sort(key=lambda clip: (clip.start_seconds, clip.path.name))

    if not clips:
        raise ValueError(f"no timestamped MP3 files found in {directory}")
    for previous, current in zip(clips, clips[1:]):
        if previous.start_seconds == current.start_seconds:
            raise ValueError(
                f"duplicate narration timestamp: {previous.path.name}, {current.path.name}"
            )
    return video.resolve(), clips


def _number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def build_ffmpeg_command(
    *,
    ffmpeg: str,
    video: Path,
    clips: Sequence[NarrationClip],
    video_duration: float,
    output: Path,
    english_volume: float,
) -> list[str]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
    ]
    for clip in clips:
        command.extend(["-i", str(clip.path)])

    filter_parts = [
        "[0:a]aresample=48000,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo[original]"
    ]
    english_inputs: list[str] = []
    for index, clip in enumerate(clips, start=1):
        next_start = (
            clips[index].start_seconds
            if index < len(clips)
            else video_duration
        )
        section_duration = max(0.0, next_start - clip.start_seconds)
        label = f"clip{index}"
        filter_parts.append(
            f"[{index}:a]"
            f"atrim=duration={_number(section_duration)},"
            "asetpts=PTS-STARTPTS,"
            "aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"adelay={clip.start_seconds * 1000}:all=1"
            f"[{label}]"
        )
        english_inputs.append(f"[{label}]")

    filter_parts.append(
        "".join(english_inputs)
        + f"amix=inputs={len(english_inputs)}:duration=longest:normalize=0,"
        f"volume={_number(english_volume)}[english]"
    )
    filter_parts.append(
        "[original][english]amix=inputs=2:duration=first:normalize=0[out]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[out]",
            "-c:a",
            "flac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-t",
            _number(video_duration),
            str(output),
        ]
    )
    return command


def source_fingerprint(
    video: Path,
    clips: Sequence[NarrationClip],
    english_volume: float = 1.0,
) -> dict[str, object]:
    def file_state(path: Path) -> dict[str, object]:
        stat = path.stat()
        return {
            "path": str(path.resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }

    return {
        "version": CACHE_VERSION,
        "english_volume": english_volume,
        "video": file_state(video),
        "clips": [
            {
                **file_state(clip.path),
                "start_seconds": clip.start_seconds,
            }
            for clip in clips
        ],
    }


def cache_is_current(
    output: Path,
    manifest: Path,
    fingerprint: dict[str, object],
) -> bool:
    if not output.is_file() or not manifest.is_file():
        return False
    try:
        cached = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return cached == fingerprint


def cache_paths(directory: Path, cache_root: Path) -> tuple[Path, Path]:
    key = hashlib.sha256(str(directory.resolve()).encode("utf-8")).hexdigest()[:16]
    cache_directory = cache_root / key
    return cache_directory / "mixed-timeline.flac", cache_directory / "manifest.json"


def probe_duration(ffprobe: str, video: Path) -> float:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as error:
        raise RuntimeError(f"could not read video duration: {video}") from error
    if duration <= 0:
        raise RuntimeError(f"video duration must be positive: {video}")
    return duration


def ensure_timeline(
    *,
    ffmpeg: str,
    ffprobe: str,
    video: Path,
    clips: Sequence[NarrationClip],
    output: Path,
    manifest: Path,
    rebuild: bool,
    english_volume: float,
) -> bool:
    fingerprint = source_fingerprint(video, clips, english_volume)
    if not rebuild and cache_is_current(output, manifest, fingerprint):
        return False

    duration = probe_duration(ffprobe, video)
    clips_within_video = [clip for clip in clips if clip.start_seconds < duration]
    if not clips_within_video:
        raise ValueError("all narration timestamps are after the end of the video")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.stem}.tmp{output.suffix}")
    command = build_ffmpeg_command(
        ffmpeg=ffmpeg,
        video=video,
        clips=clips_within_video,
        video_duration=duration,
        output=temporary,
        english_volume=english_volume,
    )
    try:
        subprocess.run(command, check=True)
        temporary.replace(output)
        manifest.write_text(
            json.dumps(fingerprint, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def build_mpv_command(
    *,
    mpv: str,
    video: Path,
    timeline: Path,
) -> list[str]:
    return [
        mpv,
        f"--external-file={timeline}",
        "--aid=2",
        "--vo=wlshm",
        "--ao=pulse",
        "--autofit=960x540",
        "--force-window=yes",
        str(video),
    ]


def find_program(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"required program not found in PATH: {name}")
    return executable


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Play the current directory's news video with synchronized English narration."
        )
    )
    parser.add_argument(
        "video",
        nargs="?",
        type=Path,
        help="Video file (default: the only video in the current directory).",
    )
    parser.add_argument(
        "--english-volume",
        type=float,
        default=1.0,
        metavar="LEVEL",
        help="English narration volume multiplier (default: 1.0).",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild the cached English timeline.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    if args.english_volume < 0:
        print("[error] --english-volume cannot be negative", file=sys.stderr)
        return 2

    try:
        directory = Path.cwd()
        video, clips = discover_media(directory, args.video)
        ffmpeg = find_program("ffmpeg")
        ffprobe = find_program("ffprobe")
        mpv = find_program("mpv")
        cache_root = Path.home() / ".cache" / "review-news"
        timeline, manifest = cache_paths(directory, cache_root)
        rebuilt = ensure_timeline(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            video=video,
            clips=clips,
            output=timeline,
            manifest=manifest,
            rebuild=args.rebuild,
            english_volume=args.english_volume,
        )
        state = "created" if rebuilt else "cached"
        print(f"[timeline] {state}: {timeline}")
        print(f"[video] {video.name}")
        print(f"[narration] {len(clips)} MP3 files")
        subprocess.run(
            build_mpv_command(
                mpv=mpv,
                video=video,
                timeline=timeline,
            ),
            check=True,
        )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"[error] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

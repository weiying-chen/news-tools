#!/usr/bin/env python3
"""Review a local news video with its timestamped English narration MP3s."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import rename_news


VIDEO_EXTENSIONS = {".mkv", ".mov", ".mp4", ".webm"}
NARRATION_RE = re.compile(r"^\d+_(\d{3,4})\.mp3$", re.IGNORECASE)
CACHE_VERSION = 4


@dataclass(frozen=True)
class NarrationClip:
    path: Path
    start_seconds: int


def prepare_narration_files(directory: Path) -> int:
    untimed = sorted(
        path
        for path in directory.glob("*.mp3")
        if path.is_file() and not NARRATION_RE.fullmatch(path.name)
    )
    if not untimed:
        return 0

    source = directory / "body.txt"
    if not source.is_file():
        raise ValueError(
            f"cannot rename untimed MP3 files without {source.name}: "
            + ", ".join(path.name for path in untimed)
        )

    with contextlib.redirect_stdout(io.StringIO()):
        _, renamed = rename_news.rename_in_story(
            story_dir=directory,
            min_score=0.50,
            apply=True,
            source_txt=source,
        )
    remaining = sorted(
        path.name
        for path in directory.glob("*.mp3")
        if path.is_file() and not NARRATION_RE.fullmatch(path.name)
    )
    if remaining:
        raise ValueError("unconverted MP3 files remain: " + ", ".join(remaining))
    return renamed


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
    return cache_directory / "mixed-timeline.mka", cache_directory / "manifest.json"


def _escape_ffmetadata(value: str) -> str:
    return re.sub(r"([\\;#=])", r"\\\1", value).replace("\n", r"\n")


def write_chapter_file(
    output: Path,
    clips: Sequence[NarrationClip],
    video_duration: float,
) -> None:
    duration_ms = round(video_duration * 1000)
    clips_within_video = [
        clip for clip in clips if clip.start_seconds * 1000 < duration_ms
    ]
    lines = [";FFMETADATA1"]
    for index, clip in enumerate(clips_within_video):
        start_ms = clip.start_seconds * 1000
        end_ms = (
            clips_within_video[index + 1].start_seconds * 1000
            if index + 1 < len(clips_within_video)
            else duration_ms
        )
        lines.extend(
            [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start_ms}",
                f"END={end_ms}",
                f"title={_escape_ffmetadata(clip.path.name)}",
            ]
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_input_config(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "Ctrl+LEFT add chapter -1\nCtrl+RIGHT add chapter 1\n",
        encoding="utf-8",
    )


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
    video: Path,
    video_duration: float,
    clips: Sequence[NarrationClip],
    output: Path,
    manifest: Path,
    rebuild: bool,
    english_volume: float,
) -> bool:
    fingerprint = source_fingerprint(video, clips, english_volume)
    if not rebuild and cache_is_current(output, manifest, fingerprint):
        return False

    clips_within_video = [clip for clip in clips if clip.start_seconds < video_duration]
    if not clips_within_video:
        raise ValueError("all narration timestamps are after the end of the video")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.stem}.tmp{output.suffix}")
    command = build_ffmpeg_command(
        ffmpeg=ffmpeg,
        video=video,
        clips=clips_within_video,
        video_duration=video_duration,
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
    video: Path | str,
    timeline: Path | str,
    chapters: Path | str,
    input_config: Path | str,
    window_title: str,
) -> list[str]:
    return [
        mpv,
        f"--external-file={timeline}",
        f"--chapters-file={chapters}",
        f"--input-conf={input_config}",
        "--aid=2",
        "--profile=fast",
        "--autofit=1280x720",
        "--geometry=50%:50%",
        "--focus-on=all",
        "--no-window-minimized",
        f"--title={window_title}",
        "--force-window=yes",
        str(video),
    ]


WINDOWS_CENTER_SCRIPT = r"""
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ReviewNewsNative {
    public static readonly IntPtr TopMost = new IntPtr(-1);
    public static readonly IntPtr NotTopMost = new IntPtr(-2);
    [StructLayout(LayoutKind.Sequential)]
    public struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindow(string className, string windowName);
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")]
    public static extern bool SystemParametersInfo(
        uint action, uint parameter, out Rect value, uint flags);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")]
    public static extern void SwitchToThisWindow(IntPtr window, bool alternateTab);
    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(
        IntPtr window, IntPtr after, int x, int y, int width, int height,
        uint flags);
}
'@
$title = $args[0]
$window = [IntPtr]::Zero
for ($attempt = 0; $attempt -lt 50; $attempt++) {
    $window = [ReviewNewsNative]::FindWindow($null, $title)
    if ($window -ne [IntPtr]::Zero) { break }
    Start-Sleep -Milliseconds 100
}
if ($window -eq [IntPtr]::Zero) { exit 1 }
[void][ReviewNewsNative]::ShowWindow($window, 9)
$windowRect = New-Object ReviewNewsNative+Rect
$workArea = New-Object ReviewNewsNative+Rect
if (-not [ReviewNewsNative]::GetWindowRect($window, [ref]$windowRect)) { exit 1 }
if (-not [ReviewNewsNative]::SystemParametersInfo(
        48, 0, [ref]$workArea, 0)) { exit 1 }
$width = $windowRect.Right - $windowRect.Left
$height = $windowRect.Bottom - $windowRect.Top
$x = $workArea.Left + [Math]::Max(0, [int](
    (($workArea.Right - $workArea.Left) - $width) / 2))
$y = $workArea.Top + [Math]::Max(0, [int](
    (($workArea.Bottom - $workArea.Top) - $height) / 2))
if (-not [ReviewNewsNative]::SetWindowPos(
        $window, [ReviewNewsNative]::TopMost, $x, $y, 0, 0, 65)) { exit 1 }
if (-not [ReviewNewsNative]::SetWindowPos(
        $window, [ReviewNewsNative]::NotTopMost, 0, 0, 0, 0, 67)) { exit 1 }
[void][ReviewNewsNative]::SetForegroundWindow($window)
[ReviewNewsNative]::SwitchToThisWindow($window, $true)
""".strip()


def build_windows_center_command(
    *,
    powershell: str,
    window_title: str,
) -> list[str]:
    return [
        powershell,
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        WINDOWS_CENTER_SCRIPT,
        window_title,
    ]


def running_in_wsl() -> bool:
    return "microsoft" in platform.release().lower()


def find_windows_mpv() -> str:
    executable = shutil.which("mpv.exe")
    if executable:
        return executable
    installed = Path("/mnt/c/Program Files/MPV Player/mpv.exe")
    if installed.is_file():
        return str(installed)
    raise RuntimeError(
        "Windows mpv not found; install it with: "
        "winget.exe install --id shinchiro.mpv --exact"
    )


def to_windows_path(path: Path) -> str:
    result = subprocess.run(
        ["wslpath", "-w", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def play_video(
    *,
    mpv: str,
    video: Path,
    timeline: Path,
    chapters: Path,
    input_config: Path,
) -> None:
    window_title = f"review-news-{os.getpid()}"
    if running_in_wsl():
        mpv = find_windows_mpv()
        video = to_windows_path(video)
        timeline = to_windows_path(timeline)
        chapters = to_windows_path(chapters)
        input_config = to_windows_path(input_config)
    command = build_mpv_command(
        mpv=mpv,
        video=video,
        timeline=timeline,
        chapters=chapters,
        input_config=input_config,
        window_title=window_title,
    )
    powershell = shutil.which("powershell.exe") if running_in_wsl() else None
    if not powershell:
        subprocess.run(command, check=True)
        return

    player = subprocess.Popen(command)
    try:
        subprocess.run(
            build_windows_center_command(
                powershell=powershell,
                window_title=window_title,
            ),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        return_code = player.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, command)


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
        renamed = prepare_narration_files(directory)
        if renamed:
            print(f"[renamed] {renamed} MP3 files")
        video, clips = discover_media(directory, args.video)
        ffmpeg = find_program("ffmpeg")
        ffprobe = find_program("ffprobe")
        mpv = find_program("mpv")
        cache_root = Path.home() / ".cache" / "review-news"
        timeline, manifest = cache_paths(directory, cache_root)
        chapters = timeline.with_name("chapters.ffmetadata")
        input_config = timeline.with_name("input.conf")
        video_duration = probe_duration(ffprobe, video)
        rebuilt = ensure_timeline(
            ffmpeg=ffmpeg,
            video=video,
            video_duration=video_duration,
            clips=clips,
            output=timeline,
            manifest=manifest,
            rebuild=args.rebuild,
            english_volume=args.english_volume,
        )
        write_chapter_file(chapters, clips, video_duration)
        write_input_config(input_config)
        state = "created" if rebuilt else "cached"
        print(f"[timeline] {state}: {timeline}")
        print(f"[video] {video.name}")
        print(f"[narration] {len(clips)} MP3 files")
        play_video(
            mpv=mpv,
            video=video,
            timeline=timeline,
            chapters=chapters,
            input_config=input_config,
        )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"[error] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Connect to an existing Chrome session for news voiceover downloads."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Sequence
from urllib.request import urlopen


NATURALREADERS_URL_PART = "naturalreaders.com/online"
TIMECODE_RE = re.compile(r"^\d{4}$")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
NODE_VERSION = "v26.13.1"
CONNECTOR_VERSION = "1.13.0"
NODE_ARCHIVE = f"node-{NODE_VERSION}-win-x64.zip"
NODE_BASE_URL = f"https://nodejs.org/dist/{NODE_VERSION}"


@dataclass(frozen=True)
class TimedVoiceover:
    ordinal: int
    timecode: str
    text: str

    @property
    def filename(self) -> str:
        return f"{self.ordinal}_{self.timecode}.mp3"


def extract_timed_voiceovers(body: str) -> list[TimedVoiceover]:
    """Extract English VO only from blocks introduced by four-digit timecodes."""
    lines = body.splitlines()
    voiceovers: list[TimedVoiceover] = []
    index = 0
    while index < len(lines):
        timecode = lines[index].strip()
        if not TIMECODE_RE.fullmatch(timecode):
            index += 1
            continue
        index += 1
        block: list[str] = []
        while index < len(lines) and lines[index].strip():
            block.append(lines[index].strip())
            index += 1

        english: list[str] = []
        for line in block:
            if not english and len(CJK_RE.findall(line)) >= 2:
                continue
            if line.startswith(("/*", "(", "（", "~", ">")):
                continue
            english.append(line)
        if not english:
            raise ValueError(f"timecode {timecode} has no English voiceover text")
        voiceovers.append(
            TimedVoiceover(len(voiceovers) + 1, timecode, "\n".join(english))
        )
    return voiceovers


def control_uid(snapshot: str, accessible_name: str) -> str:
    pattern = re.compile(
        rf'^\s*uid=(\S+)\s+(?:button|link)\s+"{re.escape(accessible_name)}"(?:\s|$)',
        re.MULTILINE,
    )
    matches = pattern.findall(snapshot)
    if len(matches) != 1:
        raise ValueError(
            f"expected one {accessible_name!r} control, found {len(matches)}"
        )
    return matches[0]


def editor_uid(snapshot: str) -> str:
    matches = re.findall(r'^\s*uid=(\S+)\s+generic\s+value=', snapshot, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"expected one text editor, found {len(matches)}")
    return matches[0]


@dataclass(frozen=True)
class ConnectorPaths:
    root_wsl: Path
    root_windows: PureWindowsPath

    @classmethod
    def for_account(cls, drive: str, username: str) -> "ConnectorPaths":
        relative = Path("Users") / username / "AppData" / "Local" / "news-tools-browser"
        return cls(
            Path(f"/mnt/{drive.casefold()}") / relative,
            PureWindowsPath(f"{drive.upper()}:/{relative.as_posix()}"),
        )

    @property
    def node_exe(self) -> Path:
        return self.root_wsl / "node" / "node.exe"

    @property
    def cli_script_wsl(self) -> Path:
        return (
            self.root_wsl
            / "node_modules"
            / "chrome-devtools-mcp"
            / "out"
            / "Default"
            / "gen"
            / "src"
            / "bin"
            / "chrome-devtools.js"
        )

    @property
    def cli_script_windows(self) -> PureWindowsPath:
        return (
            self.root_windows
            / "node_modules"
            / "chrome-devtools-mcp"
            / "out"
            / "Default"
            / "gen"
            / "src"
            / "bin"
            / "chrome-devtools.js"
        )

    @property
    def downloads_wsl(self) -> Path:
        return self.root_wsl.parents[2] / "Downloads"

    @property
    def npm_cli_windows(self) -> PureWindowsPath:
        return self.root_windows / "node" / "node_modules" / "npm" / "bin" / "npm-cli.js"

    def is_prepared(self) -> bool:
        return self.node_exe.is_file() and self.cli_script_wsl.is_file()


def windows_account(path_env: str | None = None) -> tuple[str, str]:
    path_env = os.environ.get("PATH", "") if path_env is None else path_env
    match = re.search(
        r"(?:^|:)/mnt/([a-zA-Z])/Users/([^/:]+)/AppData/Local/"
        r"Microsoft/WindowsApps(?:$|:)",
        path_env,
    )
    if not match:
        raise RuntimeError("could not identify the Windows account from WSL PATH")
    drive, username = match.groups()
    return drive.upper(), username


def connector_paths() -> ConnectorPaths:
    return ConnectorPaths.for_account(*windows_account())


def _download(url: str, destination: Path) -> None:
    with urlopen(url, timeout=60) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)


def _expected_node_digest(shasums: str) -> str:
    for line in shasums.splitlines():
        digest, _, filename = line.partition("  ")
        if filename == NODE_ARCHIVE:
            return digest
    raise RuntimeError(f"Node checksum not found for {NODE_ARCHIVE}")


def prepare_connector(paths: ConnectorPaths) -> None:
    """Install a private Windows Node runtime and Chrome connector only."""
    paths.root_wsl.mkdir(parents=True, exist_ok=True)
    if not paths.node_exe.is_file():
        node_dir = paths.root_wsl / "node"
        if node_dir.exists():
            raise RuntimeError(f"incomplete Node installation: {node_dir}")
        with tempfile.TemporaryDirectory(prefix="download-news-node-") as temp_name:
            temp = Path(temp_name)
            archive = temp / NODE_ARCHIVE
            checksums = temp / "SHASUMS256.txt"
            _download(f"{NODE_BASE_URL}/{NODE_ARCHIVE}", archive)
            _download(f"{NODE_BASE_URL}/SHASUMS256.txt", checksums)
            expected = _expected_node_digest(checksums.read_text(encoding="utf-8"))
            actual = hashlib.sha256(archive.read_bytes()).hexdigest()
            if actual != expected:
                raise RuntimeError("downloaded Node archive failed checksum verification")
            with zipfile.ZipFile(archive) as bundle:
                destination = paths.root_wsl.resolve()
                for member in bundle.infolist():
                    target = (destination / member.filename).resolve()
                    if destination not in target.parents and target != destination:
                        raise RuntimeError("unsafe path in Node archive")
                bundle.extractall(destination)
            extracted = paths.root_wsl / f"node-{NODE_VERSION}-win-x64"
            extracted.rename(node_dir)

    if paths.cli_script_wsl.is_file():
        return
    _run(
        [
            str(paths.node_exe),
            str(paths.npm_cli_windows),
            "install",
            "--prefix",
            str(paths.root_windows),
            "--save-exact",
            f"chrome-devtools-mcp@{CONNECTOR_VERSION}",
        ]
    )


def connector_command(
    paths: ConnectorPaths,
    action: str,
    *extra: str,
) -> list[str]:
    command = [str(paths.node_exe), str(paths.cli_script_windows), action]
    command.extend(extra)
    if action == "list_pages":
        command.append("--output-format=json")
    return command


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )


def start_connector(paths: ConnectorPaths) -> None:
    _run(
        connector_command(
            paths,
            "start",
            "--autoConnect",
            "--usageStatistics=false",
            "--javascriptEvaluation=false",
        )
    )


def stop_connector(paths: ConnectorPaths) -> None:
    _run(connector_command(paths, "stop"))


def _page_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("pages", "result"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    raise ValueError("connector returned an unrecognized page list")


def run_connector(paths: ConnectorPaths) -> list[dict[str, Any]]:
    result = _run(connector_command(paths, "list_pages"))
    return _page_list(json.loads(result.stdout))


def run_cli(paths: ConnectorPaths, action: str, *arguments: str) -> str:
    return _run(connector_command(paths, action, *arguments)).stdout


def select_naturalreader_page(
    pages: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    matches = [
        page
        for page in pages
        if NATURALREADERS_URL_PART in str(page.get("url", "")).casefold()
    ]
    if not matches:
        raise ValueError("no NaturalReader tab is open in the approved Chrome instance")
    if len(matches) != 1:
        raise ValueError(f"found {len(matches)} NaturalReader tabs; leave exactly one open")
    return matches[0]


def _download_state(directory: Path) -> dict[Path, tuple[int, int]]:
    return {
        path: (path.stat().st_mtime_ns, path.stat().st_size)
        for path in directory.glob("*.mp3")
        if path.is_file()
    }


def _wait_for_new_mp3(
    directory: Path,
    before: dict[Path, tuple[int, int]],
    timeout: float = 30,
) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = _download_state(directory)
        changed = [path for path, state in current.items() if before.get(path) != state]
        partials = list(directory.glob("*.crdownload"))
        if len(changed) == 1 and not partials:
            return changed[0]
        if len(changed) > 1:
            raise RuntimeError("multiple new MP3 files appeared in Downloads")
        time.sleep(0.25)
    raise TimeoutError("NaturalReader MP3 did not appear in Downloads")


def move_download(source: Path, destination: Path) -> None:
    try:
        source.replace(destination)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        shutil.move(str(source), str(destination))


def _snapshot(paths: ConnectorPaths, page_id: int) -> str:
    return run_cli(paths, "take_snapshot", str(page_id))


def _click(paths: ConnectorPaths, page_id: int, uid: str) -> None:
    run_cli(paths, "click", str(page_id), uid)


def close_conversion_dialog(
    paths: ConnectorPaths,
    page_id: int,
    snapshot: str,
) -> bool:
    try:
        uid = control_uid(snapshot, "convert cancel button")
    except ValueError:
        return False
    _click(paths, page_id, uid)
    return True


def download_voiceover(
    paths: ConnectorPaths,
    page_id: int,
    voiceover: TimedVoiceover,
    downloads: Path,
    destination: Path,
    conversion_timeout: float = 120,
) -> None:
    """Replace NaturalReader text, convert it, and save one timestamped MP3."""
    snapshot = _snapshot(paths, page_id)
    if close_conversion_dialog(paths, page_id, snapshot):
        snapshot = _snapshot(paths, page_id)
    run_cli(paths, "fill", str(page_id), editor_uid(snapshot), voiceover.text)

    snapshot = _snapshot(paths, page_id)
    _click(paths, page_id, control_uid(snapshot, "settings button [reading page]"))
    snapshot = _snapshot(paths, page_id)
    _click(paths, page_id, control_uid(snapshot, "convert to mp3 button [settings]"))
    snapshot = _snapshot(paths, page_id)
    _click(paths, page_id, control_uid(snapshot, "convert now button"))

    deadline = time.monotonic() + conversion_timeout
    while True:
        snapshot = _snapshot(paths, page_id)
        if "Convert complete" in snapshot:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"conversion timed out for {voiceover.filename}")
        time.sleep(0.5)

    before = _download_state(downloads)
    _click(paths, page_id, control_uid(snapshot, "Download"))
    downloaded = _wait_for_new_mp3(downloads, before)
    if destination.exists():
        raise FileExistsError(destination)
    move_download(downloaded, destination)
    try:
        snapshot = _snapshot(paths, page_id)
        close_conversion_dialog(paths, page_id, snapshot)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Connect to the existing Chrome instance approved through "
            "chrome://inspect/#remote-debugging."
        ),
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Install the private Windows connector without accessing Chrome",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify the unique NaturalReader tab; do not modify it",
    )
    parser.add_argument("--body", type=Path, default=Path("body.txt"))
    parser.add_argument("--output", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        paths = connector_paths()
    except RuntimeError as error:
        print(f"[error] {error}")
        return 1
    if args.prepare:
        try:
            prepare_connector(paths)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print(f"[error] could not prepare browser connector: {error}")
            return 1
        print("[prepared] Windows browser connector; Chrome was not accessed")
        return 0
    if not paths.is_prepared():
        print("[error] browser connector is not prepared; run download-news --prepare")
        return 1

    voiceovers: list[TimedVoiceover] = []
    if not args.check:
        try:
            voiceovers = extract_timed_voiceovers(args.body.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            print(f"[error] could not read voiceovers: {error}")
            return 1
        args.output.mkdir(parents=True, exist_ok=True)

    started = False
    try:
        start_connector(paths)
        started = True
        page = select_naturalreader_page(run_connector(paths))
        if args.check:
            print(f"[connected] NaturalReader page {page.get('id')}")
            return 0
        page_id = int(page["id"])
        for voiceover in voiceovers:
            destination = args.output / voiceover.filename
            if destination.exists():
                print(f"[skip] {voiceover.filename}")
                continue
            print(f"[convert] {voiceover.filename}")
            download_voiceover(
                paths,
                page_id,
                voiceover,
                paths.downloads_wsl,
                destination,
            )
            print(f"[downloaded] {destination}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError) as error:
        print(f"[error] {error}")
        return 1
    finally:
        if started:
            try:
                stop_connector(paths)
            except (OSError, subprocess.SubprocessError):
                pass

    print(f"[done] {len(voiceovers)} voiceover(s) processed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

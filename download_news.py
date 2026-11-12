#!/usr/bin/env python3
"""Open NaturalReader in the Chrome instance used for news downloads."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Sequence
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


NATURALREADERS_URL = "https://www.naturalreaders.com/online/"
WINDOWS_CHROME = Path("/mnt/c/Program Files/Google/Chrome/Application/chrome.exe")
WINDOWS_CURL = Path("/mnt/c/Windows/System32/curl.exe")
DEFAULT_PORT = 9222


def devtools_request(port: int, path: str, method: str = "GET") -> str:
    url = f"http://127.0.0.1:{port}{path}"
    request = Request(url, method=method)
    try:
        with urlopen(request, timeout=2) as response:
            return response.read().decode("utf-8")
    except URLError as wsl_error:
        if not WINDOWS_CURL.is_file():
            raise wsl_error
        command = [str(WINDOWS_CURL), "--fail", "--silent", "--show-error"]
        if method != "GET":
            command.extend(["--request", method])
        command.append(url)
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                errors="replace",
            )
        except subprocess.CalledProcessError as error:
            raise URLError(error.stderr.strip() or str(error)) from error
        return result.stdout


def fetch_tabs(port: int) -> list[dict[str, str]]:
    targets = json.loads(devtools_request(port, "/json/list"))
    return [target for target in targets if target.get("type") == "page"]


def find_naturalreader_tab(
    tabs: Sequence[dict[str, str]],
) -> dict[str, str] | None:
    for tab in tabs:
        if "naturalreaders.com/online" in tab.get("url", "").casefold():
            return tab
    return None


def open_tab(port: int, url: str) -> None:
    devtools_request(port, f"/json/new?{quote(url, safe=':/')}", method="PUT")


def activate_tab(port: int, target_id: str) -> None:
    devtools_request(port, f"/json/activate/{target_id}")


def chrome_command(executable: Path, profile: str, port: int) -> list[str]:
    return [
        str(executable),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        NATURALREADERS_URL,
    ]


def windows_profile_path(path_env: str | None = None) -> str:
    path_env = os.environ.get("PATH", "") if path_env is None else path_env
    match = re.search(
        r"(?:^|:)/mnt/([a-zA-Z])/Users/([^/:]+)/AppData/Local/"
        r"Microsoft/WindowsApps(?:$|:)",
        path_env,
    )
    if match:
        drive, username = match.groups()
        return rf"{drive.upper()}:\Users\{username}\AppData\Local\news-tools-chrome"

    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "echo", r"%LOCALAPPDATA%\news-tools-chrome"],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    return result.stdout.strip().replace("\r", "")


def launch_chrome(port: int) -> None:
    if not WINDOWS_CHROME.is_file():
        raise FileNotFoundError(f"Chrome not found: {WINDOWS_CHROME}")
    subprocess.Popen(
        chrome_command(WINDOWS_CHROME, windows_profile_path(), port),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_chrome(port: int, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fetch_tabs(port)
            return
        except (OSError, URLError):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Chrome did not open debugging port {port}")
            time.sleep(0.25)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open NaturalReader for the news voiceover workflow.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Chrome debugging port identifying the browser instance (default: 9222)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        tabs = fetch_tabs(args.port)
    except (OSError, URLError):
        print(f"[info] opening dedicated Chrome on port {args.port}")
        try:
            launch_chrome(args.port)
            wait_for_chrome(args.port)
        except (FileNotFoundError, subprocess.SubprocessError, TimeoutError) as error:
            print(f"[error] {error}")
            return 1
        print(f"[opened] {NATURALREADERS_URL}")
        return 0

    tab = find_naturalreader_tab(tabs)
    try:
        if tab is None:
            open_tab(args.port, NATURALREADERS_URL)
        else:
            activate_tab(args.port, tab["id"])
    except (OSError, URLError) as error:
        print(f"[error] could not open NaturalReader: {error}")
        return 1
    print(f"[opened] {NATURALREADERS_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

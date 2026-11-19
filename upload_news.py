#!/usr/bin/env python3
"""Upload explicit news files through an approved Google Drive Chrome tab."""

from __future__ import annotations

import argparse
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlsplit

import download_news


SHARED_DRIVES_ROOT_URL = "https://drive.google.com/drive/shared-drives"
NEWS_DRIVE_NAME = "大愛每日英語新聞"
COMPLETED_FOLDER_NAME = "翻譯完成稿"


def named_row_uid(snapshot: str, name: str) -> str:
    matches = re.findall(
        rf'^\s*uid=(\S+)\s+row\s+"{re.escape(name)}(?:\s|\")',
        snapshot,
        re.MULTILINE,
    )
    if len(matches) != 1:
        raise ValueError(f"expected one Drive row named {name!r}, found {len(matches)}")
    return matches[0]


def latest_year_row(snapshot: str) -> tuple[int, str]:
    matches = [
        (int(year), uid)
        for uid, year in re.findall(
            r'^\s*uid=(\S+)\s+row\s+"(\d{4})年(?:\s|\")',
            snapshot,
            re.MULTILINE,
        )
    ]
    if not matches:
        raise ValueError("no year folders found in Drive")
    return max(matches)


def latest_month_row(snapshot: str) -> tuple[int, int, str]:
    matches = [
        (int(year), int(month), uid)
        for uid, year, month in re.findall(
            r'^\s*uid=(\S+)\s+row\s+"(\d{4})\.\s*(\d{1,2})月(?:\s|\")',
            snapshot,
            re.MULTILINE,
        )
    ]
    if not matches:
        raise ValueError("no month folders found in Drive")
    return max(matches)


def accessible_uid(snapshot: str, role: str, names: Sequence[str]) -> str:
    for name in names:
        matches = re.findall(
            rf'^\s*uid=(\S+)\s+{re.escape(role)}\s+"{re.escape(name)}'
            rf'(?:\s[^"\n]*)?"(?:\s|$)',
            snapshot,
            re.MULTILINE,
        )
        if len(matches) == 1:
            return matches[0]
    raise ValueError(f"could not find {role} named one of {list(names)!r}")


def named_drive_item(snapshot: str, name: str) -> tuple[str, bool]:
    try:
        return accessible_uid(snapshot, "link", [name]), False
    except ValueError:
        return named_row_uid(snapshot, name), True


def select_drive_page(
    pages: Sequence[dict[str, object]],
    root_url: str = SHARED_DRIVES_ROOT_URL,
) -> dict[str, object]:
    preferred = [page for page in pages if page.get("url") == root_url]
    if len(preferred) == 1:
        return preferred[0]
    news_pages = [
        page
        for page in pages
        if NEWS_DRIVE_NAME in str(page.get("title", ""))
        and "drive.google.com/drive" in str(page.get("url", ""))
    ]
    if len(news_pages) == 1:
        return news_pages[0]
    drive_pages = [
        page
        for page in pages
        if "drive.google.com/drive" in str(page.get("url", ""))
    ]
    if len(drive_pages) != 1:
        raise ValueError(
            f"expected the supplied Drive folder tab or one Drive tab, found {len(drive_pages)}"
        )
    return drive_pages[0]


def upload_workspaces(targets: Sequence[Path]) -> list[Path]:
    candidates = sorted(
        {path if path.is_dir() else path.parent for path in targets},
        key=lambda path: len(path.parts),
    )
    roots: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
            roots.append(resolved)
    return roots


def start_upload_connector(
    paths: download_news.ConnectorPaths,
    targets: Sequence[Path],
) -> None:
    options = [
        "--autoConnect",
        "--usageStatistics=false",
        "--javascriptEvaluation=false",
    ]
    options.extend(
        f"--workspace={workspace}" for workspace in upload_workspaces(targets)
    )
    download_news._run(download_news.connector_command(paths, "start", *options))


def _snapshot(paths: download_news.ConnectorPaths, page_id: int) -> str:
    return download_news.run_cli(paths, "take_snapshot", str(page_id))


def wait_for_snapshot(
    paths: download_news.ConnectorPaths,
    page_id: int,
    predicate: Callable[[str], bool],
    description: str,
    timeout: float = 20,
) -> str:
    deadline = time.monotonic() + timeout
    while True:
        snapshot = _snapshot(paths, page_id)
        if predicate(snapshot):
            return snapshot
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Drive did not show {description}")
        time.sleep(0.25)


def _open_row(
    paths: download_news.ConnectorPaths,
    page_id: int,
    uid: str,
) -> None:
    download_news.run_cli(paths, "click", str(page_id), uid, "--dblClick=true")


def navigate_to_latest_month(
    paths: download_news.ConnectorPaths,
    page_id: int,
    root_url: str = SHARED_DRIVES_ROOT_URL,
) -> tuple[int, int]:
    download_news.run_cli(
        paths,
        "navigate_page",
        str(page_id),
        "--type=url",
        f"--url={root_url}",
    )
    snapshot = wait_for_snapshot(
        paths,
        page_id,
        lambda value: NEWS_DRIVE_NAME in value,
        NEWS_DRIVE_NAME,
    )
    drive_uid, double_click = named_drive_item(snapshot, NEWS_DRIVE_NAME)
    click_options = ["--dblClick=true"] if double_click else []
    download_news.run_cli(paths, "click", str(page_id), drive_uid, *click_options)

    snapshot = wait_for_snapshot(
        paths,
        page_id,
        lambda value: COMPLETED_FOLDER_NAME in value,
        COMPLETED_FOLDER_NAME,
    )
    _open_row(paths, page_id, named_row_uid(snapshot, COMPLETED_FOLDER_NAME))

    snapshot = wait_for_snapshot(
        paths,
        page_id,
        lambda value: bool(re.search(r'\srow\s+"\d{4}年(?:\s|\")', value)),
        "year folders",
    )
    year, year_uid = latest_year_row(snapshot)
    _open_row(paths, page_id, year_uid)

    snapshot = wait_for_snapshot(
        paths,
        page_id,
        lambda value: bool(re.search(r'\srow\s+"\d{4}\.\s*\d{1,2}月', value)),
        "month folders",
    )
    month_year, month, month_uid = latest_month_row(snapshot)
    if month_year != year:
        raise ValueError(f"latest month belongs to {month_year}, expected {year}")
    _open_row(paths, page_id, month_uid)
    wait_for_snapshot(
        paths,
        page_id,
        lambda value: f"{year}." in value and f"{month}月" in value,
        f"{year}.{month}月",
    )
    return year, month


def _upload_through_menu(
    paths: download_news.ConnectorPaths,
    page_id: int,
    targets: Sequence[Path],
    labels: Sequence[str],
) -> None:
    snapshot = _snapshot(paths, page_id)
    new_uid = accessible_uid(snapshot, "button", ["新增", "New"])
    download_news.run_cli(paths, "click", str(page_id), new_uid)
    snapshot = wait_for_snapshot(
        paths,
        page_id,
        lambda value: any(label in value for label in labels),
        f"the {labels[0]} menu item",
    )
    upload_uid = accessible_uid(snapshot, "menuitem", labels)
    download_news.run_cli(
        paths,
        "upload_file",
        str(page_id),
        upload_uid,
        *(str(path) for path in targets),
    )


def upload_paths(
    paths: download_news.ConnectorPaths,
    page_id: int,
    targets: Sequence[Path],
) -> None:
    files = [path for path in targets if path.is_file()]
    folders = [path for path in targets if path.is_dir()]
    if files:
        _upload_through_menu(
            paths,
            page_id,
            files,
            ["檔案上傳", "File upload"],
        )
    for folder in folders:
        _upload_through_menu(
            paths,
            page_id,
            [folder],
            ["資料夾上傳", "Folder upload"],
        )

    names = [path.name for path in targets]
    wait_for_snapshot(
        paths,
        page_id,
        lambda value: all(name in value for name in names)
        or "上傳完成" in value
        or "upload complete" in value.casefold(),
        "uploaded files",
        timeout=120,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload explicit files or folders to the latest Drive month.",
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument(
        "--root-url",
        default=SHARED_DRIVES_ROOT_URL,
        help="Drive root containing the news folder (default: Shared Drives)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Navigate to and report the destination without uploading",
    )
    return parser


def validate_args(args: argparse.Namespace) -> argparse.Namespace:
    if not args.check and not args.paths:
        raise ValueError("provide at least one file or folder to upload, or use --check")
    root = urlsplit(args.root_url)
    if root.scheme != "https" or root.hostname != "drive.google.com" or not root.path.startswith("/drive/"):
        raise ValueError("--root-url must be a Google Drive URL")
    args.paths = [path.expanduser().resolve() for path in args.paths]
    missing = [path for path in args.paths if not path.exists()]
    if missing:
        raise ValueError(f"path does not exist: {missing[0]}")
    unsupported = [path for path in args.paths if not (path.is_file() or path.is_dir())]
    if unsupported:
        raise ValueError(f"path is not a regular file or folder: {unsupported[0]}")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = validate_args(build_parser().parse_args(argv))
        paths = download_news.connector_paths()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"[error] {error}")
        return 1
    if not paths.is_prepared():
        print("[error] browser connector is not prepared; run download-news --prepare")
        return 1

    started = False
    try:
        start_upload_connector(paths, args.paths)
        started = True
        page = select_drive_page(download_news.run_connector(paths), args.root_url)
        page_id = int(page["id"])
        year, month = navigate_to_latest_month(paths, page_id, args.root_url)
        print(f"[destination] {NEWS_DRIVE_NAME}/{COMPLETED_FOLDER_NAME}/{year}年/{year}.{month}月")
        if args.check:
            return 0
        upload_paths(paths, page_id, args.paths)
        for path in args.paths:
            print(f"[uploaded] {path.name}")
        return 0
    except (
        OSError,
        RuntimeError,
        ValueError,
        TimeoutError,
        subprocess.SubprocessError,
    ) as error:
        print(f"[error] {error}")
        return 1
    finally:
        if started:
            try:
                download_news.stop_connector(paths)
            except (OSError, subprocess.SubprocessError):
                pass


if __name__ == "__main__":
    raise SystemExit(main())

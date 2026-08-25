#!/usr/bin/env python3
"""Build the extracted and validated reference files for the news project."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


DEFAULT_ROOT = Path.home() / "text" / "news"


def pipeline_paths(root: Path) -> dict[str, Path]:
    refs = root / "refs"
    return {
        "refs": refs,
        "manifest": refs / "manifest.json",
        "candidates": refs / "candidates.txt",
        "reference": refs / "reference.json",
        "compact": refs / "compact-text",
        "audit": refs / "validation-audit.json",
        "first_review": refs / "ai-first-pass-reviews.json",
        "second_review": refs / "ai-second-pass-reviews.json",
        "final": refs / "names-titles-orgs.json",
        "extract_candidates": refs / "extract_candidates.py",
        "build_reference": refs / "build_reference.py",
        "extract_compact": refs / "extract_compact_text.py",
        "build_audit": refs / "build_validation_audit.py",
        "build_validated": refs / "build_validated_reference.py",
    }


def discover_source(paths: dict[str, Path]) -> Path:
    manifest = paths["manifest"]
    if not manifest.is_file():
        raise SystemExit("No source was given and refs/manifest.json does not exist; pass --source.")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read source from {manifest}: {exc}") from exc
    source = Path(str(data.get("source", ""))).expanduser()
    if not str(data.get("source", "")).strip():
        raise SystemExit(f"No source path recorded in {manifest}; pass --source.")
    return source


def pipeline_commands(paths: dict[str, Path], source: Path, audit_only: bool) -> list[list[str]]:
    py = sys.executable
    commands = [
        [py, str(paths["extract_candidates"]), str(source), "--output", str(paths["refs"])],
        [py, str(paths["build_reference"]), str(paths["candidates"]), str(paths["reference"])],
        [py, str(paths["extract_compact"]), str(source), str(paths["compact"])],
        [
            py,
            str(paths["build_audit"]),
            str(paths["reference"]),
            str(paths["compact"]),
            str(paths["audit"]),
        ],
    ]
    if not audit_only:
        commands.append(
            [
                py,
                str(paths["build_validated"]),
                str(paths["audit"]),
                str(paths["first_review"]),
                str(paths["second_review"]),
                str(paths["final"]),
            ]
        )
    return commands


def validate_inputs(paths: dict[str, Path], source: Path, audit_only: bool) -> None:
    if not source.is_dir():
        raise SystemExit(f"Missing news source directory: {source}")
    required = [
        "extract_candidates",
        "build_reference",
        "extract_compact",
        "build_audit",
    ]
    if not audit_only:
        required.extend(["build_validated", "first_review", "second_review"])
    missing = [paths[name] for name in required if not paths[name].is_file()]
    if missing:
        raise SystemExit("Missing required news reference files:\n" + "\n".join(str(path) for path in missing))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help=f"news project root (default: {DEFAULT_ROOT})",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="completed-news DOCX archive (defaults to the source in refs/manifest.json)",
    )
    parser.add_argument("--audit-only", action="store_true", help="rebuild extraction and audit but not reviewed final JSON")
    parser.add_argument("--dry-run", action="store_true", help="show pipeline commands without running them")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"Missing news project root: {root}")
    paths = pipeline_paths(root)
    paths["refs"].mkdir(parents=True, exist_ok=True)
    source = args.source.expanduser().resolve() if args.source else discover_source(paths)
    validate_inputs(paths, source, args.audit_only)
    commands = pipeline_commands(paths, source, args.audit_only)

    if args.dry_run:
        for command in commands:
            print(" ".join(command))
        return 0

    for command in commands:
        print(f"[run] {Path(command[1]).name}", flush=True)
        subprocess.run(command, check=True)
    print(f"[ready] {paths['audit' if args.audit_only else 'final']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

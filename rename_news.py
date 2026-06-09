#!/usr/bin/env python3
"""Rename MP3 files from text-based names to VO timecode names.

Expected workflow:
- Each story folder contains one `*_final.docx` (excluding `*_標題職銜_final.docx`).
- The docx includes lines like `1_0016`, `2_0035`, etc.
- MP3 files may be named with sentence snippets (often from English VO text).

The script extracts timecode blocks from docx, fuzzy-matches filename stems to those
blocks, and proposes/executes renames to `<timecode>.mp3`.
"""

from __future__ import annotations

import argparse
import html
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

# Accept whatever appears in script timecodes after "_" (e.g. 3_059, 3_0059).
TIMECODE_RE = re.compile(r"^(\d+_\d+)$")


@dataclass
class Block:
    timecode: str
    lines: list[str]


@dataclass
class MatchCandidate:
    timecode: str
    score: float
    matched_line: str


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()

    # Canonicalize known pronunciation variants seen in filenames/text.
    s = re.sub(r"\btzu\s*chi\b", " tzuchi ", s)
    s = re.sub(r"\bc[iy]\s*ji\b", " tzuchi ", s)
    s = re.sub(r"\bciji\b", " tzuchi ", s)

    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def token_set(s: str) -> set[str]:
    if not s:
        return set()
    return set(s.split())


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def prefix_overlap_tokens(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def has_partial_trailing_prefix(filename_tokens: list[str], line_tokens: list[str]) -> bool:
    if len(filename_tokens) < 2 or len(line_tokens) < len(filename_tokens):
        return False
    if filename_tokens[:-1] != line_tokens[: len(filename_tokens) - 1]:
        return False

    tail = filename_tokens[-1]
    line_tail = line_tokens[len(filename_tokens) - 1]
    if len(tail) < 2:
        return False
    if line_tail.startswith(tail):
        return True

    # Some exported filename snippets get clipped after the first syllable of a
    # known pronunciation variant, e.g. "Founded by Ci" for "Founded by Ci Ji".
    return line_tail == "tzuchi" and tail == "ci"


def parse_docx_paragraphs(docx_path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(docx_path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    except (zipfile.BadZipFile, KeyError):
        return []

    paragraphs = re.findall(r"<w:p[\s\S]*?</w:p>", xml)
    out: list[str] = []
    for p in paragraphs:
        text_parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p)
        if not text_parts:
            continue
        txt = html.unescape("".join(text_parts)).strip()
        if txt:
            out.append(txt)
    return out


def extract_blocks(docx_path: Path) -> list[Block]:
    lines = parse_docx_paragraphs(docx_path)
    blocks: list[Block] = []
    current: Block | None = None

    for raw in lines:
        line = raw.strip()
        m = TIMECODE_RE.match(line)
        if m:
            current = Block(timecode=m.group(1), lines=[])
            blocks.append(current)
            continue
        if current is None:
            continue
        # Skip obvious metadata blocks to reduce noise.
        if line.startswith("/*") or line.startswith("(20") or line.startswith("(13") or line.startswith("(16"):
            continue
        current.lines.append(line)

    # Remove empty blocks.
    return [b for b in blocks if b.lines]


def extract_blocks_from_txt(txt_path: Path) -> list[Block]:
    blocks: list[Block] = []
    current: Block | None = None

    for raw in txt_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = TIMECODE_RE.match(line)
        if m:
            current = Block(timecode=m.group(1), lines=[])
            blocks.append(current)
            continue
        if current is None:
            continue
        # Keep spoken/script lines; skip comment/control markers.
        if line.startswith("/*") or line.endswith("*/"):
            continue
        current.lines.append(line)

    return [b for b in blocks if b.lines]


def score_filename_against_line(filename_norm: str, line_norm: str) -> float:
    if not filename_norm or not line_norm:
        return 0.0

    seq = SequenceMatcher(None, filename_norm, line_norm).ratio()
    jac = jaccard(token_set(filename_norm), token_set(line_norm))

    ft = filename_norm.split()
    lt = line_norm.split()
    pref = prefix_overlap_tokens(ft, lt)
    pref_bonus = min(pref, 5) * 0.03

    # If filename is an exact prefix of line, reward heavily.
    # This is common when mp3 filenames are truncated sentence snippets.
    exact_prefix = line_norm.startswith(filename_norm)
    partial_tail_prefix = has_partial_trailing_prefix(ft, lt)
    prefix_boost = 0.22 if exact_prefix else 0.0
    partial_prefix_boost = 0.16 if partial_tail_prefix else 0.0

    return 0.55 * seq + 0.45 * jac + pref_bonus + prefix_boost + partial_prefix_boost


def best_candidates(stem: str, blocks: list[Block]) -> list[MatchCandidate]:
    stem_norm = normalize_text(stem)
    cands: list[MatchCandidate] = []
    for b in blocks:
        best_score = 0.0
        best_line = ""
        for line in b.lines:
            ln = normalize_text(line)
            score = score_filename_against_line(stem_norm, ln)
            if score > best_score:
                best_score = score
                best_line = line
        cands.append(MatchCandidate(timecode=b.timecode, score=best_score, matched_line=best_line))
    cands.sort(key=lambda c: c.score, reverse=True)
    return cands


def detect_story_docx(story_dir: Path) -> Path | None:
    candidates = sorted(
        p
        for p in story_dir.glob("*_final.docx")
        if "_標題職銜_" not in p.name and not p.name.startswith("~$") and p.is_file()
    )
    return candidates[0] if candidates else None


def detect_story_txt(story_dir: Path, docx: Path) -> Path | None:
    base = docx.stem.removesuffix("_final")
    candidates = [
        story_dir / "body.txt",
        story_dir.parent / "body.txt",
        story_dir / f"{base}.txt",
        story_dir.parent / f"{base}.txt",
    ]
    for p in candidates:
        if p.is_file():
            return p
    txts = sorted(p for p in story_dir.glob("*.txt") if p.is_file())
    return txts[0] if len(txts) == 1 else None


def iter_story_dirs(root: Path) -> Iterable[Path]:
    # Include root itself if it looks like a story dir.
    if detect_story_docx(root):
        yield root
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if detect_story_docx(d):
            yield d


def rename_with_blocks(
    story_dir: Path,
    blocks: list[Block],
    min_score: float,
    apply: bool,
    source_name: str,
) -> tuple[int, int]:
    if not blocks:
        print(f"[skip] {story_dir}: no timecode blocks parsed from {source_name}")
        return (0, 0)

    mp3s = sorted(story_dir.glob("*.mp3"))
    if not mp3s:
        print(f"[skip] {story_dir}: no mp3 files")
        return (0, 0)

    # Only rename files that are not already in timecode format.
    todo = [p for p in mp3s if not TIMECODE_RE.match(p.stem)]
    if not todo:
        print(f"[ok] {story_dir}: all mp3 already timecoded")
        return (0, 0)

    print(f"\n[story] {story_dir}")
    print(f"[source] {source_name} (timecodes source)")
    print(f"[info] parsed {len(blocks)} timecode blocks; {len(todo)} mp3 need rename")

    used_timecodes: set[str] = set()
    planned = 0
    renamed = 0

    for src in todo:
        cands = best_candidates(src.stem, blocks)
        picked = None
        for c in cands:
            if c.timecode in used_timecodes:
                continue
            if c.score < min_score:
                break
            picked = c
            break

        if not picked:
            top = cands[0] if cands else None
            if top:
                print(
                    f"[warn] {src.name}: no confident match (best={top.timecode}, score={top.score:.3f})"
                )
            else:
                print(f"[warn] {src.name}: no candidates")
            continue

        used_timecodes.add(picked.timecode)
        dst = src.with_name(f"{picked.timecode}.mp3")

        if dst.exists() and dst != src:
            print(f"[warn] {src.name}: target exists -> {dst.name}, skipped")
            continue

        planned += 1
        print(
            f"[match] {src.name} -> {dst.name} (score={picked.score:.3f}; line='{picked.matched_line[:90]}')"
        )
        if apply and dst != src:
            src.rename(dst)
            renamed += 1

    return (planned, renamed)


def rename_in_story(
    story_dir: Path,
    min_score: float,
    apply: bool,
    source_txt: Path | None = None,
) -> tuple[int, int]:
    docx = detect_story_docx(story_dir)
    if not docx and not source_txt:
        print(f"[skip] {story_dir}: no *_final.docx found")
        return (0, 0)

    txt = source_txt if source_txt and source_txt.is_file() else (
        detect_story_txt(story_dir, docx) if docx else None
    )
    if txt:
        blocks = extract_blocks_from_txt(txt)
        return rename_with_blocks(
            story_dir=story_dir,
            blocks=blocks,
            min_score=min_score,
            apply=apply,
            source_name=txt.name,
        )

    if not docx:
        print(f"[skip] {story_dir}: no *_final.docx found")
        return (0, 0)
    blocks = extract_blocks(docx)
    return rename_with_blocks(
        story_dir=story_dir,
        blocks=blocks,
        min_score=min_score,
        apply=apply,
        source_name=docx.name,
    )


def rename_from_file(source: Path, min_score: float, apply: bool) -> tuple[int, int]:
    story_dir = source.parent
    ext = source.suffix.lower()

    if ext == ".txt":
        blocks = extract_blocks_from_txt(source)
        return rename_with_blocks(
            story_dir=story_dir,
            blocks=blocks,
            min_score=min_score,
            apply=apply,
            source_name=source.name,
        )

    if ext == ".docx":
        txt = detect_story_txt(story_dir, source)
        if txt:
            blocks = extract_blocks_from_txt(txt)
            source_name = txt.name
        else:
            blocks = extract_blocks(source)
            source_name = source.name
        return rename_with_blocks(
            story_dir=story_dir,
            blocks=blocks,
            min_score=min_score,
            apply=apply,
            source_name=source_name,
        )

    print(f"[error] unsupported file type: {source}")
    return (0, 0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rename mp3 files from text snippet names to timecode names using *_final.docx"
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("."),
        help="Story folder/root folder, or a single .txt/.docx file (default: current directory)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.50,
        help="Minimum fuzzy-match score (default: 0.50)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview matches without renaming files.",
    )
    parser.add_argument(
        "--source-txt",
        type=Path,
        default=None,
        help="Optional explicit txt file to use as timecode source (default for dirs: ./body.txt).",
    )

    args = parser.parse_args()
    root = args.path.expanduser().resolve()

    if not root.exists():
        print(f"[error] path not found: {root}")
        return 2

    apply = not args.dry_run

    if root.is_file():
        if root.suffix.lower() not in {".txt", ".docx"}:
            print("[error] when passing a file, it must be .txt or .docx")
            return 1
        planned, renamed = rename_from_file(root, min_score=args.min_score, apply=apply)
        print(f"\n[summary] planned={planned}, renamed={renamed}")
        if args.dry_run:
            print("[info] run again without --dry-run to execute renames")
        return 0

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[mode] {mode}")
    print(f"[config] min_score={args.min_score}")

    total_planned = 0
    total_renamed = 0
    source_txt = args.source_txt.expanduser().resolve() if args.source_txt else None
    if source_txt is None and root.is_dir():
        default_source_txt = (root / "body.txt").resolve()
        if default_source_txt.is_file():
            source_txt = default_source_txt
            print(f"[source] {source_txt}")
    if source_txt and not source_txt.is_file():
        print(f"[error] source txt not found: {source_txt}")
        return 2

    if source_txt:
        # When an explicit txt source is provided, operate only on the given path.
        stories = [root]
    else:
        stories = list(iter_story_dirs(root))
        if not stories:
            print("[error] no story folders with *_final.docx found (and no body.txt in target dir)")
            return 1

    for story in stories:
        planned, renamed = rename_in_story(
            story,
            min_score=args.min_score,
            apply=apply,
            source_txt=source_txt,
        )
        total_planned += planned
        total_renamed += renamed

    print(f"\n[summary] planned={total_planned}, renamed={total_renamed}")
    if args.dry_run:
        print("[info] run again without --dry-run to execute renames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

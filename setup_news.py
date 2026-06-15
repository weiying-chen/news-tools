#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
R_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
R_NS_MAP = {"r": R_NS}

# Strip a leading date-like parenthesis block, e.g.:
# "(0312)Title.docx" -> "Title.docx"
# "（2026-03-12）Title.docx" -> "Title.docx"
LEADING_DATE_PAREN_RE = re.compile(
    r"^\s*[（(]\s*[\d]{2,8}(?:[-/.][\d]{1,2}){0,2}\s*[）)]\s*"
)
LEADING_DATE_PLAIN_RE = re.compile(
    r"^\s*[\d]{4,8}(?:[-/.][\d]{1,2}){0,2}\s*(?=[\u4e00-\u9fffA-Za-z_])"
)
TRAILING_DATE_PAREN_RE = re.compile(
    r"\s*[（(]\s*[\d]{1,8}(?:[-/.][\d]{1,2}){0,2}\s*[）)]\s*$"
)
EN_NAME_HINT_RE = re.compile(
    r'^[\s\d.,，．。:：;；!?！？~\-–—秒分]*'
    r'([A-Za-zÀ-ÖØ-öø-ÿĀ-žḀ-ỹ][A-Za-zÀ-ÖØ-öø-ÿĀ-žḀ-ỹ.\s"“”\'‘’\-]*[A-Za-zÀ-ÖØ-öø-ÿĀ-žḀ-ỹ])'
    r'(?:\s*[\u4e00-\u9fff].*)?$'
)
EN_NAME_VALUE_RE = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿĀ-žḀ-ỹ][A-Za-zÀ-ÖØ-öø-ÿĀ-žḀ-ỹ.\s\"'“”‘’\-]*[A-Za-zÀ-ÖØ-öø-ÿĀ-žḀ-ỹ.]"
)
SOUND_BITE_PREFIX_RE = re.compile(r"^\s*SB\s*[:：\-]?\s*")
CUE_PAREN_PREFIX_RE = re.compile(r"^\s*[（(][^（）()]*[）)]\s*(.+?)\s*$")
EN_PHRASE_RE = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿĀ-žḀ-ỹ][A-Za-zÀ-ÖØ-öø-ÿĀ-žḀ-ỹ.'’‘\-]*"
    r"(?:\s+[A-Za-zÀ-ÖØ-öø-ÿĀ-žḀ-ỹ][A-Za-zÀ-ÖØ-öø-ÿĀ-žḀ-ỹ.'’‘\-]*)*"
)
NAME_TITLE_RE = re.compile(r"^(?:Mr|Ms|Mrs|Miss|Dr|Prof)\.?\b", re.IGNORECASE)
ORG_HINT_RE = re.compile(
    r"\b(?:School|University|College|Institute|Campus|Hospital|Foundation)\b",
    re.IGNORECASE,
)
ROLE_PHRASE_RE = re.compile(
    r"\b(?:Director|Department|Region|Province|Chairman|President|Senator|Minister|Mayor|Governor|Executive|Officer)\b",
    re.IGNORECASE,
)
NON_NAME_CUE_TOKENS = {
    "OS",
    "VO",
    "SOT",
    "NAT",
    "NATSOT",
    "SB",
    "CG",
    "PKG",
}
URL_RE = re.compile(r"https?://[^\s<>()\"']+")


def strip_sound_bite_prefix(text: str) -> str:
    return SOUND_BITE_PREFIX_RE.sub("", text, count=1)


def pick_best_english_phrase(text: str) -> str:
    phrases = [p.strip().rstrip(" .,;:-") for p in EN_PHRASE_RE.findall(text)]
    phrases = [p for p in phrases if p]
    if not phrases:
        return ""

    def score(phrase: str) -> tuple[int, int]:
        words = len(phrase.split())
        rank = 0
        if NAME_TITLE_RE.match(phrase):
            rank += 3
        if words >= 2:
            rank += 2
        if ORG_HINT_RE.search(phrase):
            rank -= 2
        return (rank, words)

    return max(phrases, key=score)


def extract_name_from_cue_segment(text: str) -> str:
    cue = strip_sound_bite_prefix(text).strip()
    if not cue:
        return ""

    # Heuristic 0: cues often look like "<name>, <role/title>".
    if "," in cue:
        left = cue.split(",", 1)[0].strip()
        if left:
            best_left = pick_best_english_phrase(left)
            if looks_like_english_name(best_left):
                return best_left

    # Heuristic 1: if cue has double spaces, tail often carries the person's name.
    if re.search(r"\s{2,}", cue):
        tail = re.split(r"\s{2,}", cue)[-1].strip()
        if tail:
            best_tail = pick_best_english_phrase(tail)
            if looks_like_english_name(best_tail):
                return best_tail

    # Heuristic 2: if Chinese role text appears, name is often after it.
    if re.search(r"[\u4e00-\u9fff]", cue):
        tail_after_cjk = re.split(r"[\u4e00-\u9fff]+", cue)[-1].strip()
        if tail_after_cjk:
            best_tail = pick_best_english_phrase(tail_after_cjk)
            if looks_like_english_name(best_tail):
                return best_tail

    best = pick_best_english_phrase(cue)
    if looks_like_english_name(best):
        return best
    return ""


def extract_docx_paragraphs(docx_path: Path) -> list[str]:
    with zipfile.ZipFile(docx_path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)

    paragraphs: list[str] = []
    for para in root.findall(".//w:p", NS):
        texts = [t.text or "" for t in para.findall(".//w:t", NS)]
        text = "".join(texts)
        if text.strip():
            paragraphs.append(text.strip())
        else:
            paragraphs.append("")
    return paragraphs


def extract_docx_hyperlink_urls(docx_path: Path) -> list[str]:
    with zipfile.ZipFile(docx_path) as zf:
        try:
            rels_xml = zf.read("word/_rels/document.xml.rels")
        except KeyError:
            return []

    root = ET.fromstring(rels_xml)
    urls: list[str] = []
    for rel in root.findall(".//r:Relationship", R_NS_MAP):
        target = (rel.get("Target") or "").strip()
        target_mode = (rel.get("TargetMode") or "").strip()
        if target_mode.lower() != "external":
            continue
        if target.lower().startswith(("http://", "https://")):
            urls.append(target)
    return urls


def body_lines_after_marker(lines: list[str], marker: str = "<") -> list[str]:
    start_idx = 0
    for idx, line in enumerate(lines):
        if line.strip() == marker:
            start_idx = idx + 1
            break

    body = lines[start_idx:]
    while body and not body[0].strip():
        body = body[1:]
    return body


def normalize_filename(name: str) -> str:
    stem = Path(name).stem
    suffix = Path(name).suffix
    cleaned_stem = LEADING_DATE_PAREN_RE.sub("", stem).strip()
    cleaned_stem = LEADING_DATE_PLAIN_RE.sub("", cleaned_stem).strip()
    cleaned_stem = TRAILING_DATE_PAREN_RE.sub("", cleaned_stem).strip()
    if not cleaned_stem:
        return name
    return f"{cleaned_stem}{suffix}"


def find_first_url_in_lines(lines: list[str]) -> str:
    for line in lines:
        match = URL_RE.search(line)
        if not match:
            continue
        return match.group(0).rstrip(".,;:!?)）]}")
    return ""


def copy_to_clipboard(text: str, copy_cmd: str = "wl-copy") -> bool:
    try:
        subprocess.run([copy_cmd], input=(text + "\n").encode("utf-8"), check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def extract_english_name_hint(text: str) -> str:
    def finalize_name(name: str) -> str:
        cleaned = (
            name.replace("“", '"')
            .replace("”", '"')
            .replace("‘", "'")
            .replace("’", "'")
            .strip()
        )
        return cleaned if looks_like_english_name(cleaned) else ""

    stripped = text.strip()
    if not stripped:
        return ""

    # Support lines that start with one/more parenthesized cues, even if text follows.
    # Example: "(17．Gayansa嘉彥薩)紫衣"
    leading_chunks_match = re.match(r"^\s*(?:[（(][^（）()]*[）)]\s*)+", stripped)
    if leading_chunks_match:
        for chunk in re.findall(r"[（(]([^（）()]*)[）)]", leading_chunks_match.group(0)):
            candidate = extract_name_from_cue_segment(chunk.strip())
            if candidate.isupper() and len(candidate) <= 3:
                continue
            if looks_like_english_name(candidate):
                return finalize_name(candidate)

    if stripped[0] in {"(", "（"} and stripped[-1] in {")", "）"}:
        # Prefer chunk parsing first; it handles composite cues and mixed CJK/English better.
        chunks = re.findall(r"[（(]([^（）()]*)[）)]", stripped)
        for chunk in chunks:
            candidate = extract_name_from_cue_segment(chunk.strip())
            if candidate.isupper() and len(candidate) <= 3:
                continue
            if looks_like_english_name(candidate):
                return finalize_name(candidate)

        inner = strip_sound_bite_prefix(stripped[1:-1].strip())
        match = EN_NAME_HINT_RE.match(inner)
        if match:
            name = extract_name_from_cue_segment(match.group(1).strip())
            if not name:
                name = match.group(1).strip().rstrip(" .,;:-")
            return finalize_name(name)

    # Support cues like "(13) Rabina" where name follows a parenthesized timing.
    prefix_match = CUE_PAREN_PREFIX_RE.match(stripped)
    if prefix_match:
        name = extract_name_from_cue_segment(prefix_match.group(1).strip())
        if not name:
            name = prefix_match.group(1).strip().rstrip(" .,;:-")
        return finalize_name(name)
    return ""


def looks_like_english_name(text: str) -> bool:
    candidate = text.strip()
    if not candidate:
        return False
    if " of " in candidate.lower() and ROLE_PHRASE_RE.search(candidate):
        return False
    compact_upper = re.sub(r"[^A-Za-z]", "", candidate).upper()
    if compact_upper in NON_NAME_CUE_TOKENS:
        return False
    # Reject short all-caps cue codes like "OS", "VO", etc.
    if compact_upper and compact_upper == candidate.replace(" ", "").upper():
        if len(compact_upper) <= 3 and " " not in candidate:
            return False
    return bool(EN_NAME_VALUE_RE.fullmatch(candidate))


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def next_non_empty_line(lines: list[str], idx: int) -> str:
    for i in range(idx + 1, len(lines)):
        s = lines[i].strip()
        if s:
            return s
    return ""


def detect_people_entries(lines: list[str]) -> list[dict[str, str]]:
    seen: set[str] = set()
    entries: list[dict[str, str]] = []
    pending_english_names: list[str] = []
    in_super = False
    consumed_super_header = False

    for idx, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue

        english_name = extract_english_name_hint(s)
        if english_name:
            pending_english_names.append(english_name)
            continue
        if looks_like_english_name(s) and next_non_empty_line(lines, idx) == "/*SUPER:":
            pending_english_names.append(s.strip())
            continue

        if s == "/*SUPER:":
            in_super = True
            consumed_super_header = False
            continue

        if not in_super:
            continue

        if s.startswith("*/"):
            in_super = False
            consumed_super_header = False
            continue

        if consumed_super_header:
            continue

        label = s.replace("│", "｜")
        if label.endswith("//"):
            label = label[:-2].rstrip()
        label = label.rstrip("｜|:：").rstrip()
        if not label:
            consumed_super_header = True
            continue
        if "｜" in label or "|" in label:
            if "｜" in label:
                left, right = [part.strip() for part in label.split("｜", 1)]
            else:
                left, right = [part.strip() for part in label.split("|", 1)]
            if looks_like_english_name(right) and not has_cjk(left):
                consumed_super_header = True
                continue
        if label in seen:
            # Duplicate label blocks (same person repeated later) should not leak
            # their nearest cue into the next distinct SUPER entry.
            if pending_english_names:
                pending_english_names.pop()
            consumed_super_header = True
            continue

        seen.add(label)
        # Use the most recent cue nearest to this SUPER block.
        right_name = ""
        if "｜" in label:
            _, right = [part.strip() for part in label.split("｜", 1)]
            if looks_like_english_name(right):
                right_name = right
        elif looks_like_english_name(label):
            right_name = label

        if pending_english_names and right_name:
            pending_english_names.pop()
            name_en = right_name
        else:
            name_en = pending_english_names.pop() if pending_english_names else ""
        if not name_en:
            name_en = right_name
        entries.append({"label": label, "name_en": name_en.strip()})
        consumed_super_header = True
    return entries


def render_body_txt(lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def render_meta_txt(lines: list[str]) -> str:
    people_entries = detect_people_entries(lines)
    out: list[str] = [
        "TITLE:",
        "",
        "OVERVIEW:",
        "",
    ]
    if people_entries:
        out.append("PEOPLE:")
        out.append("")
        for idx, entry in enumerate(people_entries):
            label = entry["label"].strip()
            name_en = entry["name_en"].strip()
            rendered_label = label

            if "｜" in label or "|" in label:
                sep = "｜" if "｜" in label else "|"
                left, right = [part.strip() for part in label.split(sep, 1)]
                if looks_like_english_name(right) and has_cjk(left):
                    rendered_label = left
                    if not name_en:
                        name_en = right

            out.append(rendered_label)
            if name_en:
                out.append(name_en)
            if idx < len(people_entries) - 1:
                out.append("")
    return "\n".join(out).rstrip() + "\n"


def safe_write(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.write_text(content, encoding="utf-8")


def move_or_copy(src: Path, dst: Path, keep_original: bool) -> None:
    if src.resolve() == dst.resolve():
        return
    if keep_original:
        shutil.copy2(src, dst)
    else:
        shutil.move(src, dst)


def run(args: argparse.Namespace) -> int:
    input_value = args.input
    if not input_value:
        cwd_docx = sorted(Path.cwd().glob("*.docx"))
        if len(cwd_docx) != 1:
            print(
                f"[error] expected exactly one .docx in current directory, found {len(cwd_docx)}",
                file=sys.stderr,
            )
            print(
                "[info] pass input explicitly: setup-news /path/to/file.docx",
                file=sys.stderr,
            )
            return 1
        input_path = cwd_docx[0].resolve()
    else:
        input_path = Path(input_value).expanduser().resolve()
    if not input_path.exists():
        print(f"[error] input file not found: {input_path}", file=sys.stderr)
        return 1
    if input_path.suffix.lower() != ".docx":
        print(f"[error] input must be a .docx file: {input_path}", file=sys.stderr)
        return 1

    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    normalized_name = normalize_filename(input_path.name)
    normalized_input_path = input_path
    if normalized_name != input_path.name:
        candidate = input_path.with_name(normalized_name)
        if candidate.exists() and candidate != input_path:
            # If normalized target already exists, reuse it instead of failing.
            normalized_input_path = candidate
        else:
            input_path.rename(candidate)
            normalized_input_path = candidate

    target_docx = workspace / normalized_name
    same_target = (
        target_docx.exists()
        and normalized_input_path.exists()
        and target_docx.resolve() == normalized_input_path.resolve()
    )
    if target_docx.exists() and not args.force and not same_target:
        print(f"[error] target docx already exists: {target_docx}", file=sys.stderr)
        return 1

    move_or_copy(normalized_input_path, target_docx, keep_original=args.keep_original)

    lines = body_lines_after_marker(extract_docx_paragraphs(target_docx))
    first_url = find_first_url_in_lines(lines)
    if not first_url:
        docx_urls = extract_docx_hyperlink_urls(target_docx)
        first_url = docx_urls[0] if docx_urls else ""
    if first_url and copy_to_clipboard(first_url):
        print(f"[copied] {first_url}")
    elif first_url:
        print("[warn] URL found but failed to copy with wl-copy", file=sys.stderr)
    body_txt = workspace / "body.txt"
    meta_txt = workspace / "meta.txt"
    safe_write(body_txt, render_body_txt(lines), force=args.force)
    safe_write(meta_txt, render_meta_txt(lines), force=args.force)

    print(f"[created] {target_docx.name}")
    print(f"[created] {body_txt.name}")
    print(f"[created] {meta_txt.name}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare news intake files in workspace root: normalize filename, place DOCX in "
            "workspace, and generate body.txt/meta.txt from DOCX content."
        )
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="",
        help="Path to the input .docx file. If omitted, use the only .docx in cwd.",
    )
    parser.add_argument(
        "--workspace",
        default="~/text/news",
        help="Workspace root where case folder is created (default: ~/text/news)",
    )
    parser.add_argument(
        "--keep-original",
        action="store_true",
        help="Copy instead of move the input file into the case folder",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing target/body/meta files if present",
    )
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()

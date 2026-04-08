#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOH'
Usage:
  gen-news [source_docx]

Generate news outputs in the current folder using:
  - body.txt
  - meta.txt
  - source docx (argument, or the only .docx in current directory)

Outputs:
  ./<base_stem>/<base_stem>_final.docx
  ./<base_stem>/<base_stem>_標題職銜_final.docx

Also moves .mp3 files from current folder into ./<base_stem>/.
EOH
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

WORD_DIR="/home/weiying/python/word"
PY="$WORD_DIR/.venv/bin/python"
NEWS_PY="$WORD_DIR/generate_news.py"
META_PY="$WORD_DIR/generate_meta.py"

if [[ ! -x "$PY" ]]; then
  echo "[error] missing python venv at $PY" >&2
  exit 1
fi

source_docx="${1:-}"
if [[ -z "$source_docx" ]]; then
  mapfile -t docx_files < <(find . -maxdepth 1 -type f -name '*.docx' | sort)
  if [[ "${#docx_files[@]}" -ne 1 ]]; then
    echo "[error] expected exactly one .docx in current folder, found ${#docx_files[@]}" >&2
    echo "[info] pass source docx explicitly: gen-news /path/to/source.docx" >&2
    exit 1
  fi
  source_docx="${docx_files[0]}"
fi

if [[ ! -f "$source_docx" ]]; then
  echo "[error] source docx not found: $source_docx" >&2
  exit 1
fi

if [[ ! -f body.txt ]]; then
  echo "[error] body.txt not found in current directory" >&2
  exit 1
fi

if [[ ! -f meta.txt ]]; then
  echo "[error] meta.txt not found in current directory" >&2
  exit 1
fi

source_name="$(basename "$source_docx")"
source_stem="${source_name%.*}"

if [[ "$source_stem" == *_final ]]; then
  base_stem="${source_stem%_final}"
else
  base_stem="$source_stem"
fi

target_dir="./${base_stem}"
mkdir -p "$target_dir"

news_out="${target_dir}/${base_stem}_final.docx"
meta_out="${target_dir}/${base_stem}_標題職銜_final.docx"

"$PY" "$NEWS_PY" \
  --source-txt body.txt \
  --source-docx "$source_docx" \
  --output "$news_out"

"$PY" "$META_PY" \
  --source-txt body.txt \
  --meta-txt meta.txt \
  --source-docx "$source_docx" \
  --output "$meta_out"

# Move MP3 assets from current folder into the story output folder.
shopt -s nullglob
moved_count=0
moved_names=()
for mp3 in ./*.mp3 ./*.mp3:Zone.Identifier; do
  mv -f "$mp3" "$target_dir/"
  moved_count=$((moved_count + 1))
  moved_names+=("$(basename "$mp3")")
done

echo "[created] $(basename "$news_out")"
echo "[created] $(basename "$meta_out")"
if (( moved_count == 0 )); then
  echo "[moved] 0 files"
else
  for name in "${moved_names[@]}"; do
    echo "[moved] $name"
  done
fi

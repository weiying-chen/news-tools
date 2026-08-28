# news-tools

News generation and reference tooling.

## Setup

Create the project-local Python environment and install the tools:

```bash
cd /home/weiying/python/news-tools
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --editable .
```

Faster-Whisper is installed inside `.venv`. Downloaded Whisper models remain in
the shared Hugging Face cache, so other projects do not need duplicate model
copies.

`setup-news` automatically detects VO passages, aligns them against the
downloaded video, and adds their timestamps to `body.txt` after all alignments
pass validation.

## Reference generator

Rebuild the extracted, audited, and validated news references:

```bash
python3 /home/weiying/python/news-tools/ref_news.py
```

The completed-news archive defaults to the source recorded in
`~/text/news/refs/manifest.json`. Pass `--source` to override it, `--root` to
use another news project, `--dry-run` to show the pipeline without running it,
or `--audit-only` to stop before applying the saved review decisions.

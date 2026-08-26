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

## VO timestamps

Detect VO passages, align them directly against a downloaded video, and write a
timestamped copy without changing the source body:

```bash
.venv/bin/timestamp-vo ~/text/news/body.txt ~/text/news/video.mp4
```

The default output is `body_timestamped_sample.txt` beside the source body.

## Reference generator

Rebuild the extracted, audited, and validated news references:

```bash
python3 /home/weiying/python/news-tools/ref_news.py
```

The completed-news archive defaults to the source recorded in
`~/text/news/refs/manifest.json`. Pass `--source` to override it, `--root` to
use another news project, `--dry-run` to show the pipeline without running it,
or `--audit-only` to stop before applying the saved review decisions.

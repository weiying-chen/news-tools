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

The installed `.venv/bin/setup-news` command automatically detects VO passages, aligns them against the
downloaded video, and adds their timestamps to `body.txt` after all alignments
pass validation. Low-confidence VO matches are rechecked in short audio windows
with the medium Faster-Whisper model, and are replaced only when the alignment
materially improves. The same Faster-Whisper transcript is reused to add a missing
duration below a SUPER block when exactly one interview occurs between two VO
passages. Existing cue durations are preserved, and ambiguous intervals are
left unchanged with a warning.

If YouTube blocks the automatic download, place one manually downloaded video in
the workspace root before running setup-news. It is detected automatically, the
YouTube download is skipped, and timestamp generation continues normally:

```bash
cp /path/to/downloaded-video.mp4 ~/text/news/
setup-news /path/to/source.docx
```

After a failed setup already created the workspace files, place the video in the
workspace and rerun with `--force` before manually editing `body.txt` or `meta.txt`.
Use `--video` only when the workspace contains multiple videos and one must be
selected explicitly.

## Download voiceovers

Prepare the private Windows connector without accessing any running Chrome:

```bash
download-news --prepare
```

Before downloading, leave exactly one NaturalReader tab open in the desired
logged-in Chrome instance and enable Remote Debugging at
`chrome://inspect/#remote-debugging`. From the directory containing `body.txt`,
run:

```bash
download-news
```

Chrome asks for permission before the command can access the browser. The
command extracts only English VO blocks introduced by standalone four-digit
timecodes, converts them through NaturalReader, and saves them as ordinal and
timecode names such as `1_0021.mp3`. Existing destination MP3s are skipped. The
command refuses zero or multiple matching NaturalReader tabs and does not
launch another Chrome profile. Use `download-news --check` to verify the
connection without changing the page or consuming conversion quota.

## Reference generator

Rebuild the extracted, audited, and validated news references:

```bash
python3 /home/weiying/python/news-tools/ref_news.py
```

The completed-news archive defaults to the source recorded in
`~/text/news/refs/manifest.json`. Pass `--source` to override it, `--root` to
use another news project, `--dry-run` to show the pipeline without running it,
or `--audit-only` to stop before applying the saved review decisions.

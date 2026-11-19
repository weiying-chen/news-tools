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

Prepare the browser connector without accessing any running Chrome:

```bash
download-news --prepare
```

On native Linux this installs the connector under
`~/.local/share/news-tools-browser` using Node and npm from `PATH`, and watches
`~/Downloads` for completed MP3 files. Under WSL it continues to use the
private Windows Node connector and the Windows Downloads folder.

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

## Upload completed news

With Google Drive open in the approved Chrome instance,
verify navigation to the latest year and month without uploading:

```bash
upload-news --check
```

The Shared Drives page is the default starting point. To test the identical
folder hierarchy in personal My Drive, override only the root:

```bash
upload-news --root-url "https://drive.google.com/drive/u/0/my-drive" --check
upload-news --root-url "https://drive.google.com/drive/u/0/my-drive" ./upload-test
```

Upload only explicitly named files or folders:

```bash
upload-news main.docx meta.docx
upload-news ./completed-story
```

The command navigates from the selected root to
`大愛每日英語新聞/翻譯完成稿`, selects the
numerically latest year and month folders, and verifies that Drive reports the
upload. Folder arguments use Drive's `Folder upload` action and preserve their
contents. It uses the existing logged-in Chrome session and does not use Drive
API credentials.

## Reference generator

Rebuild the extracted, audited, and validated news references:

```bash
python3 /home/weiying/python/news-tools/ref_news.py
```

The completed-news archive defaults to the source recorded in
`~/text/news/refs/manifest.json`. Pass `--source` to override it, `--root` to
use another news project, `--dry-run` to show the pipeline without running it,
or `--audit-only` to stop before applying the saved review decisions.

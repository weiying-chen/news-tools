# news-tools

News generation and reference tooling.

## Reference generator

Rebuild the extracted, audited, and validated news references:

```bash
python3 /home/weiying/python/news-tools/ref_news.py
```

The completed-news archive defaults to the source recorded in
`~/text/news/refs/manifest.json`. Pass `--source` to override it, `--root` to
use another news project, `--dry-run` to show the pipeline without running it,
or `--audit-only` to stop before applying the saved review decisions.

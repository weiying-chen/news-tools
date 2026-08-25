from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import ref_news


class RefNewsTests(unittest.TestCase):
    def test_default_root_is_news_folder(self):
        self.assertEqual(ref_news.DEFAULT_ROOT, Path.home() / "text" / "news")

    def test_pipeline_paths_are_kept_under_refs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = ref_news.pipeline_paths(root)
            self.assertEqual(paths["refs"], root / "refs")
            self.assertEqual(paths["compact"], root / "refs" / "compact-text")
            self.assertEqual(paths["final"], root / "refs" / "names-titles-orgs.json")


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path


GEN_NEWS_PATH = Path(__file__).resolve().parents[1] / 'gen_news.sh'


class GenNewsTest(unittest.TestCase):
    def test_generation_copies_mp3s_without_renaming_them(self) -> None:
        script = GEN_NEWS_PATH.read_text(encoding='utf-8')

        self.assertNotIn('rename_news.py', script)
        self.assertNotIn('[renamed]', script)


if __name__ == '__main__':
    unittest.main()

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path('/home/weiying/python/news-tools/gen_news.sh')


class GenNewsScriptTest(unittest.TestCase):
    def test_runs_mp3_rename_after_copy_to_story_folder(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix='news_tools_gen_test_'))
        story_dir = temp_dir / 'case'
        story_dir.mkdir(parents=True, exist_ok=True)

        (story_dir / 'body.txt').write_text('1_0001\nLine one\n', encoding='utf-8')
        (story_dir / 'meta.txt').write_text('TITLE:\n', encoding='utf-8')
        (story_dir / 'sample.docx').write_text('dummy', encoding='utf-8')
        (story_dir / 'hello world.mp3').write_bytes(b'fake-mp3')

        fake_news = temp_dir / 'fake_generate_news.py'
        fake_news.write_text(
            '#!/usr/bin/env python3\n'
            'import sys\n'
            'out = sys.argv[sys.argv.index("--output") + 1]\n'
            'open(out, "wb").close()\n',
            encoding='utf-8',
        )
        fake_news.chmod(fake_news.stat().st_mode | stat.S_IXUSR)

        fake_meta = temp_dir / 'fake_generate_meta.py'
        fake_meta.write_text(
            '#!/usr/bin/env python3\n'
            'import sys\n'
            'out = sys.argv[sys.argv.index("--output") + 1]\n'
            'open(out, "wb").close()\n',
            encoding='utf-8',
        )
        fake_meta.chmod(fake_meta.stat().st_mode | stat.S_IXUSR)

        fake_rename = temp_dir / 'fake_rename_news_mp3.py'
        fake_rename.write_text(
            '#!/usr/bin/env python3\n'
            'from pathlib import Path\n'
            'import sys\n'
            'target = Path(sys.argv[1])\n'
            'src = target / "hello world.mp3"\n'
            'dst = target / "1_0001.mp3"\n'
            'if src.exists():\n'
            '    src.rename(dst)\n'
            'print("[match] hello world.mp3 -> 1_0001.mp3 (score=0.900; line=\'Line one\')")\n'
            'print("[summary] planned=1, renamed=1")\n',
            encoding='utf-8',
        )
        fake_rename.chmod(fake_rename.stat().st_mode | stat.S_IXUSR)

        env = dict(os.environ)
        env.update(
            {
                'GEN_NEWS_PYTHON': '/usr/bin/python3',
                'GEN_NEWS_NEWS_SCRIPT': str(fake_news),
                'GEN_NEWS_META_SCRIPT': str(fake_meta),
                'GEN_NEWS_RENAME_SCRIPT': str(fake_rename),
            }
        )
        result = subprocess.run(
            [str(SCRIPT_PATH), 'sample.docx'],
            cwd=story_dir,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

        out_story = story_dir / 'sample'
        self.assertTrue((out_story / 'sample_final.docx').exists())
        self.assertTrue((out_story / 'sample_標題職銜_final.docx').exists())
        self.assertTrue((out_story / '1_0001.mp3').exists())
        self.assertIn('[copied] hello world.mp3', result.stdout)
        self.assertIn('[renamed] hello world.mp3 -> 1_0001.mp3', result.stdout)
        self.assertIn('[renamed] 1 files', result.stdout)


if __name__ == '__main__':
    unittest.main()

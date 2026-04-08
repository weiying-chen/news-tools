import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SETUP_MODULE_PATH = Path('/home/weiying/python/news-tools/setup_news.py')
RENAME_MODULE_PATH = Path('/home/weiying/python/news-tools/rename_news_mp3.py')


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup_module = load_module('setup_news', SETUP_MODULE_PATH)
rename_module = load_module('rename_news_mp3', RENAME_MODULE_PATH)


class SetupNewsUnitTest(unittest.TestCase):
    def _make_docx(self, xml: str, tmp: Path) -> Path:
        docx_path = tmp / 'sample.docx'
        with zipfile.ZipFile(docx_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('word/document.xml', xml)
        return docx_path

    def test_normalize_filename_strips_leading_date_parentheses(self) -> None:
        self.assertEqual(
            setup_module.normalize_filename('（2026-03-12）Today Story.docx'),
            'Today Story.docx',
        )
        self.assertEqual(
            setup_module.normalize_filename('(0312)Another Story.docx'),
            'Another Story.docx',
        )

    def test_body_lines_after_marker(self) -> None:
        lines = ['header', '<', '', 'line1', 'line2']
        self.assertEqual(setup_module.body_lines_after_marker(lines), ['line1', 'line2'])

    def test_detect_people_entries_uses_hint_and_super_label(self) -> None:
        lines = [
            '(SB) (Anabel) (17秒)',
            '/*SUPER:',
            '主持人｜阿明 //',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(entries, [{'label': '主持人｜阿明', 'name_en': 'Anabel'}])

    def test_render_meta_txt_contains_people_block(self) -> None:
        lines = [
            '(Alice)',
            '/*SUPER:',
            'Reporter｜Alice',
            '*/',
        ]
        content = setup_module.render_meta_txt(lines)
        self.assertIn('PEOPLE:\n\nReporter｜Alice\nAlice\n', content)

    def test_setup_news_cli_creates_body_and_meta(self) -> None:
        with tempfile.TemporaryDirectory(prefix='news_tools_test_') as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / 'workspace'
            xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Header line</w:t></w:r></w:p>
    <w:p><w:r><w:t>&lt;</w:t></w:r></w:p>
    <w:p><w:r><w:t>(Anabel)</w:t></w:r></w:p>
    <w:p><w:r><w:t>/*SUPER:</w:t></w:r></w:p>
    <w:p><w:r><w:t>主持人｜阿明 //</w:t></w:r></w:p>
    <w:p><w:r><w:t>*/</w:t></w:r></w:p>
    <w:p><w:r><w:t>Body line one</w:t></w:r></w:p>
  </w:body>
</w:document>
'''
            docx_path = self._make_docx(xml, tmp_path)
            env = dict(os.environ)
            env['PYTHONDONTWRITEBYTECODE'] = '1'
            result = subprocess.run(
                [
                    'python3',
                    str(SETUP_MODULE_PATH),
                    str(docx_path),
                    '--workspace',
                    str(workspace),
                    '--keep-original',
                    '--force',
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn('[created]', result.stdout)
            self.assertTrue((workspace / 'sample.docx').exists())
            body_txt = (workspace / 'body.txt').read_text(encoding='utf-8')
            meta_txt = (workspace / 'meta.txt').read_text(encoding='utf-8')
            self.assertIn('Body line one\n', body_txt)
            self.assertIn('主持人｜阿明\nAnabel\n', meta_txt)


class RenameNewsMp3UnitTest(unittest.TestCase):
    def test_extract_blocks_from_txt(self) -> None:
        with tempfile.TemporaryDirectory(prefix='news_tools_test_') as tmp:
            txt_path = Path(tmp) / 'body.txt'
            txt_path.write_text(
                '1_0016\nThis is line one.\n/* comment */\n2_0035\nSecond block line.\n',
                encoding='utf-8',
            )
            blocks = rename_module.extract_blocks_from_txt(txt_path)
            self.assertEqual(len(blocks), 2)
            self.assertEqual(blocks[0].timecode, '1_0016')
            self.assertEqual(blocks[0].lines, ['This is line one.'])
            self.assertEqual(blocks[1].timecode, '2_0035')

    def test_best_candidates_prefers_matching_block(self) -> None:
        blocks = [
            rename_module.Block('1_0016', ['This is line one']),
            rename_module.Block('2_0035', ['Another sentence here']),
        ]
        cands = rename_module.best_candidates('this is line', blocks)
        self.assertGreater(len(cands), 0)
        self.assertEqual(cands[0].timecode, '1_0016')

    def test_rename_with_blocks_apply(self) -> None:
        with tempfile.TemporaryDirectory(prefix='news_tools_test_') as tmp:
            story = Path(tmp)
            src = story / 'this is line one.mp3'
            src.write_bytes(b'fake mp3')

            blocks = [rename_module.Block('1_0016', ['This is line one'])]
            planned, renamed = rename_module.rename_with_blocks(
                story_dir=story,
                blocks=blocks,
                min_score=0.2,
                apply=True,
                source_name='body.txt',
            )
            self.assertEqual(planned, 1)
            self.assertEqual(renamed, 1)
            self.assertFalse(src.exists())
            self.assertTrue((story / '1_0016.mp3').exists())

    def test_rename_news_mp3_cli_apply(self) -> None:
        with tempfile.TemporaryDirectory(prefix='news_tools_test_') as tmp:
            story = Path(tmp) / 'story'
            story.mkdir(parents=True, exist_ok=True)
            (story / 'body.txt').write_text(
                '1_0016\nThis is line one.\n',
                encoding='utf-8',
            )
            (story / 'this is line one.mp3').write_bytes(b'fake mp3')

            env = dict(os.environ)
            env['PYTHONDONTWRITEBYTECODE'] = '1'
            result = subprocess.run(
                [
                    'python3',
                    str(RENAME_MODULE_PATH),
                    str(story),
                    '--source-txt',
                    str(story / 'body.txt'),
                    '--min-score',
                    '0.2',
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn('[summary] planned=1, renamed=1', result.stdout)
            self.assertTrue((story / '1_0016.mp3').exists())


if __name__ == '__main__':
    unittest.main()

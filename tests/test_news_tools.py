import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SETUP_MODULE_PATH = Path('/home/weiying/python/news-tools/setup_news.py')
RENAME_MODULE_PATH = Path('/home/weiying/python/news-tools/rename_news_mp3.py')
GEN_NEWS_PATH = Path('/home/weiying/python/news-tools/gen_news.sh')


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

    def _make_docx_with_rels(self, xml: str, rels_xml: str, tmp: Path) -> Path:
        docx_path = tmp / 'sample.docx'
        with zipfile.ZipFile(docx_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('word/document.xml', xml)
            zf.writestr('word/_rels/document.xml.rels', rels_xml)
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
        self.assertEqual(
            setup_module.normalize_filename('0521西班牙浴佛_final.docx'),
            '西班牙浴佛_final.docx',
        )
        self.assertEqual(
            setup_module.normalize_filename('證嚴上人開示(5.31).docx'),
            '證嚴上人開示.docx',
        )

    def test_body_lines_after_marker(self) -> None:
        lines = ['header', '<', '', 'line1', 'line2']
        self.assertEqual(setup_module.body_lines_after_marker(lines), ['line1', 'line2'])

    def test_find_first_url_in_lines_detects_standalone_or_embedded(self) -> None:
        self.assertEqual(
            setup_module.find_first_url_in_lines(
                ['note', 'https://example.com/news/123', 'tail']
            ),
            'https://example.com/news/123',
        )
        self.assertEqual(
            setup_module.find_first_url_in_lines(
                ['Intro', 'Please see https://example.org/story?id=9 for context.']
            ),
            'https://example.org/story?id=9',
        )

    def test_extract_docx_hyperlink_urls(self) -> None:
        with tempfile.TemporaryDirectory(prefix='news_tools_test_') as tmp:
            tmp_path = Path(tmp)
            xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:body>
    <w:p><w:hyperlink r:id="rId6"><w:r><w:t>DaAi link</w:t></w:r></w:hyperlink></w:p>
  </w:body>
</w:document>
'''
            rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://www.daai.tv/news/abroad/595486" TargetMode="External"/>
</Relationships>
'''
            docx_path = self._make_docx_with_rels(xml, rels, tmp_path)
            self.assertEqual(
                setup_module.extract_docx_hyperlink_urls(docx_path),
                ['https://www.daai.tv/news/abroad/595486'],
            )

    def test_detect_people_entries_uses_hint_and_super_label(self) -> None:
        lines = [
            '(SB) (Anabel) (17秒)',
            '/*SUPER:',
            '主持人｜阿明 //',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(entries, [{'label': '主持人｜阿明', 'name_en': 'Anabel'}])

    def test_detect_people_entries_ignores_os_and_keeps_sb_name(self) -> None:
        lines = [
            '(Jessica Su)(中文)',
            '/*SUPER:',
            '慈濟志工｜蘇美娟//',
            '*/',
            '/*SUBT:',
            '提娃那│//',
            '希望教室課輔助學 發現教育黑數',
            '*/',
            '(OS)',
            '(SB Jazmin Ramirez)(15秒)',
            '/*SUPER:',
            '提娃那希望教室職工｜賈茲敏//',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(
            entries,
            [
                {'label': '慈濟志工｜蘇美娟', 'name_en': 'Jessica Su'},
                {'label': '提娃那希望教室職工｜賈茲敏', 'name_en': 'Jazmin Ramirez'},
            ],
        )

    def test_detect_people_entries_ignores_subt_entries(self) -> None:
        lines = [
            '(Jessica Su)(中文)',
            '/*SUPER:',
            '慈濟志工｜蘇美娟//',
            '*/',
            '/*SUBT:',
            '提娃那│//',
            '希望教室課輔助學 發現教育黑數',
            '*/',
            '(OS)',
            '(SB Jazmin Ramirez)(15秒)',
            '/*SUPER:',
            '提娃那希望教室職工｜賈茲敏//',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(
            entries,
            [
                {'label': '慈濟志工｜蘇美娟', 'name_en': 'Jessica Su'},
                {'label': '提娃那希望教室職工｜賈茲敏', 'name_en': 'Jazmin Ramirez'},
            ],
        )

    def test_detect_people_entries_prefers_name_before_comma_role(self) -> None:
        lines = [
            '(11．Abdullah , SL TzuChi scholar)',
            '/*SUPER:',
            '慈青營學員｜阿卜杜拉//',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(
            entries,
            [{'label': '慈青營學員｜阿卜杜拉', 'name_en': 'Abdullah'}],
        )

    def test_detect_people_entries_reads_name_from_leading_parentheses(self) -> None:
        lines = [
            '(17．Gayansa嘉彥薩)紫衣',
            '/*SUPER:',
            '慈青營學員｜嘉彥薩//',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(
            entries,
            [{'label': '慈青營學員｜嘉彥薩', 'name_en': 'Gayansa'}],
        )

    def test_detect_people_entries_does_not_leak_previous_name_to_master_label(self) -> None:
        lines = [
            '(26" Dr.Voltaire Guadalupe)',
            '(Director of Department of Health in CALABARZON Region)',
            '/*SUPER:',
            '衛生部卡拉巴區區長｜Dr.Voltaire Guadalupe//',
            '*/',
            '/*SUPER:',
            '證嚴上人開示(5.31)｜//',
            '很感恩',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(
            entries,
            [
                {
                    'label': '衛生部卡拉巴區區長｜Dr.Voltaire Guadalupe',
                    'name_en': 'Dr.Voltaire Guadalupe',
                },
                {
                    'label': '證嚴上人開示(5.31)',
                    'name_en': '',
                },
            ],
        )

    def test_detect_people_entries_prefers_full_name_over_single_word(self) -> None:
        lines = [
            '(14秒 Pamela；Mancela Aulesu)',
            '/*SUPER:',
            '帕梅拉的母親｜瑪麗塞拉//',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(
            entries,
            [{'label': '帕梅拉的母親｜瑪麗塞拉', 'name_en': 'Mancela Aulesu'}],
        )

    def test_detect_people_entries_keeps_curly_apostrophe_name(self) -> None:
        lines = [
            '(SB Ahmad Al-Za’anin)(18)',
            '/*SUPER:',
            '努拉兒子｜艾哈邁德//',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(
            entries,
            [{'label': '努拉兒子｜艾哈邁德', 'name_en': "Ahmad Al-Za'anin"}],
        )

    def test_detect_people_entries_discards_pending_name_for_duplicate_label(self) -> None:
        lines = [
            '(SB Ivan)(11)',
            '/*SUPER:',
            '提娃那衛局社會服務醫生｜伊凡//',
            '*/',
            '(SB Sandra)(19秒)',
            '/*SUPER:',
            '家長｜珊卓拉//',
            '*/',
            '(SB Sandra)(11)',
            '/*SUPER:',
            '家長｜珊卓拉//',
            '*/',
            '/*SUPER:',
            '大林慈院口腔顎面外科主任｜劉書呈//',
            '*/',
            '(SB Liliana)(12秒)',
            '/*SUPER:',
            '家長｜莉莉安娜//',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(
            entries,
            [
                {'label': '提娃那衛局社會服務醫生｜伊凡', 'name_en': 'Ivan'},
                {'label': '家長｜珊卓拉', 'name_en': 'Sandra'},
                {'label': '大林慈院口腔顎面外科主任｜劉書呈', 'name_en': ''},
                {'label': '家長｜莉莉安娜', 'name_en': 'Liliana'},
            ],
        )

    def test_detect_people_entries_strips_trailing_separator_when_name_empty(self) -> None:
        lines = [
            '/*SUPER:',
            '患者｜//',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(entries, [{'label': '患者', 'name_en': ''}])

    def test_detect_people_entries_skips_label_with_english_right_side(self) -> None:
        lines = [
            '/*SUPER:',
            'Tzu Chi volunteer doctor｜Liao Kuan Hsuan//',
            '*/',
            '/*SUPER:',
            '提娃那衛生局牙科醫師｜Maria//',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(entries, [{'label': '提娃那衛生局牙科醫師｜Maria', 'name_en': 'Maria'}])

    def test_detect_people_entries_keeps_cjk_left_with_english_right(self) -> None:
        lines = [
            '/*SUPER:',
            '藍毘尼佛教慈濟學校人文教師｜Vivek//',
            '*/',
            '/*SUPER:',
            '藍毘尼佛教慈濟學校校長｜Kriti//',
            '*/',
        ]
        entries = setup_module.detect_people_entries(lines)
        self.assertEqual(
            entries,
            [
                {'label': '藍毘尼佛教慈濟學校人文教師｜Vivek', 'name_en': 'Vivek'},
                {'label': '藍毘尼佛教慈濟學校校長｜Kriti', 'name_en': 'Kriti'},
            ],
        )

    def test_render_meta_txt_contains_people_block(self) -> None:
        lines = [
            '(Alice)',
            '/*SUPER:',
            '主持人｜阿明',
            '*/',
        ]
        content = setup_module.render_meta_txt(lines)
        self.assertIn('PEOPLE:\n\n主持人｜阿明\nAlice\n', content)

    def test_render_meta_txt_splits_english_name_from_label(self) -> None:
        lines = [
            '/*SUPER:',
            '衛生部卡拉巴區區長｜Dr. Voltaire Guadalupe',
            '*/',
        ]
        content = setup_module.render_meta_txt(lines)
        self.assertIn('PEOPLE:\n\n衛生部卡拉巴區區長\nDr. Voltaire Guadalupe\n', content)

    def test_render_meta_txt_keeps_chinese_name_in_label(self) -> None:
        lines = [
            '/*SUPER:',
            '主持人｜阿明',
            '*/',
        ]
        content = setup_module.render_meta_txt(lines)
        self.assertIn('PEOPLE:\n\n主持人｜阿明\n', content)

    def test_render_meta_txt_omits_people_when_no_entries(self) -> None:
        lines = ['No super block here']
        content = setup_module.render_meta_txt(lines)
        self.assertNotIn('PEOPLE:', content)

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

    def test_run_copies_first_url_to_clipboard(self) -> None:
        with tempfile.TemporaryDirectory(prefix='news_tools_test_') as tmp:
            tmp_path = Path(tmp)
            docx_path = tmp_path / 'sample.docx'
            docx_path.write_bytes(b'fake')
            workspace = tmp_path / 'workspace'
            args = type(
                'Args',
                (),
                {
                    'input': str(docx_path),
                    'workspace': str(workspace),
                    'keep_original': True,
                    'force': True,
                },
            )()

            with mock.patch.object(
                setup_module, 'extract_docx_paragraphs', return_value=['<', 'See https://example.com/a']
            ), mock.patch.object(setup_module, 'move_or_copy'), mock.patch.object(
                setup_module, 'safe_write'
            ), mock.patch.object(setup_module.subprocess, 'run') as run_mock:
                stdout_buffer = io.StringIO()
                with redirect_stdout(stdout_buffer):
                    rc = setup_module.run(args)

            self.assertEqual(rc, 0)
            run_mock.assert_called_once_with(
                ['wl-copy'],
                input=b'https://example.com/a\n',
                check=True,
            )
            self.assertIn('[copied] https://example.com/a', stdout_buffer.getvalue())


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

    def test_score_filename_allows_partial_trailing_token_prefix(self) -> None:
        score = rename_module.score_filename_against_line(
            rename_module.normalize_text('Children eagerly wave at the camera. Founded by Ci'),
            rename_module.normalize_text(
                'Children eagerly wave at the camera. Founded by Tzu Chi for underprivileged children, '
                'this center in San Agustín Acasaguastlán, Guatemala, has served the community for 23 years, '
                'but many of its facilities are now worn out.'
            ),
        )
        self.assertGreaterEqual(score, 0.5)

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


class GenNewsScriptUnitTest(unittest.TestCase):
    def test_gen_news_does_not_copy_zone_identifier_stream_files(self) -> None:
        script = GEN_NEWS_PATH.read_text(encoding='utf-8')
        self.assertNotIn('.mp3:Zone.Identifier', script)


if __name__ == '__main__':
    unittest.main()

import importlib.util
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


RENAME_MODULE_PATH = Path(__file__).resolve().parents[1] / 'rename_news.py'


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rename_module = load_module('rename_news', RENAME_MODULE_PATH)


class RenameNewsBlocksTest(unittest.TestCase):
    def test_timecode_validation_finds_missing_and_extra_mp3s(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            directory = Path(tmp_dir)
            (directory / '1_0016.mp3').touch()
            (directory / '3_0100.mp3').touch()
            blocks = [
                rename_module.Block('1_0016', ['First line']),
                rename_module.Block('2_0035', ['Second line']),
            ]

            missing, extra = rename_module.find_timecode_mismatches(directory, blocks)

        self.assertEqual(missing, ['2_0035'])
        self.assertEqual(extra, ['3_0100.mp3'])

    def test_rename_warns_about_missing_and_extra_timecodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            directory = Path(tmp_dir)
            (directory / '1_0016.mp3').touch()
            (directory / '3_0100.mp3').touch()
            blocks = [
                rename_module.Block('1_0016', ['First line']),
                rename_module.Block('2_0035', ['Second line']),
            ]
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                rename_module.rename_with_blocks(
                    story_dir=directory,
                    blocks=blocks,
                    min_score=0.5,
                    apply=True,
                    source_name='body.txt',
                )

        self.assertIn('[warn] missing MP3 for timecode: 2_0035', output.getvalue())
        self.assertIn('[warn] MP3 has no body timecode: 3_0100.mp3', output.getvalue())

    def test_one_letter_tail_is_truncated_after_long_matching_prefix(self) -> None:
        filename_tokens = 'volunteers bring winter supplies across the region c'.split()
        line_tokens = (
            'volunteers bring winter supplies across the region communities '
            'receive blankets and warm clothing'
        ).split()

        self.assertTrue(
            rename_module.has_partial_trailing_prefix(
                filename_tokens,
                line_tokens,
            )
        )

    def test_one_letter_c_matches_normalized_tzu_chi_after_long_prefix(self) -> None:
        filename_tokens = 'cold weather continues across south america c'.split()
        line_tokens = 'cold weather continues across south america tzuchi volunteers'.split()

        self.assertTrue(
            rename_module.has_partial_trailing_prefix(
                filename_tokens,
                line_tokens,
            )
        )

    def test_prefixed_mp3_name_is_not_accepted_as_script_timecode(self) -> None:
        self.assertEqual(rename_module.parse_timecode('1_0016', 1), '')

    def test_bare_timecodes_receive_ordered_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / 'body.txt'
            source.write_text(
                '\n'.join(
                    [
                        '0016',
                        'First spoken line',
                        '0035',
                        'Second spoken line',
                    ]
                ),
                encoding='utf-8',
            )

            blocks = rename_module.extract_blocks_from_txt(source)

        self.assertEqual(
            [(block.timecode, block.lines) for block in blocks],
            [
                ('1_0016', ['First spoken line']),
                ('2_0035', ['Second spoken line']),
            ],
        )


if __name__ == '__main__':
    unittest.main()

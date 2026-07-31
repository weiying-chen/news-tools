import importlib.util
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

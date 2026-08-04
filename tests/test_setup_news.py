import importlib.util
import sys
import unittest
from pathlib import Path


SETUP_MODULE_PATH = Path(__file__).resolve().parents[1] / 'setup_news.py'


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup_module = load_module('setup_news', SETUP_MODULE_PATH)


class SetupNewsPeopleTest(unittest.TestCase):
    def test_multiple_named_labels_in_one_super_block_are_included(self) -> None:
        lines = [
            '/*SUPER:',
            '拉羅馬那慈濟小學前校長｜瑪麗亞//',
            '感謝慈濟//',
            '(NS 3秒)',
            '大愛電視節目資深企畫｜吳志怡//',
            '在畢業典禮現場//',
            '*/',
        ]

        self.assertEqual(
            setup_module.detect_people_entries(lines),
            [
                {
                    'label': '拉羅馬那慈濟小學前校長｜瑪麗亞',
                    'name_en': '',
                },
                {
                    'label': '大愛電視節目資深企畫｜吳志怡',
                    'name_en': '',
                },
            ],
        )

    def test_reports_super_labels_missing_english_names(self) -> None:
        lines = [
            '(12 seconds Maria)',
            '/*SUPER:',
            '畢業生｜瑪麗亞//',
            '*/',
            '/*SUPER:',
            '慈濟志工｜陳大明//',
            '*/',
        ]

        self.assertEqual(
            setup_module.find_super_labels_missing_english_names(lines),
            ['慈濟志工｜陳大明'],
        )

    def test_daai_html_youtube_id_becomes_canonical_watch_url(self) -> None:
        html = r'''
            var newsJson = '{\"NewsID\":111111,\"YTID\":\"Rhs8Q5uRQsA\"}';
            var newsJson = '{\"NewsID\":597547,\"YTID\":\"tCL86SwAlFI\"}';
        '''

        self.assertEqual(
            setup_module.extract_youtube_url_from_daai_html(html, news_id='597547'),
            'https://www.youtube.com/watch?v=tCL86SwAlFI',
        )

    def test_youtube_download_command_targets_workspace(self) -> None:
        command = setup_module.build_youtube_download_command(
            'https://www.youtube.com/watch?v=tCL86SwAlFI',
            Path('/work/news'),
        )

        self.assertEqual(
            command,
            [
                'yt-dlp',
                '--no-playlist',
                '--paths',
                '/work/news',
                'https://www.youtube.com/watch?v=tCL86SwAlFI',
            ],
        )

    def test_parenthesized_name_with_extra_spaces_keeps_full_name(self) -> None:
        lines = [
            '(Helena  Hung)',
            '/*SUPER:',
            '慈濟墨西哥負責人｜洪良岱//',
            '*/',
        ]

        self.assertEqual(
            setup_module.detect_people_entries(lines),
            [
                {
                    'label': '慈濟墨西哥負責人｜洪良岱',
                    'name_en': 'Helena Hung',
                }
            ],
        )

    def test_number_before_parenthesized_name_is_used_for_next_super(self) -> None:
        lines = [
            '30(Rocio)',
            '/*SUPER:',
            '慈濟志工｜羅西歐//',
            '我能理解哥哥//',
            '*/',
        ]

        self.assertEqual(
            setup_module.detect_people_entries(lines),
            [
                {
                    'label': '慈濟志工｜羅西歐',
                    'name_en': 'Rocio',
                }
            ],
        )

    def test_cjk_label_english_right_overrides_location_cue(self) -> None:
        lines = [
            '(15" Machache)(秒數更正)',
            '/*SUPER:',
            '馬查奇居民｜Malehlohonolo Tsabeng//',
            '因為是冬天 沒東西吃//',
            '*/',
        ]

        self.assertEqual(
            setup_module.detect_people_entries(lines),
            [
                {
                    'label': '馬查奇居民｜Malehlohonolo Tsabeng',
                    'name_en': 'Malehlohonolo Tsabeng',
                }
            ],
        )

    def test_cue_prefers_english_name_before_cjk_location(self) -> None:
        lines = [
            '(Chiu Shu Yu 邱慈甄，梅洛市Merlo)',
            '/*SUPER:',
            '慈濟阿根廷聯絡處負責人│邱淑玉//',
            '*/',
        ]

        self.assertEqual(
            setup_module.detect_people_entries(lines),
            [
                {
                    'label': '慈濟阿根廷聯絡處負責人｜邱淑玉',
                    'name_en': 'Chiu Shu Yu',
                }
            ],
        )

    def test_short_mixed_case_english_name_on_label_right_is_kept(self) -> None:
        lines = [
            '(8)',
            '/*SUPER:',
            '守護天使食堂負責人｜Eva//',
            '我想感謝基金會//',
            '*/',
        ]

        self.assertEqual(
            setup_module.render_meta_txt(lines),
            "\n".join(
                [
                    "TITLE:",
                    "",
                    "OVERVIEW:",
                    "",
                    "PEOPLE:",
                    "",
                    "守護天使食堂負責人",
                    "Eva",
                    "",
                ]
            ),
        )

    def test_cue_extracts_name_before_english_role_title(self) -> None:
        lines = [
            '(SB  Freeman Su Executive Director of NE Region )(7秒)',
            '/*SUPER:',
            '慈濟紐約分會執行長｜蘇濟義//',
            '*/',
        ]

        self.assertEqual(
            setup_module.detect_people_entries(lines),
            [
                {
                    'label': '慈濟紐約分會執行長｜蘇濟義',
                    'name_en': 'Freeman Su',
                }
            ],
        )

    def test_multispace_cjk_role_english_name_label_is_split(self) -> None:
        lines = [
            '(15，伊曼紐)',
            '/*SUPER:',
            '慈濟志工    Emmanule//',
            '在這一區 我們有屬於//',
            '*/',
        ]

        self.assertEqual(
            setup_module.render_meta_txt(lines),
            "\n".join(
                [
                    "TITLE:",
                    "",
                    "OVERVIEW:",
                    "",
                    "PEOPLE:",
                    "",
                    "慈濟志工",
                    "Emmanule",
                    "",
                ]
            ),
        )


if __name__ == '__main__':
    unittest.main()

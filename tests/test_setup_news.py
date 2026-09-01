import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


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

    def test_anonymous_super_role_does_not_require_english_name(self) -> None:
        lines = [
            '/*SUPER:',
            '家長//',
            '感謝大家的幫助//',
            '*/',
        ]

        self.assertEqual(
            setup_module.find_super_labels_missing_english_names(lines),
            [],
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

    def test_daai_popup_news_youtube_id_is_extracted(self) -> None:
        html = r'''
            var newsJson = '{\"NewsID\":597848,\"YTID\":\"0Qkh8jlG8_4\"}';
            var popupNews = '{\"NewsID\":597791,\"YTID\":\"ujuoJF5IKfk\"}';
        '''

        self.assertEqual(
            setup_module.extract_youtube_url_from_daai_html(html, news_id='597791'),
            'https://www.youtube.com/watch?v=ujuoJF5IKfk',
        )

    def test_youtube_download_command_targets_workspace(self) -> None:
        with mock.patch.object(
            setup_module.Path,
            'home',
            return_value=Path('/home/tester'),
        ):
            command = setup_module.build_youtube_download_command(
                'https://www.youtube.com/watch?v=tCL86SwAlFI',
                Path('/work/news'),
            )

        self.assertEqual(
            command,
            [
                'yt-dlp',
                '--no-playlist',
                '--js-runtimes',
                'node',
                '--extractor-args',
                'youtubepot-bgutilscript:script_path=/home/tester/.local/share/bgutil-ytdlp-pot-provider/server/build/generate_once.js',
                '--extractor-args',
                'youtube:player_client=mweb',
                '--paths',
                '/work/news',
                'https://www.youtube.com/watch?v=tCL86SwAlFI',
            ],
        )

    def test_youtube_download_retries_with_combined_format(self) -> None:
        failure = setup_module.subprocess.CalledProcessError(1, ['yt-dlp'])

        with (
            mock.patch.object(setup_module.shutil, 'which', return_value='/usr/bin/yt-dlp'),
            mock.patch.object(
                setup_module.tempfile,
                'mkdtemp',
                return_value='/work/news/.setup-news-download-123',
            ),
            mock.patch.object(setup_module.shutil, 'rmtree'),
            mock.patch.object(
                setup_module,
                'find_downloaded_youtube_video',
                return_value=Path('/work/news/video.mp4'),
            ),
            mock.patch.object(
                setup_module.subprocess,
                'run',
                side_effect=[failure, mock.DEFAULT],
            ) as run,
        ):
            setup_module.download_youtube_video(
                'https://www.youtube.com/watch?v=tCL86SwAlFI',
                Path('/work/news'),
            )

        self.assertEqual(run.call_count, 2)
        first_command = run.call_args_list[0].args[0]
        fallback_command = run.call_args_list[1].args[0]
        self.assertNotIn('--format', first_command)
        self.assertIn('temp:/work/news/.setup-news-download-123', first_command)
        self.assertEqual(
            fallback_command[fallback_command.index('--format') + 1],
            '18',
        )

    def test_youtube_download_cleans_private_temp_directory_after_failure(self) -> None:
        failure = setup_module.subprocess.CalledProcessError(1, ['yt-dlp'])

        with (
            mock.patch.object(setup_module.shutil, 'which', return_value='/usr/bin/yt-dlp'),
            mock.patch.object(
                setup_module.tempfile,
                'mkdtemp',
                return_value='/work/news/.setup-news-download-123',
            ),
            mock.patch.object(setup_module.subprocess, 'run', side_effect=failure),
            mock.patch.object(setup_module.shutil, 'rmtree') as rmtree,
        ):
            with self.assertRaises(setup_module.subprocess.CalledProcessError):
                setup_module.download_youtube_video(
                    'https://www.youtube.com/watch?v=tCL86SwAlFI',
                    Path('/work/news'),
                )

        rmtree.assert_called_once_with(
            Path('/work/news/.setup-news-download-123'),
            ignore_errors=True,
        )

    def test_downloaded_video_automatically_timestamps_body(self) -> None:
        workspace = Path('/work/news')
        body = workspace / 'body.txt'
        video = workspace / 'story.mp4'

        with (
            mock.patch.object(
                setup_module,
                'download_youtube_video',
                return_value=video,
            ) as download,
            mock.patch.object(
                setup_module.timestamp_vo,
                'timestamp_body',
            ) as timestamp,
        ):
            setup_module.download_and_timestamp_video(
                'https://www.youtube.com/watch?v=tCL86SwAlFI',
                workspace,
                body,
            )

        download.assert_called_once_with(
            'https://www.youtube.com/watch?v=tCL86SwAlFI',
            workspace,
        )
        timestamp.assert_called_once_with(body, video)

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
        self.assertEqual(setup_module.render_body_txt(lines), "\n".join(lines) + "\n")

    def test_fullwidth_latin_initial_in_cue_is_normalized(self) -> None:
        lines = [
            '(SB Ｍidori)(7秒)',
            '/*SUPER:',
            '捐血民眾｜米多里//',
            '*/',
        ]

        self.assertEqual(
            setup_module.detect_people_entries(lines),
            [
                {
                    'label': '捐血民眾｜米多里',
                    'name_en': 'Midori',
                }
            ],
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

    def test_multispace_cjk_role_and_name_are_normalized_in_meta(self) -> None:
        lines = [
            '(11秒)',
            '/*SUPER:',
            '大愛村居民    賽爾瓦//',
            '嗯 我很感謝志工//',
            '*/',
        ]

        self.assertEqual(
            setup_module.render_meta_txt(lines),
            "\n".join(
                [
                    'TITLE:',
                    '',
                    'OVERVIEW:',
                    '',
                    'PEOPLE:',
                    '',
                    '大愛村居民｜賽爾瓦',
                    '',
                ]
            ),
        )

    def test_voice_only_super_header_keeps_three_explicit_fields(self) -> None:
        lines = [
            '/*SUPER:',
            '聲音  慈濟雪隆分會副執行長  蘇祈逢//',
            '因為他的這屍體 是往下沖的//',
            '*/',
        ]

        self.assertIn(
            '聲音｜慈濟雪隆分會副執行長｜蘇祈逢//',
            setup_module.render_body_txt(lines).splitlines(),
        )
        self.assertIn(
            '聲音｜慈濟雪隆分會副執行長｜蘇祈逢',
            setup_module.render_meta_txt(lines).splitlines(),
        )


if __name__ == '__main__':
    unittest.main()

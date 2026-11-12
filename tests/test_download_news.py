import importlib.util
import errno
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "download_news.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


download_news = load_module("download_news", MODULE_PATH)


class DownloadNewsTest(unittest.TestCase):
    def test_extracts_only_timecoded_voiceovers(self):
        body = """0014
中文旁白。
First narration.

(Speaker)
/*SUPER:
受訪者｜名字//
訪問內容//
*/
Interview translation that must not be downloaded.

0038
第二段中文旁白。
Second narration
continues here.

0123
Da Ai TV reports.
"""

        voiceovers = download_news.extract_timed_voiceovers(body)

        self.assertEqual(
            [(item.ordinal, item.timecode, item.text) for item in voiceovers],
            [
                (1, "0014", "First narration."),
                (2, "0038", "Second narration\ncontinues here."),
                (3, "0123", "Da Ai TV reports."),
            ],
        )

    def test_finds_accessible_control_uid(self):
        snapshot = '\n'.join(
            [
                'uid=1_38 button "settings button [reading page]"',
                'uid=2_2 button "convert to mp3 button [settings]"',
            ]
        )

        uid = download_news.control_uid(snapshot, "convert to mp3 button [settings]")

        self.assertEqual(uid, "2_2")

    def test_finds_editor_uid_by_value(self):
        snapshot = 'uid=1_40 generic value="Existing text"'

        self.assertEqual(download_news.editor_uid(snapshot), "1_40")

    def test_moves_download_across_filesystems(self):
        source = mock.Mock(spec=Path)
        source.replace.side_effect = OSError(errno.EXDEV, "cross-device link")
        destination = Path("/output/1_0014.mp3")
        with mock.patch.object(download_news.shutil, "move") as move:
            download_news.move_download(source, destination)

        move.assert_called_once_with(str(source), str(destination))

    def test_closes_stale_conversion_dialog(self):
        snapshot = 'uid=4_4 button "convert cancel button"'
        paths = mock.Mock()
        with mock.patch.object(download_news, "_click") as click:
            self.assertTrue(download_news.close_conversion_dialog(paths, 3, snapshot))

        click.assert_called_once_with(paths, 3, "4_4")

    def test_finds_windows_account_from_wsl_path(self):
        path_env = ":".join(
            [
                "/usr/bin",
                "/mnt/c/Users/026422/AppData/Local/Microsoft/WindowsApps",
            ]
        )

        account = download_news.windows_account(path_env)

        self.assertEqual(account, ("C", "026422"))

    def test_builds_windows_connector_command(self):
        paths = download_news.ConnectorPaths.for_account("C", "026422")

        command = download_news.connector_command(paths, "list_pages")

        self.assertEqual(command[0], str(paths.node_exe))
        self.assertEqual(command[1], str(paths.cli_script_windows))
        self.assertEqual(command[2:], ["list_pages", "--output-format=json"])
        self.assertEqual(paths.downloads_wsl, Path("/mnt/c/Users/026422/Downloads"))

    def test_requires_exactly_one_naturalreader_page(self):
        pages = [
            {"id": 1, "url": "https://example.com/"},
            {"id": 2, "url": "https://www.naturalreaders.com/online/"},
        ]

        page = download_news.select_naturalreader_page(pages)

        self.assertEqual(page["id"], 2)

    def test_rejects_multiple_naturalreader_pages(self):
        pages = [
            {"id": 1, "url": "https://www.naturalreaders.com/online/"},
            {"id": 2, "url": "https://www.naturalreaders.com/online/?second"},
        ]

        with self.assertRaisesRegex(ValueError, "found 2 NaturalReader tabs"):
            download_news.select_naturalreader_page(pages)

    def test_main_stops_when_connector_is_not_prepared(self):
        paths = mock.Mock()
        paths.is_prepared.return_value = False
        with (
            mock.patch.object(download_news, "connector_paths", return_value=paths),
            mock.patch.object(download_news, "run_connector") as run_connector,
        ):
            result = download_news.main([])

        self.assertEqual(result, 1)
        run_connector.assert_not_called()

    def test_prepare_does_not_connect_to_chrome(self):
        paths = mock.Mock()
        with (
            mock.patch.object(download_news, "connector_paths", return_value=paths),
            mock.patch.object(download_news, "prepare_connector") as prepare,
            mock.patch.object(download_news, "start_connector") as start,
        ):
            result = download_news.main(["--prepare"])

        self.assertEqual(result, 0)
        prepare.assert_called_once_with(paths)
        start.assert_not_called()

    def test_main_lists_pages_without_launching_chrome(self):
        paths = mock.Mock()
        paths.is_prepared.return_value = True
        pages = [{"id": 7, "url": "https://www.naturalreaders.com/online/"}]
        with (
            mock.patch.object(download_news, "connector_paths", return_value=paths),
            mock.patch.object(download_news, "start_connector") as start,
            mock.patch.object(download_news, "run_connector", return_value=pages),
            mock.patch.object(download_news, "stop_connector") as stop,
        ):
            result = download_news.main(["--check"])

        self.assertEqual(result, 0)
        start.assert_called_once_with(paths)
        stop.assert_called_once_with(paths)

    def test_main_downloads_missing_voiceovers_in_order(self):
        paths = mock.Mock()
        paths.is_prepared.return_value = True
        paths.downloads_wsl = Path("/windows/Downloads")
        pages = [{"id": 7, "url": "https://www.naturalreaders.com/online/"}]
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            body = root / "body.txt"
            body.write_text(
                "0014\n中文。\nFirst.\n\n0038\n中文。\nSecond.\n",
                encoding="utf-8",
            )
            (root / "1_0014.mp3").write_bytes(b"existing")
            with (
                mock.patch.object(download_news, "connector_paths", return_value=paths),
                mock.patch.object(download_news, "start_connector"),
                mock.patch.object(download_news, "run_connector", return_value=pages),
                mock.patch.object(download_news, "stop_connector"),
                mock.patch.object(download_news, "download_voiceover") as download,
            ):
                result = download_news.main(
                    ["--body", str(body), "--output", str(root)]
                )

        self.assertEqual(result, 0)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(download.call_args.args[2].filename, "2_0038.mp3")


if __name__ == "__main__":
    unittest.main()

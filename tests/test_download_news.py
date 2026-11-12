import importlib.util
import sys
import unittest
from urllib.error import URLError
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
    def test_fetch_tabs_falls_back_to_windows_loopback(self):
        payload = '[{"id":"one","type":"page","url":"https://example.com"}]'
        with (
            mock.patch.object(
                download_news,
                "urlopen",
                side_effect=URLError("WSL loopback unavailable"),
            ),
            mock.patch.object(
                download_news.subprocess,
                "run",
                return_value=mock.Mock(stdout=payload),
            ) as run,
        ):
            tabs = download_news.fetch_tabs(9222)

        self.assertEqual(tabs[0]["id"], "one")
        self.assertIn("http://127.0.0.1:9222/json/list", run.call_args.args[0])

    def test_finds_windows_profile_from_wsl_path(self):
        path_env = ":".join(
            [
                "/usr/bin",
                "/mnt/c/Users/026422/AppData/Local/Microsoft/WindowsApps",
            ]
        )

        profile = download_news.windows_profile_path(path_env=path_env)

        self.assertEqual(
            profile,
            r"C:\Users\026422\AppData\Local\news-tools-chrome",
        )

    def test_builds_dedicated_chrome_command(self):
        command = download_news.chrome_command(
            Path("/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"),
            r"C:\Users\tester\AppData\Local\news-tools-chrome",
            9333,
        )

        self.assertEqual(
            command,
            [
                "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
                "--remote-debugging-port=9333",
                r"--user-data-dir=C:\Users\tester\AppData\Local\news-tools-chrome",
                "--no-first-run",
                download_news.NATURALREADERS_URL,
            ],
        )

    def test_reuses_matching_naturalreader_tab(self):
        tabs = [
            {"id": "first", "url": "https://example.com/"},
            {"id": "second", "url": download_news.NATURALREADERS_URL},
        ]

        self.assertEqual(download_news.find_naturalreader_tab(tabs)["id"], "second")

    def test_main_opens_url_in_connected_chrome(self):
        with (
            mock.patch.object(download_news, "fetch_tabs", return_value=[]),
            mock.patch.object(download_news, "open_tab") as open_tab,
        ):
            result = download_news.main([])

        self.assertEqual(result, 0)
        open_tab.assert_called_once_with(9222, download_news.NATURALREADERS_URL)


if __name__ == "__main__":
    unittest.main()

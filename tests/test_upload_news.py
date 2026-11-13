import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "upload_news.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


upload_news = load_module("upload_news", MODULE_PATH)


class UploadNewsTest(unittest.TestCase):
    def test_finds_named_drive_row(self):
        snapshot = (
            'uid=1_58 row "翻譯完成稿 共用資料夾 上次修改日期 11月1日" selectable'
        )

        self.assertEqual(upload_news.named_row_uid(snapshot, "翻譯完成稿"), "1_58")

    def test_selects_latest_year_numerically(self):
        snapshot = "\n".join(
            [
                'uid=1_1 row "2025年 共用資料夾" selectable',
                'uid=1_2 row "2024年 共用資料夾" selectable',
                'uid=1_3 row "2026年 共用資料夾" selectable',
            ]
        )

        self.assertEqual(upload_news.latest_year_row(snapshot), (2026, "1_3"))

    def test_selects_latest_month_with_optional_space(self):
        snapshot = "\n".join(
            [
                'uid=1_1 row "2026. 9月 共用資料夾" selectable',
                'uid=1_2 row "2026.10月 共用資料夾" selectable',
                'uid=1_3 row "2026. 11月 共用資料夾" selectable',
            ]
        )

        self.assertEqual(upload_news.latest_month_row(snapshot), (2026, 11, "1_3"))

    def test_requires_files_unless_checking(self):
        parser = upload_news.build_parser()

        with self.assertRaisesRegex(ValueError, "provide at least one file"):
            upload_news.validate_args(parser.parse_args([]))

        args = upload_news.validate_args(parser.parse_args(["--check"]))
        self.assertTrue(args.check)

    def test_accepts_a_folder_for_upload(self):
        parser = upload_news.build_parser()
        with tempfile.TemporaryDirectory() as temp_name:
            folder = Path(temp_name)

            args = upload_news.validate_args(parser.parse_args([str(folder)]))

        self.assertEqual(args.paths, [folder.resolve()])

    def test_finds_localized_menuitem_with_shortcut_suffix(self):
        snapshot = 'uid=2_3 menuitem "資料夾上傳 Alt+C 再按 I"'

        uid = upload_news.accessible_uid(snapshot, "menuitem", ["資料夾上傳"])

        self.assertEqual(uid, "2_3")

    def test_limits_connector_workspaces_to_upload_locations(self):
        with tempfile.TemporaryDirectory() as temp_name:
            folder = Path(temp_name) / "story"
            folder.mkdir()
            file = folder / "main.docx"
            file.write_bytes(b"document")

            workspaces = upload_news.upload_workspaces([folder, file])

        self.assertEqual(workspaces, [folder.resolve()])

    def test_prefers_news_drive_tab_after_navigation(self):
        pages = [
            {
                "id": 1,
                "url": "https://drive.google.com/drive/my-drive",
                "title": "My Drive - Google Drive",
            },
            {
                "id": 2,
                "url": "https://drive.google.com/drive/folders/month-id",
                "title": "2026.11月 - 大愛每日英語新聞 - Google 雲端硬碟",
            },
        ]

        page = upload_news.select_drive_page(pages)

        self.assertEqual(page["id"], 2)


if __name__ == "__main__":
    unittest.main()

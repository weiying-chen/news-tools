import importlib.util
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "timestamp_vo.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


timestamp_vo = load_module("timestamp_vo", MODULE_PATH)


class TimestampVoTest(unittest.TestCase):
    def test_extracts_vo_and_excludes_super_interview_and_report(self) -> None:
        body = "\n".join(
            [
                "(SB Speaker)(5秒)",
                "/*SUPER:",
                "受訪者｜例子//",
                "這是訪問內容//",
                "*/",
                "~",
                "0018",
                "第一段旁白。",
                "First narration.",
                "",
                "(SB Other)(7秒)",
                "/*SUPER:",
                "另一段訪問//",
                "*/",
                "~",
                "",
                "第二段旁白。",
                "Second narration.",
                "",
                "/*REPORT:",
                "記者與製作人//",
                "*/",
            ]
        )

        passages = timestamp_vo.extract_vo_passages(body)

        self.assertEqual(
            [(item.text, item.timecode) for item in passages],
            [("第一段旁白。", "0018"), ("第二段旁白。", None)],
        )

    def test_aligns_known_vo_to_ordered_transcript_windows(self) -> None:
        passages = [
            timestamp_vo.VoPassage(0, "夏天還是鬧起血荒", None),
            timestamp_vo.VoPassage(1, "慈濟會所提供涼爽環境", None),
        ]
        segments = [
            timestamp_vo.TranscriptSegment(0.0, 4.0, "開場介紹"),
            timestamp_vo.TranscriptSegment(17.2, 21.0, "夏天還是鬧起血荒"),
            timestamp_vo.TranscriptSegment(25.0, 30.0, "受訪者說話"),
            timestamp_vo.TranscriptSegment(75.3, 79.0, "慈濟會所提供涼爽環境"),
        ]

        matches = timestamp_vo.align_vo_passages(passages, segments)

        self.assertEqual([match.start_seconds for match in matches], [17.2, 75.3])

    def test_inserts_nearest_second_timecode_without_overwriting_source(self) -> None:
        body = "第一段旁白。\nFirst narration.\n"
        passage = timestamp_vo.VoPassage(0, "第一段旁白。", None)
        match = timestamp_vo.VoMatch(passage, 20.08, 0.95)

        rendered = timestamp_vo.render_timestamped_body(body, [match])

        self.assertEqual(rendered, "0020\n第一段旁白。\nFirst narration.\n")

    def test_nearest_second_rounds_half_up(self) -> None:
        self.assertEqual(timestamp_vo._format_timecode(44.49), "0044")
        self.assertEqual(timestamp_vo._format_timecode(44.50), "0045")

    def test_removes_existing_timecodes_for_live_regeneration(self) -> None:
        body = "0018\n第一段旁白。\n\n0043\n第二段旁白。\n"

        untimed = timestamp_vo.remove_existing_timecodes(body)

        self.assertEqual(untimed, "第一段旁白。\n\n第二段旁白。\n")

    def test_timestamp_body_updates_source_after_all_matches_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            body_path = Path(tmp_dir) / "body.txt"
            video_path = Path(tmp_dir) / "video.mp4"
            body_path.write_text("第一段旁白。\nFirst narration.\n", encoding="utf-8")
            video_path.touch()
            segments = [timestamp_vo.TranscriptSegment(17.2, 21.0, "第一段旁白。")]

            with mock.patch.object(timestamp_vo, "transcribe", return_value=segments):
                timestamp_vo.timestamp_body(body_path, video_path)

            self.assertEqual(
                body_path.read_text(encoding="utf-8"),
                "0017\n第一段旁白。\nFirst narration.\n",
            )


if __name__ == "__main__":
    unittest.main()

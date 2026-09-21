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

    def test_long_vo_alignment_includes_more_than_twelve_segments(self) -> None:
        opening = ["a", "b", "c"]
        remainder = [f"longsegment{index}" for index in range(12)]
        passage = timestamp_vo.VoPassage(0, "".join(opening + remainder), None)
        segments = [
            timestamp_vo.TranscriptSegment(float(index), float(index + 1), text)
            for index, text in enumerate(opening + remainder)
        ]

        match = timestamp_vo.align_vo_passages([passage], segments)[0]

        self.assertEqual(match.start_seconds, 0.0)
        self.assertEqual(match.score, 1.0)

    def test_reliable_opening_word_keeps_segment_onset(self) -> None:
        passage = timestamp_vo.VoPassage(0, "這所學校", None)
        segment = timestamp_vo.TranscriptSegment(
            98.55,
            99.53,
            "這所學校",
            words=(
                timestamp_vo.TranscriptWord(98.55, 98.99, "這", 0.932),
                timestamp_vo.TranscriptWord(98.99, 99.15, "所", 0.999),
            ),
        )

        match = timestamp_vo.align_vo_passages([passage], [segment])[0]

        self.assertEqual(match.start_seconds, 98.55)
        self.assertEqual(timestamp_vo._format_timecode(match.start_seconds), "0139")

    def test_unreliable_opening_word_uses_first_reliable_word(self) -> None:
        passage = timestamp_vo.VoPassage(0, "表哥的鼓勵", None)
        segments = [
            timestamp_vo.TranscriptSegment(69.96, 70.62, "previous speech"),
            timestamp_vo.TranscriptSegment(
                70.62,
                71.90,
                "表哥的鼓勵",
                words=(
                    timestamp_vo.TranscriptWord(70.62, 71.32, "表", 0.003),
                    timestamp_vo.TranscriptWord(71.32, 71.60, "哥", 0.988),
                    timestamp_vo.TranscriptWord(71.60, 71.72, "的", 0.981),
                ),
            ),
        ]

        match = timestamp_vo.align_vo_passages([passage], segments)[0]

        self.assertEqual(match.start_seconds, 71.32)
        self.assertEqual(timestamp_vo._format_timecode(match.start_seconds), "0112")

    def test_unreliable_opening_after_silence_keeps_detected_onset(self) -> None:
        passage = timestamp_vo.VoPassage(0, "這間我們希望基金會", None)
        segments = [
            timestamp_vo.TranscriptSegment(21.98, 23.68, "previous speech"),
            timestamp_vo.TranscriptSegment(
                26.10,
                27.76,
                "之間我們希望基金會",
                words=(
                    timestamp_vo.TranscriptWord(26.10, 26.50, "之", 0.176),
                    timestamp_vo.TranscriptWord(26.50, 26.66, "間", 0.991),
                ),
            ),
        ]

        match = timestamp_vo.align_vo_passages([passage], segments)[0]

        self.assertEqual(match.start_seconds, 26.10)
        self.assertEqual(timestamp_vo._format_timecode(match.start_seconds), "0026")

    def test_sung_false_opening_before_long_gap_uses_reliable_word(self) -> None:
        passage = timestamp_vo.VoPassage(0, "一到十年級", None)
        segments = [
            timestamp_vo.TranscriptSegment(130.0, 134.5, "song"),
            timestamp_vo.TranscriptSegment(
                135.82,
                141.48,
                "一到十年級",
                words=(
                    timestamp_vo.TranscriptWord(135.82, 136.26, "一", 0.017),
                    timestamp_vo.TranscriptWord(140.32, 140.76, "到", 0.968),
                    timestamp_vo.TranscriptWord(140.76, 141.02, "十", 0.990),
                    timestamp_vo.TranscriptWord(141.02, 141.16, "年", 0.995),
                    timestamp_vo.TranscriptWord(141.16, 141.48, "級", 0.988),
                ),
            ),
        ]

        match = timestamp_vo.align_vo_passages([passage], segments)[0]

        self.assertEqual(match.start_seconds, 140.32)

    def test_uses_passage_onset_inside_matched_segment(self) -> None:
        passage = timestamp_vo.VoPassage(0, "除了軍警人員還有十多位社區婦女", None)
        segment = timestamp_vo.TranscriptSegment(
            134.1,
            140.0,
            "前一句旁白除了軍警人員還有十多位社區婦女",
            words=(
                timestamp_vo.TranscriptWord(134.1, 135.8, "前一句旁白", 0.99),
                timestamp_vo.TranscriptWord(136.2, 136.8, "除了", 0.99),
                timestamp_vo.TranscriptWord(136.8, 137.5, "軍警人員", 0.99),
                timestamp_vo.TranscriptWord(137.5, 138.0, "還有", 0.99),
                timestamp_vo.TranscriptWord(138.0, 139.5, "十多位社區婦女", 0.99),
            ),
        )

        match = timestamp_vo.align_vo_passages([passage], [segment])[0]

        self.assertEqual(match.start_seconds, 136.2)
        self.assertEqual(timestamp_vo._format_timecode(match.start_seconds), "0216")

    def test_prior_window_cannot_hide_next_passage_start(self) -> None:
        passages = [
            timestamp_vo.VoPassage(0, "firstsecond", None),
            timestamp_vo.VoPassage(1, "second", None),
        ]
        segments = [
            timestamp_vo.TranscriptSegment(10.0, 11.0, "first"),
            timestamp_vo.TranscriptSegment(20.0, 21.0, "second"),
        ]

        matches = timestamp_vo.align_vo_passages(passages, segments)

        self.assertEqual([match.start_seconds for match in matches], [10.0, 20.0])

    def test_inserts_tolerance_rounded_timecode_without_overwriting_source(self) -> None:
        body = "第一段旁白。\nFirst narration.\n"
        passage = timestamp_vo.VoPassage(0, "第一段旁白。", None)
        match = timestamp_vo.VoMatch(passage, 20.08, 0.95)

        rendered = timestamp_vo.render_timestamped_body(body, [match])

        self.assertEqual(rendered, "0020\n第一段旁白。\nFirst narration.\n")

    def test_timestamp_allows_small_lead_before_rounding_upward(self) -> None:
        self.assertEqual(timestamp_vo._format_timecode(14.30), "0015")
        self.assertEqual(timestamp_vo._format_timecode(98.00), "0138")
        self.assertEqual(timestamp_vo._format_timecode(98.01), "0138")
        self.assertEqual(timestamp_vo._format_timecode(98.20), "0138")
        self.assertEqual(timestamp_vo._format_timecode(98.21), "0139")
        self.assertEqual(timestamp_vo._format_timecode(98.49), "0139")
        self.assertEqual(timestamp_vo._format_timecode(98.50), "0139")
        self.assertEqual(timestamp_vo._format_timecode(98.99), "0139")
        self.assertEqual(timestamp_vo._format_timecode(37.86), "0038")
        self.assertEqual(timestamp_vo._format_timecode(59.01), "0059")

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

            with mock.patch.object(
                timestamp_vo,
                "transcribe",
                return_value=segments,
            ) as transcribe:
                timestamp_vo.timestamp_body(body_path, video_path)

            self.assertEqual(
                body_path.read_text(encoding="utf-8"),
                "0017\n第一段旁白。\nFirst narration.\n",
            )
            transcribe.assert_called_once_with(video_path, "small")

    def test_timestamp_body_rechecks_suspicious_match_with_medium(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            body_path = Path(tmp_dir) / "body.txt"
            video_path = Path(tmp_dir) / "video.mp4"
            body_path.write_text(
                "人有無限可能。愛的種子一旦種下，終有一日開枝散葉。\n",
                encoding="utf-8",
            )
            video_path.touch()
            small_segments = [
                timestamp_vo.TranscriptSegment(
                    54.46,
                    57.36,
                    "一旦種下終有一日開枝散葉",
                )
            ]
            medium_segments = [
                timestamp_vo.TranscriptSegment(
                    51.98,
                    57.68,
                    "人有無限可能愛的種子一旦種下終有一日開枝散葉",
                )
            ]

            with mock.patch.object(
                timestamp_vo,
                "transcribe",
                side_effect=[small_segments, medium_segments],
            ) as transcribe:
                matches = timestamp_vo.timestamp_body(body_path, video_path)

            self.assertEqual(matches[0].start_seconds, 51.98)
            self.assertTrue(body_path.read_text(encoding="utf-8").startswith("0052\n"))
            self.assertEqual(transcribe.call_count, 2)
            self.assertEqual(transcribe.call_args_list[1].args[:2], (video_path, "medium"))
            self.assertEqual(
                transcribe.call_args_list[1].kwargs,
                {"clip_ranges": [(46.46, 60.36)]},
            )

    def test_adds_missing_single_super_duration_between_vo_passages(self) -> None:
        body = "\n".join(
            [
                "0010",
                "第一段旁白。",
                "",
                "/*SUPER:",
                "記者｜林國新//",
                "訪問內容//",
                "*/",
                "",
                "0025",
                "第二段旁白。",
                "",
            ]
        )
        passages = timestamp_vo.extract_vo_passages(body)
        matches = [
            timestamp_vo.VoMatch(passages[0], 10.0, 0.9, end_seconds=15.0),
            timestamp_vo.VoMatch(passages[1], 25.0, 0.9, end_seconds=30.0),
        ]
        segments = [
            timestamp_vo.TranscriptSegment(10.0, 15.0, "第一段旁白"),
            timestamp_vo.TranscriptSegment(16.0, 23.0, "interview"),
            timestamp_vo.TranscriptSegment(25.0, 30.0, "第二段旁白"),
        ]

        durations, warnings = timestamp_vo.infer_missing_super_durations(
            body,
            matches,
            segments,
        )
        rendered = timestamp_vo.render_timestamped_body(
            body,
            matches,
            super_durations=durations,
        )

        self.assertEqual(warnings, [])
        self.assertEqual([(item.line_index, item.seconds) for item in durations], [(6, 7)])
        self.assertIn("\n*/\n7\n", rendered)

    def test_adds_missing_super_duration_before_first_vo(self) -> None:
        body = "\n".join(
            [
                "/*SUPER:",
                "記者｜林國新//",
                "現場報導//",
                "*/",
                "",
                "0139",
                "第一段旁白。",
            ]
        )
        passages = timestamp_vo.extract_vo_passages(body)
        matches = [
            timestamp_vo.VoMatch(passages[0], 99.0, 0.9, end_seconds=105.0),
        ]
        segments = [
            timestamp_vo.TranscriptSegment(81.0, 99.0, "現場報導"),
            timestamp_vo.TranscriptSegment(99.0, 105.0, "第一段旁白"),
        ]

        durations, warnings = timestamp_vo.infer_missing_super_durations(
            body,
            matches,
            segments,
        )

        self.assertEqual(warnings, [])
        self.assertEqual([(item.line_index, item.seconds) for item in durations], [(3, 18)])

    def test_preserves_super_with_duration_in_preceding_cue(self) -> None:
        body = "\n".join(
            [
                "0010",
                "第一段旁白。",
                "",
                "(SB Reporter)(17秒)",
                "/*SUPER:",
                "記者｜林國新//",
                "*/",
                "",
                "0025",
                "第二段旁白。",
            ]
        )
        passages = timestamp_vo.extract_vo_passages(body)
        matches = [
            timestamp_vo.VoMatch(passages[0], 10.0, 0.9, end_seconds=15.0),
            timestamp_vo.VoMatch(passages[1], 25.0, 0.9, end_seconds=30.0),
        ]

        durations, warnings = timestamp_vo.infer_missing_super_durations(
            body,
            matches,
            [],
        )

        self.assertEqual(durations, [])
        self.assertEqual(warnings, [])

    def test_preserves_super_duration_after_interview_translation(self) -> None:
        body = "\n".join(
            [
                "0010",
                "第一段旁白。",
                "",
                "/*SUPER:",
                "記者｜林國新//",
                "訪問內容//",
                "*/",
                "Interview translation.",
                "continues here.",
                "17",
                "",
                "0025",
                "第二段旁白。",
            ]
        )
        passages = timestamp_vo.extract_vo_passages(body)
        matches = [
            timestamp_vo.VoMatch(passages[0], 10.0, 0.9, end_seconds=15.0),
            timestamp_vo.VoMatch(passages[1], 25.0, 0.9, end_seconds=30.0),
        ]

        durations, warnings = timestamp_vo.infer_missing_super_durations(
            body,
            matches,
            [timestamp_vo.TranscriptSegment(16.0, 23.0, "interview")],
        )

        self.assertEqual(durations, [])
        self.assertEqual(warnings, [])

    def test_preserves_super_duration_in_named_preceding_cue(self) -> None:
        body = "\n".join(
            [
                "0010",
                "第一段旁白。",
                "",
                "(25秒，Dipak Kumar Karki)",
                "/*SUPER:",
                "里長｜狄帕克//",
                "*/",
                "",
                "0025",
                "第二段旁白。",
            ]
        )
        passages = timestamp_vo.extract_vo_passages(body)
        matches = [
            timestamp_vo.VoMatch(passages[0], 10.0, 0.9, end_seconds=15.0),
            timestamp_vo.VoMatch(passages[1], 25.0, 0.9, end_seconds=30.0),
        ]

        durations, warnings = timestamp_vo.infer_missing_super_durations(
            body,
            matches,
            [timestamp_vo.TranscriptSegment(16.0, 23.0, "interview")],
        )

        self.assertEqual(durations, [])
        self.assertEqual(warnings, [])

    def test_multiple_supers_between_vo_passages_are_left_unchanged(self) -> None:
        body = "\n".join(
            [
                "0010",
                "第一段旁白。",
                "/*SUPER:",
                "第一位//",
                "*/",
                "/*SUPER:",
                "第二位//",
                "*/",
                "0025",
                "第二段旁白。",
            ]
        )
        passages = timestamp_vo.extract_vo_passages(body)
        matches = [
            timestamp_vo.VoMatch(passages[0], 10.0, 0.9, end_seconds=15.0),
            timestamp_vo.VoMatch(passages[1], 25.0, 0.9, end_seconds=30.0),
        ]

        durations, warnings = timestamp_vo.infer_missing_super_durations(
            body,
            matches,
            [timestamp_vo.TranscriptSegment(16.0, 23.0, "interviews")],
        )

        self.assertEqual(durations, [])
        self.assertEqual(len(warnings), 2)

    def test_infers_one_missing_duration_beside_known_super(self) -> None:
        body = "\n".join(
            [
                "0010",
                "第一段旁白。",
                "(4秒)",
                "/*SUPER:",
                "第一位//",
                "*/",
                "(Second Person)",
                "/*SUPER:",
                "第二位//",
                "*/",
                "0030",
                "第二段旁白。",
            ]
        )
        passages = timestamp_vo.extract_vo_passages(body)
        matches = [
            timestamp_vo.VoMatch(passages[0], 10.0, 0.9, end_seconds=15.0),
            timestamp_vo.VoMatch(passages[1], 30.0, 0.9, end_seconds=35.0),
        ]
        segments = [
            timestamp_vo.TranscriptSegment(16.0, 20.0, "first interview"),
            timestamp_vo.TranscriptSegment(21.0, 27.0, "second interview"),
        ]

        durations, warnings = timestamp_vo.infer_missing_super_durations(
            body,
            matches,
            segments,
        )

        self.assertEqual(warnings, [])
        self.assertEqual([(item.line_index, item.seconds) for item in durations], [(9, 7)])


if __name__ == "__main__":
    unittest.main()

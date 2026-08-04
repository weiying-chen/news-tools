import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REVIEW_MODULE_PATH = Path(__file__).resolve().parents[1] / 'review_news.py'


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


review_module = load_module('review_news', REVIEW_MODULE_PATH)


class ReviewNewsTest(unittest.TestCase):
    def test_timestamped_mp3_filename_becomes_timeline_clip(self) -> None:
        clip = review_module.parse_narration_clip(Path('2_0118.mp3'))

        self.assertEqual(clip.start_seconds, 78)
        self.assertEqual(clip.path, Path('2_0118.mp3'))

    def test_invalid_seconds_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, 'seconds'):
            review_module.parse_narration_clip(Path('1_0168.mp3'))

    def test_current_directory_discovers_video_and_orders_clips_by_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            directory = Path(tmp_dir)
            (directory / 'story.webm').touch()
            (directory / '2_0118.mp3').touch()
            (directory / '1_0034.mp3').touch()
            (directory / 'notes.mp3').touch()

            video, clips = review_module.discover_media(directory)

        self.assertEqual(video.name, 'story.webm')
        self.assertEqual([clip.path.name for clip in clips], ['1_0034.mp3', '2_0118.mp3'])

    def test_multiple_videos_require_an_explicit_choice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            directory = Path(tmp_dir)
            (directory / 'first.mp4').touch()
            (directory / 'second.webm').touch()
            (directory / '1_0015.mp3').touch()

            with self.assertRaisesRegex(ValueError, 'multiple video'):
                review_module.discover_media(directory)

    def test_ffmpeg_command_places_and_trims_each_clip(self) -> None:
        clips = [
            review_module.NarrationClip(Path('/story/1_0015.mp3'), 15),
            review_module.NarrationClip(Path('/story/2_0042.mp3'), 42),
        ]

        command = review_module.build_ffmpeg_command(
            ffmpeg='ffmpeg',
            video=Path('/story/video.webm'),
            clips=clips,
            video_duration=70.0,
            output=Path('/cache/mixed.flac'),
            english_volume=0.8,
        )

        self.assertEqual(command[:7], [
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i',
            '/story/video.webm',
        ])
        self.assertIn('/story/1_0015.mp3', command)
        self.assertIn('/story/2_0042.mp3', command)
        graph = command[command.index('-filter_complex') + 1]
        self.assertIn('[0:a]aresample=48000', graph)
        self.assertIn('atrim=duration=27', graph)
        self.assertIn('adelay=15000:all=1', graph)
        self.assertIn('atrim=duration=28', graph)
        self.assertIn('adelay=42000:all=1', graph)
        self.assertIn('volume=0.8[english]', graph)
        self.assertIn(
            '[original][english]amix=inputs=2:duration=first:normalize=0[out]',
            graph,
        )
        self.assertEqual(command[command.index('-c:a') + 1], 'flac')
        self.assertNotIn('-b:a', command)
        self.assertEqual(command[-1], '/cache/mixed.flac')

    def test_unchanged_sources_reuse_cached_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            directory = Path(tmp_dir)
            video = directory / 'story.webm'
            mp3 = directory / '1_0015.mp3'
            output = directory / 'english.m4a'
            manifest = directory / 'manifest.json'
            video.write_bytes(b'video')
            mp3.write_bytes(b'audio')
            output.write_bytes(b'timeline')
            fingerprint = review_module.source_fingerprint(
                video,
                [review_module.NarrationClip(mp3, 15)],
            )
            manifest.write_text(json.dumps(fingerprint), encoding='utf-8')

            self.assertTrue(
                review_module.cache_is_current(
                    output,
                    manifest,
                    fingerprint,
                )
            )

    def test_mpv_command_selects_the_pre_mixed_external_timeline(self) -> None:
        command = review_module.build_mpv_command(
            mpv='mpv',
            video=Path('/story/video.webm'),
            timeline=Path('/cache/english.m4a'),
        )

        self.assertEqual(command[0], 'mpv')
        self.assertIn('--external-file=/cache/english.m4a', command)
        self.assertIn('--aid=2', command)
        self.assertNotIn('--vo=sdl', command)
        self.assertNotIn('--ao=pulse', command)
        self.assertNotIn('--hwdec=auto', command)
        self.assertIn('--profile=fast', command)
        self.assertIn('--autofit=1280x720', command)
        self.assertIn('--geometry=50%:50%', command)
        self.assertFalse(any(arg.startswith('--lavfi-complex=') for arg in command))
        self.assertEqual(command[-1], '/story/video.webm')

    def test_cache_uses_fast_lossless_mixed_audio(self) -> None:
        output, manifest = review_module.cache_paths(
            Path('/story'),
            Path('/cache'),
        )

        self.assertEqual(output.name, 'mixed-timeline.mka')
        self.assertEqual(manifest.name, 'manifest.json')


if __name__ == '__main__':
    unittest.main()

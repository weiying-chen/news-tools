import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectMetadataTest(unittest.TestCase):
    def test_timestamp_command_declares_its_runtime_dependency(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertIn("faster-whisper>=1.2,<2", metadata["project"]["dependencies"])
        self.assertEqual(
            metadata["project"]["scripts"]["timestamp-vo"],
            "timestamp_vo:main",
        )
        self.assertIn("timestamp_vo", metadata["tool"]["setuptools"]["py-modules"])


if __name__ == "__main__":
    unittest.main()

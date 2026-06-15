import importlib.util
import sys
import unittest
from pathlib import Path


SETUP_MODULE_PATH = Path('/home/weiying/python/news-tools/setup_news.py')


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup_module = load_module('setup_news', SETUP_MODULE_PATH)


class SetupNewsPeopleTest(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()

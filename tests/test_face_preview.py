import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FacePreviewTests(unittest.TestCase):
    def test_visible_copy_is_only_the_two_hermes_labels(self):
        class CopyParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_body = False
                self.in_script = False
                self.copy = []

            def handle_starttag(self, tag, attrs):
                if tag == 'body':
                    self.in_body = True
                if tag == 'script':
                    self.in_script = True

            def handle_endtag(self, tag):
                if tag == 'script':
                    self.in_script = False
                if tag == 'body':
                    self.in_body = False

            def handle_data(self, data):
                if self.in_body and not self.in_script and data.strip():
                    self.copy.append(data.strip())

        parser = CopyParser()
        parser.feed((ROOT / 'preview/face/index.html').read_text())
        self.assertEqual(parser.copy, ['HERMES', 'Hermes'])

    def test_supplied_head_is_preserved_byte_for_byte(self):
        path = ROOT / "preview/face/hermes-head.json"
        self.assertTrue(path.is_file(), "supplied head asset is not imported")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         "6b245f2307e736913866b6377bc3b496db84ad68709d54f4b8473f752e11a838")
        asset = json.loads(path.read_text())
        self.assertEqual((asset["god"], asset["bot"], asset["kind"], asset["grid"]),
                         ("hermes", "hermes", "head", 48))
        self.assertEqual(len(asset["dots"]), 1003)
        self.assertEqual(sum(dot["p"] == "body" for dot in asset["dots"]), 137)


if __name__ == "__main__":
    unittest.main()

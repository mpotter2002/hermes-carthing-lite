"""Tests for the safe, read-only companion content projection."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from src.companion_content import project_content


def png_bytes(width: int = 1, height: int = 1, *, rgba: bytes | None = None) -> bytes:
    """Build a small, valid, non-interlaced 8-bit RGBA PNG."""

    if rgba is None:
        rgba = b"\x11\x22\x33\xff" * width * height
    rows = b"".join(
        b"\x00" + rgba[row * width * 4 : (row + 1) * width * 4]
        for row in range(height)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class CompanionContentTests(unittest.TestCase):
    def test_compressed_pixels_remain_memory_bounded(self):
        import tracemalloc
        import src.companion_content as content
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
        data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00' * (8 * 1024 * 1024))) + chunk(b'IEND', b'')
        tracemalloc.start()
        try:
            with self.assertRaises(ValueError):
                content._strict_png_dimensions(data)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 1024 * 1024, 'invalid tiny image must not expand its compressed bomb')

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "incoming"
        self.output = Path(self.temp.name) / "output"
        self.root.mkdir()
        self.output.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def project(self, text: str | None, *, activity: str = "idle", tool: str | None = None) -> dict:
        reply = None if text is None else {"id": "reply-1", "text": text}
        return project_content(reply, activity, tool, self.root, self.output)

    def write_image(self, relative: str = "safe.png", data: bytes | None = None) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes() if data is None else data)
        return path

    def test_rejects_replaced_source_parent_before_open(self):
        from unittest.mock import patch
        import src.companion_content as content
        source = self.write_image('nested/safe.png')
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (outside / 'safe.png').write_bytes(png_bytes(2, 1))
        original = os.open
        replaced = False
        def racing_open(path, flags, *args, **kwargs):
            nonlocal replaced
            if not replaced and (str(path) == str(source) or str(path) == 'nested'):
                replaced = True
                source.parent.rename(self.root / 'retired')
                source.parent.symlink_to(outside, target_is_directory=True)
            return original(path, flags, *args, **kwargs)
        with patch.object(content.os, 'open', side_effect=racing_open):
            result = self.project(f'MEDIA:{source}')
        self.assertTrue(replaced)
        self.assertIsNone(result['image'])

    def test_rejects_replaced_output_parent_before_open(self):
        from unittest.mock import patch
        import src.companion_content as content
        source = self.write_image()
        self.output = self.output / 'nested' / 'delivered'
        self.output.mkdir(parents=True)
        outside = Path(self.temp.name) / 'outside'
        (outside / 'delivered').mkdir(parents=True)
        original = os.open
        replaced = False
        def racing_open(path, flags, *args, **kwargs):
            nonlocal replaced
            if not replaced and (str(path) == str(self.output) or str(path) == 'nested'):
                replaced = True
                self.output.parent.rename(self.output.parent.with_name('retired-output'))
                self.output.parent.symlink_to(outside, target_is_directory=True)
            return original(path, flags, *args, **kwargs)
        with patch.object(content.os, 'open', side_effect=racing_open):
            result = self.project(f'MEDIA:{source}')
        self.assertTrue(replaced)
        self.assertIsNone(result['image'])
        self.assertEqual(list((outside / 'delivered').iterdir()), [])

    def test_projects_valid_media_png_with_safe_deterministic_name(self) -> None:
        source = self.write_image()
        result = self.project(f"Rendered result\nMEDIA:{source}")
        expected = hashlib.sha256(source.read_bytes()).hexdigest() + ".png"
        self.assertEqual(result["image"], {"src": expected, "alt": "Rendered result"})
        self.assertEqual(result["cards"][1]["text"], "Rendered result ")
        self.assertNotIn(str(source), result["cards"][1]["text"])
        copied = self.output / expected
        self.assertEqual(copied.read_bytes(), source.read_bytes())
        self.assertEqual(copied.stat().st_mode & 0o777, 0o600)
        original_inode = copied.stat().st_ino
        self.assertEqual(self.project(f"Rendered result\nMEDIA:{source}")["image"]["src"], expected)
        self.assertEqual(copied.stat().st_ino, original_inode)

    def test_projects_markdown_image_and_sanitizes_alt_controls(self) -> None:
        source = self.write_image()
        result = self.project(f"Before ![safe\x00 alt](<{source}>) after")
        self.assertEqual(result["image"]["alt"], "safe  alt")

    def test_rejects_traversal_even_when_it_resolves_inside_root(self) -> None:
        source = self.write_image("nested/safe.png")
        traversing = self.root / "nested" / ".." / "nested" / source.name
        self.assertIsNone(self.project(f"MEDIA:{traversing}")["image"])

    def test_rejects_source_file_and_directory_symlinks(self) -> None:
        source = self.write_image()
        linked_file = self.root / "linked.png"
        linked_file.symlink_to(source)
        self.assertIsNone(self.project(f"MEDIA:{linked_file}")["image"])

        real_dir = self.root / "real"
        real_dir.mkdir()
        nested = real_dir / "safe.png"
        nested.write_bytes(png_bytes())
        linked_dir = self.root / "linked-dir"
        linked_dir.symlink_to(real_dir, target_is_directory=True)
        self.assertIsNone(self.project(f"MEDIA:{linked_dir / 'safe.png'}")["image"])

        linked_root = Path(self.temp.name) / "linked-root"
        linked_root.symlink_to(self.root, target_is_directory=True)
        self.assertIsNone(
            project_content(
                {"id": "reply-1", "text": f"MEDIA:{linked_root / source.name}"},
                "idle",
                None,
                linked_root,
                self.output,
            )["image"]
        )

    def test_rejects_directories_and_ambiguous_image_references(self) -> None:
        source = self.write_image()
        directory = self.root / "folder.png"
        directory.mkdir()
        self.assertIsNone(self.project(f"MEDIA:{directory}")["image"])
        self.assertIsNone(
            self.project(f"MEDIA:{source}\n![second]({source})")["image"]
        )

    def test_rejects_remote_and_non_absolute_references(self) -> None:
        for reference in (
            "MEDIA:https://example.test/a.png",
            "![x](http://example.test/a.png)",
            "MEDIA:relative.png",
        ):
            with self.subTest(reference=reference):
                self.assertIsNone(self.project(reference)["image"])

    def test_rejects_hidden_files_and_paths_outside_asset_root(self) -> None:
        hidden = self.write_image(".secret.png")
        outside = Path(self.temp.name) / "outside.png"
        outside.write_bytes(png_bytes())
        self.assertIsNone(self.project(f"MEDIA:{hidden}")["image"])
        self.assertIsNone(self.project(f"MEDIA:{outside}")["image"])

    def test_rejects_unsupported_extension_and_mismatched_signature(self) -> None:
        jpeg_named_png = self.write_image("fake.png", b"\xff\xd8\xff\xd9")
        png_named_jpeg = self.write_image("real.jpg", png_bytes())
        self.assertIsNone(self.project(f"MEDIA:{jpeg_named_png}")["image"])
        self.assertIsNone(self.project(f"MEDIA:{png_named_jpeg}")["image"])

    def test_rejects_oversize_bytes_dimensions_and_incomplete_png(self) -> None:
        oversize = self.write_image("oversize.png", b"\x89PNG\r\n\x1a\n" + b"x" * (4 * 1024 * 1024))
        too_wide = self.write_image("wide.png", png_bytes(2049, 1))
        incomplete = self.write_image("incomplete.png", png_bytes()[:-5])
        for source in (oversize, too_wide, incomplete):
            with self.subTest(source=source.name):
                self.assertIsNone(self.project(f"MEDIA:{source}")["image"])

    def test_rejects_trailing_polyglot_content_and_corrupt_pixels(self) -> None:
        trailing = self.write_image("trailing.png", png_bytes() + b"<script>alert(1)</script>")
        corrupt = bytearray(png_bytes())
        corrupt[-13] ^= 1
        bad_crc = self.write_image("corrupt.png", bytes(corrupt))
        self.assertIsNone(self.project(f"MEDIA:{trailing}")["image"])
        self.assertIsNone(self.project(f"MEDIA:{bad_crc}")["image"])

    def test_existing_hash_named_output_is_never_overwritten(self) -> None:
        source = self.write_image()
        name = hashlib.sha256(source.read_bytes()).hexdigest() + ".png"
        destination = self.output / name
        destination.write_bytes(b"different")
        result = self.project(f"MEDIA:{source}")
        self.assertIsNone(result["image"])
        self.assertEqual(destination.read_bytes(), b"different")

    def test_invalid_activity_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "activity"):
            self.project(None, activity="busy")

    def test_fixed_cards_are_plain_bounded_and_match_display_schema(self) -> None:
        payload = "<script>alert('x')</script>\x00" + "x" * 9000
        result = self.project(payload, activity="running", tool="python\x07")
        self.assertEqual(set(result), {"image", "cards"})
        self.assertIsNone(result["image"])
        self.assertEqual(len(result["cards"]), 2)
        self.assertEqual(
            result["cards"][0],
            {"id": "status", "title": "Status", "text": "Running · python "},
        )
        reply_card = result["cards"][1]
        self.assertEqual(set(reply_card), {"id", "title", "text"})
        self.assertEqual(reply_card["id"], "reply-excerpt")
        self.assertEqual(reply_card["title"], "Reply excerpt")
        self.assertTrue(reply_card["text"].startswith("<script>alert('x')</script> "))
        self.assertLessEqual(len(reply_card["text"]), 8192)
        self.assertNotIn("\x00", reply_card["text"])
        for card in result["cards"]:
            self.assertLessEqual(len(card["id"]), 256)
            self.assertLessEqual(len(card["title"]), 256)
            self.assertLessEqual(len(card["text"]), 8192)

    def test_reply_absent_has_status_card_only(self) -> None:
        result = self.project(None, activity="unknown")
        self.assertEqual(result, {
            "image": None,
            "cards": [{"id": "status", "title": "Status", "text": "Unknown"}],
        })

    def test_invalid_reply_and_tool_shapes_are_rejected_without_echoing_values(self) -> None:
        bad_replies = [
            {},
            {"id": "", "text": "ok"},
            {"id": "id", "text": 1},
            {"id": "id", "text": "x" * 32769},
        ]
        for reply in bad_replies:
            with self.subTest(reply=type(reply.get("text")).__name__):
                with self.assertRaisesRegex(ValueError, "reply") as caught:
                    project_content(reply, "idle", None, self.root, self.output)
                self.assertNotIn(str(self.root), str(caught.exception))
        with self.assertRaisesRegex(ValueError, "tool"):
            project_content(None, "idle", "x" * 257, self.root, self.output)


if __name__ == "__main__":
    unittest.main()

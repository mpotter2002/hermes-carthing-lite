"""Project a redacted final reply into the companion display's safe V1 content.

This module deliberately supports a strict PNG subset and fixed, read-only cards.
It performs no network access and does not inspect conversation history or tools.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import unicodedata
import zlib

_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_DIMENSION = 2048
_MAX_REPLY_LENGTH = 32768
_MAX_CARD_TEXT = 8192
_MAX_ALT_LENGTH = 512
_VALID_ACTIVITIES = {
    "unknown": "Unknown",
    "idle": "Idle",
    "thinking": "Thinking",
    "running": "Running",
}
_MEDIA_REFERENCE = re.compile(r"(?m)(?:^|[ \t])MEDIA:([^\r\n]*)")
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]\r\n]{0,512})\]\(([^)\r\n]+)\)")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _plain_text(value: str, maximum: int) -> str:
    """Remove control/format/surrogate characters and enforce a display bound."""

    cleaned = "".join(
        " " if unicodedata.category(character) in {"Cc", "Cf", "Cs"} else character
        for character in value
    )
    return cleaned[:maximum]


def _validated_inputs(
    reply: dict | None, activity: str, tool: str | None
) -> tuple[str | None, str | None]:
    if activity not in _VALID_ACTIVITIES:
        raise ValueError("invalid activity")

    clean_tool = None
    if tool is not None:
        if not isinstance(tool, str) or not tool or len(tool) > 256:
            raise ValueError("invalid tool")
        clean_tool = _plain_text(tool, 256)
        if not clean_tool.strip():
            raise ValueError("invalid tool")

    if reply is None:
        return None, clean_tool
    if not isinstance(reply, dict) or set(reply) != {"id", "text"}:
        raise ValueError("invalid reply")
    reply_id = reply["id"]
    reply_text = reply["text"]
    if (
        not isinstance(reply_id, str)
        or not reply_id
        or len(reply_id) > 256
        or not isinstance(reply_text, str)
        or len(reply_text) > _MAX_REPLY_LENGTH
    ):
        raise ValueError("invalid reply")
    return reply_text, clean_tool


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except OSError:
            return True
    return False


def _reply_without_source_paths(reply_text: str) -> str:
    """Keep authored prose/markdown alt text while removing local source paths."""

    without_markdown_paths = _MARKDOWN_IMAGE.sub(lambda match: match.group(1), reply_text)
    return _MEDIA_REFERENCE.sub("", without_markdown_paths)


def _candidate_from_reply(reply_text: str) -> tuple[str, str] | None:
    """Return one unambiguous local path and a path-free alt text source."""

    candidates: list[tuple[str, str]] = []
    spans: list[tuple[int, int]] = []

    for match in _MARKDOWN_IMAGE.finditer(reply_text):
        raw_path = match.group(2).strip()
        if raw_path.startswith("<") and raw_path.endswith(">"):
            raw_path = raw_path[1:-1].strip()
        candidates.append((raw_path, match.group(1)))
        spans.append(match.span())

    for match in _MEDIA_REFERENCE.finditer(reply_text):
        candidates.append((match.group(1).strip(), ""))
        spans.append(match.span())

    if len(candidates) != 1:
        return None

    raw_path, markdown_alt = candidates[0]
    if markdown_alt:
        alt_source = markdown_alt
    else:
        chars = list(reply_text)
        for start, end in spans:
            chars[start:end] = " " * (end - start)
        alt_source = "".join(chars).strip()
    return raw_path, _plain_text(alt_source, _MAX_ALT_LENGTH)


def _strict_png_dimensions(data: bytes) -> tuple[int, int]:
    """Validate a metadata-free, non-interlaced 8-bit PNG and its pixels."""

    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("invalid image")

    offset = len(_PNG_SIGNATURE)
    width = height = channels = 0
    saw_ihdr = saw_idat = saw_iend = False
    compressed = bytearray()

    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("invalid image")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(data) or not all(
            65 <= byte <= 90 or 97 <= byte <= 122 for byte in kind
        ):
            raise ValueError("invalid image")
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise ValueError("invalid image")

        if not saw_ihdr:
            if kind != b"IHDR" or length != 13:
                raise ValueError("invalid image")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            channel_counts = {0: 1, 2: 3, 4: 2, 6: 4}
            if (
                width < 1
                or height < 1
                or width > _MAX_DIMENSION
                or height > _MAX_DIMENSION
                or bit_depth != 8
                or color_type not in channel_counts
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                raise ValueError("invalid image")
            channels = channel_counts[color_type]
            saw_ihdr = True
        elif kind == b"IDAT":
            if saw_iend:
                raise ValueError("invalid image")
            saw_idat = True
            compressed.extend(payload)
        elif kind == b"IEND":
            if length != 0 or not saw_idat or saw_iend:
                raise ValueError("invalid image")
            saw_iend = True
            offset = chunk_end
            break
        else:
            # Metadata and unknown chunks are rejected to keep the accepted file
            # grammar small and prevent active/polyglot payloads.
            raise ValueError("invalid image")
        offset = chunk_end

    if not saw_ihdr or not saw_idat or not saw_iend or offset != len(data):
        raise ValueError("invalid image")

    expected_size = height * (1 + width * channels)
    try:
        decompressor = zlib.decompressobj()
        pixels = decompressor.decompress(bytes(compressed), expected_size + 1)
        pixels += decompressor.flush()
    except zlib.error as error:
        raise ValueError("invalid image") from error
    if (
        len(pixels) != expected_size
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        raise ValueError("invalid image")
    row_size = 1 + width * channels
    if any(pixels[row * row_size] > 4 for row in range(height)):
        raise ValueError("invalid image")
    return width, height


def _read_source(raw_path: str, asset_root: Path) -> bytes | None:
    if not raw_path or "\x00" in raw_path:
        return None
    source = Path(raw_path)
    root = Path(asset_root)
    if not source.is_absolute() or not root.is_absolute() or source.suffix.lower() != ".png":
        return None
    try:
        relative = source.relative_to(root)
    except ValueError:
        return None
    if not relative.parts or any(part in {"", ".", ".."} or part.startswith(".") for part in relative.parts):
        return None
    if _has_symlink_component(root) or _has_symlink_component(source):
        return None

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(source, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size < 1 or before.st_size > _MAX_FILE_BYTES:
            return None
        data = bytearray()
        while len(data) <= _MAX_FILE_BYTES:
            block = os.read(descriptor, min(65536, _MAX_FILE_BYTES + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(descriptor)
        if (
            len(data) > _MAX_FILE_BYTES
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(data) != after.st_size
        ):
            return None
        result = bytes(data)
        _strict_png_dimensions(result)
        return result
    except (OSError, ValueError):
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _existing_output(directory_fd: int, name: str, expected: bytes) -> bool:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size != len(expected):
            return False
        actual = bytearray()
        while len(actual) < len(expected):
            block = os.read(descriptor, min(65536, len(expected) - len(actual)))
            if not block:
                break
            actual.extend(block)
        if bytes(actual) != expected:
            return False
        os.fchmod(descriptor, 0o600)
        return True
    except OSError:
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _immutable_copy(data: bytes, output_dir: Path) -> str | None:
    output = Path(output_dir)
    if not output.is_absolute():
        return None
    try:
        if not output.exists():
            parent = output.parent
            if _has_symlink_component(parent):
                return None
            output.mkdir(mode=0o700)
        if _has_symlink_component(output) or not output.is_dir():
            return None
        directory_fd = os.open(
            output,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        return None

    digest = hashlib.sha256(data).hexdigest()
    name = digest + ".png"
    temporary = "." + digest + "." + secrets.token_hex(8) + ".tmp"
    descriptor = -1
    temporary_exists = False
    try:
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_fd,
            )
            temporary_exists = True
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written < 1:
                    return None
                view = view[written:]
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                if not _existing_output(directory_fd, name, data):
                    return None
            os.unlink(temporary, dir_fd=directory_fd)
            temporary_exists = False
            os.fsync(directory_fd)
            return name
        except OSError:
            return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_exists:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except OSError:
                pass
        os.close(directory_fd)


def _project_image(reply_text: str, asset_root: Path, output_dir: Path) -> dict | None:
    candidate = _candidate_from_reply(reply_text)
    if candidate is None:
        return None
    raw_path, alt = candidate
    data = _read_source(raw_path, Path(asset_root))
    if data is None:
        return None
    name = _immutable_copy(data, Path(output_dir))
    if name is None:
        return None
    return {"src": name, "alt": alt}


def project_content(
    reply: dict | None,
    activity: str,
    tool: str | None,
    asset_root: Path,
    output_dir: Path,
) -> dict:
    """Return exactly the V1 ``image`` and fixed read-only ``cards`` fields.

    Invalid scalar input raises a generic ``ValueError``. Unsafe or malformed image
    references fail closed to ``image: None`` without disclosing source paths.
    """

    reply_text, clean_tool = _validated_inputs(reply, activity, tool)
    status = _VALID_ACTIVITIES[activity]
    if clean_tool is not None:
        status += " · " + clean_tool
    cards = [{"id": "status", "title": "Status", "text": status}]
    image = None
    if reply_text is not None:
        clean_reply = _plain_text(_reply_without_source_paths(reply_text), _MAX_CARD_TEXT)
        cards.append(
            {"id": "reply-excerpt", "title": "Reply excerpt", "text": clean_reply}
        )
        image = _project_image(reply_text, Path(asset_root), Path(output_dir))
    return {"image": image, "cards": cards}

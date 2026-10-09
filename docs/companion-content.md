# Companion content projection V1

`src/companion_content.py` is an integration-neutral projector for the existing companion display schema. It performs no network, device, service, authentication, profile, or backend work.

## Public interface

```python
project_content(
    reply: dict | None,
    activity: str,
    tool: str | None,
    asset_root: Path,
    output_dir: Path,
) -> dict
```

The result has exactly two keys:

```python
{
    "image": None | {"src": "<sha256>.png", "alt": "plain text"},
    "cards": [
        {"id": "status", "title": "Status", "text": "Idle"},
        # Present only when reply is present:
        {"id": "reply-excerpt", "title": "Reply excerpt", "text": "..."},
    ],
}
```

The fixed cards contain only caller-provided activity/tool state and the authoritative redacted final reply. They define no HTML fields, actions, generated telemetry, dates, counts, reasoning, tool history, or other inferred data; markup-looking reply text remains inert plain text through the display's `textContent` rendering path. Control, format, and surrogate characters are replaced with spaces. The reply excerpt is limited to 8,192 characters. Image source paths are removed from the reply excerpt.

Accepted activities are `unknown`, `idle`, `thinking`, and `running`. A non-null reply must contain exactly `id` and `text`, matching the existing reply limits. Invalid scalar input raises a generic `ValueError` that does not include reply content or source paths.

## Local image policy

V1 intentionally accepts only a strict PNG subset:

- `.png` extension and PNG signature must agree.
- 8-bit, non-interlaced grayscale, RGB, grayscale-alpha, or RGBA.
- Exactly `IHDR`, one or more `IDAT`, and `IEND` chunks; metadata and unknown chunks are rejected.
- All chunk CRCs, the zlib stream, decompressed row size, and PNG filter bytes are validated.
- No trailing bytes are allowed.
- Maximum file size is 4 MiB; maximum width and height are 2,048 pixels each.

JPEG, WebP, GIF, indexed-color PNG, interlaced PNG, and PNG metadata chunks are not supported in V1. This narrower policy avoids adding an image-decoding dependency and rejects active/polyglot payloads rather than guessing.

An image is considered only when the canonical final reply contains exactly one explicit reference in either form:

```text
MEDIA:/absolute/path/to/image.png
![plain alt text](</absolute/path/to/image.png>)
```

The referenced file must be lexically beneath the caller-provided absolute `asset_root`. Traversal components, hidden path components, symlinks in any source path component, directories, relative paths, URLs, malformed/ambiguous references, unsupported files, and files outside the root fail closed to `image: None`. No remote retrieval is attempted. Markdown alt text is used when supplied; otherwise path-free text from the redacted reply is used. Alt text is sanitized and limited to 512 characters.

Validated bytes are copied from the already-open source descriptor to `output_dir`. The immutable device-facing basename is `<sha256>.png`, creation is atomic and no-follow, and the file mode is `0600`. An existing hash-named file is reused only when its bytes match; it is never overwritten. Unsafe image handling fails closed without exposing the source path.

The parent integration remains responsible for authenticated delivery, lifecycle integration, and physical-device checks.

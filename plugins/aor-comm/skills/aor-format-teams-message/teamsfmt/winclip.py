"""Windows clipboard primitives: stdlib only, ctypes straight to user32/kernel32.

Why not the obvious alternatives:

* ``System.Windows.Forms.Clipboard`` (what a PowerShell shim would use) requires an
  STA apartment thread. A CLI host cannot switch its own apartment after start, so
  that route always needs a *separate* ``powershell.exe -STA`` process. The raw
  Win32 clipboard API has no apartment requirement.
* ``Set-Clipboard -AsHtml`` exists only in Windows PowerShell 5.1, never in pwsh 7,
  and corrupts every non-ASCII character (PowerShell/PowerShell#3177, closed
  Won't Fix). That alone disqualifies it for Danish text.
* ``pyperclip`` is plain-text by design; ``pywin32`` is a dependency we do not need.

CF_HTML reference (all offset/encoding rules below are quoted from it):
https://learn.microsoft.com/en-us/windows/win32/dataxchg/html-clipboard-format
"""

from __future__ import annotations

import contextlib
import ctypes
import time
from ctypes import wintypes

__all__ = [
    "CF_TEXT",
    "CF_UNICODETEXT",
    "ClipboardError",
    "STANDARD_FORMAT_NAMES",
    "dump_all",
    "html_format_id",
    "set_clipboard",
    "unwrap_cf_html",
    "wrap_cf_html",
]

# --- Win32 surface -------------------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Pointer-sized returns MUST be declared. ctypes defaults every restype to C int,
# which truncates a 64-bit HANDLE to its low 32 bits and yields a handle that
# looks plausible and is not.
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
_user32.EnumClipboardFormats.restype = wintypes.UINT
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterClipboardFormatW.restype = wintypes.UINT
_user32.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClipboardFormatNameW.restype = ctypes.c_int

_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HANDLE
_kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
_kernel32.GlobalSize.restype = ctypes.c_size_t
_kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
_kernel32.GlobalFree.restype = wintypes.HANDLE

_GMEM_MOVEABLE = 0x0002

CF_TEXT = 1
CF_BITMAP = 2
CF_METAFILEPICT = 3
CF_TIFF = 6
CF_OEMTEXT = 7
CF_DIB = 8
CF_PALETTE = 9
CF_ENHMETAFILE = 14
CF_HDROP = 15
CF_LOCALE = 16
CF_DIBV5 = 17

STANDARD_FORMAT_NAMES = {
    CF_TEXT: "CF_TEXT",
    CF_BITMAP: "CF_BITMAP",
    CF_METAFILEPICT: "CF_METAFILEPICT",
    CF_TIFF: "CF_TIFF",
    CF_OEMTEXT: "CF_OEMTEXT",
    CF_DIB: "CF_DIB",
    CF_PALETTE: "CF_PALETTE",
    CF_ENHMETAFILE: "CF_ENHMETAFILE",
    CF_HDROP: "CF_HDROP",
    CF_LOCALE: "CF_LOCALE",
    CF_DIBV5: "CF_DIBV5",
    13: "CF_UNICODETEXT",
}
CF_UNICODETEXT = 13

# Handles for these are GDI/other objects, not GlobalAlloc blocks: GlobalSize on
# them is meaningless and GlobalLock may fault. Enumerate them, never read them.
_NON_HGLOBAL_FORMATS = frozenset({CF_BITMAP, CF_PALETTE, CF_METAFILEPICT, CF_ENHMETAFILE})


class ClipboardError(RuntimeError):
    """The clipboard could not be opened or a Win32 clipboard call failed."""


def html_format_id() -> int:
    """Registered id of the ``"HTML Format"`` clipboard format (CF_HTML)."""
    fmt = _user32.RegisterClipboardFormatW("HTML Format")
    if fmt == 0:
        raise ClipboardError(f"RegisterClipboardFormatW failed (WinError {ctypes.get_last_error()})")
    return fmt


@contextlib.contextmanager
def _clipboard(retries: int = 12, delay: float = 0.05):
    """Own the clipboard for the duration of the block.

    The clipboard is machine-wide state that any process can hold. OpenClipboard
    genuinely fails while another process owns it, so retry rather than crash on
    what is usually a sub-100ms collision.
    """
    for attempt in range(retries):
        if _user32.OpenClipboard(None):
            break
        if attempt == retries - 1:
            raise ClipboardError(
                f"OpenClipboard failed after {retries} attempts "
                f"(WinError {ctypes.get_last_error()}); another process is holding it"
            )
        time.sleep(delay)
    try:
        yield
    finally:
        _user32.CloseClipboard()


def _read_format(fmt: int) -> bytes | None:
    handle = _user32.GetClipboardData(fmt)
    if not handle:
        return None  # delayed rendering that never materialised
    size = _kernel32.GlobalSize(handle)
    if size == 0:
        return b""
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, size)
    finally:
        _kernel32.GlobalUnlock(handle)


def _format_name(fmt: int) -> str:
    if fmt in STANDARD_FORMAT_NAMES:
        return STANDARD_FORMAT_NAMES[fmt]
    buf = ctypes.create_unicode_buffer(256)
    n = _user32.GetClipboardFormatNameW(fmt, buf, len(buf))
    return buf.value if n else f"#{fmt}"


def dump_all() -> list[dict]:
    """Snapshot every format currently on the clipboard.

    Returns one dict per format with ``id``, ``name``, ``size`` and ``data``
    (``bytes``, or ``None`` where the format is not a readable memory block).
    """
    out: list[dict] = []
    with _clipboard():
        fmt = _user32.EnumClipboardFormats(0)
        while fmt:
            data = None if fmt in _NON_HGLOBAL_FORMATS else _read_format(fmt)
            out.append(
                {
                    "id": fmt,
                    "name": _format_name(fmt),
                    "size": len(data) if data is not None else None,
                    "data": data,
                }
            )
            fmt = _user32.EnumClipboardFormats(fmt)
    return out


def set_clipboard(payloads: dict[int, bytes]) -> None:
    """Replace the clipboard contents with ``{format_id: raw_bytes}``.

    Every payload must already carry whatever terminator its format requires.
    Memory handed to SetClipboardData becomes owned by the system: it must not be
    freed here, and it must not be freed on the *success* path even partially.
    """
    if not payloads:
        raise ValueError("refusing to set an empty clipboard payload set")

    with _clipboard():
        if not _user32.EmptyClipboard():
            raise ClipboardError(f"EmptyClipboard failed (WinError {ctypes.get_last_error()})")

        for fmt, blob in payloads.items():
            handle = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(blob))
            if not handle:
                raise ClipboardError(f"GlobalAlloc({len(blob)}) failed for format {fmt}")
            ptr = _kernel32.GlobalLock(handle)
            if not ptr:
                _kernel32.GlobalFree(handle)
                raise ClipboardError(f"GlobalLock failed for format {fmt}")
            try:
                ctypes.memmove(ptr, blob, len(blob))
            finally:
                _kernel32.GlobalUnlock(handle)

            if not _user32.SetClipboardData(fmt, handle):
                # Ownership did not transfer, so this block is still ours to release.
                _kernel32.GlobalFree(handle)
                raise ClipboardError(
                    f"SetClipboardData failed for format {fmt} "
                    f"(WinError {ctypes.get_last_error()})"
                )


# --- CF_HTML ------------------------------------------------------------------

# Every offset field is exactly 10 digits so the header's own length is constant,
# which is what makes single-pass offset computation correct. Zero-padding is
# explicitly sanctioned: "StartHTML:0000000071" appears in the Microsoft doc.
_HEADER_FIELDS = ("StartHTML", "EndHTML", "StartFragment", "EndFragment")
_PREFIX = "<html><body>\r\n<!--StartFragment-->"
_SUFFIX = "<!--EndFragment-->\r\n</body>\r\n</html>"


def wrap_cf_html(fragment: str) -> bytes:
    """Wrap an HTML fragment in a correctly-offset CF_HTML payload.

    CF_HTML is UTF-8 -- the documented exception to Windows' UTF-16 clipboard
    rule -- and every offset is a *byte* offset from the first byte of the
    payload. Computing them in characters is the classic way to produce a
    payload that every consumer silently ignores.
    """
    header_len = len(
        ("Version:0.9\r\n" + "".join(f"{k}:{0:010d}\r\n" for k in _HEADER_FIELDS)).encode("utf-8")
    )

    prefix_bytes = _PREFIX.encode("utf-8")
    fragment_bytes = fragment.encode("utf-8")
    body_len = len((_PREFIX + fragment + _SUFFIX).encode("utf-8"))

    offsets = {
        "StartHTML": header_len,
        "EndHTML": header_len + body_len,
        "StartFragment": header_len + len(prefix_bytes),
        "EndFragment": header_len + len(prefix_bytes) + len(fragment_bytes),
    }
    header = "Version:0.9\r\n" + "".join(f"{k}:{offsets[k]:010d}\r\n" for k in _HEADER_FIELDS)
    payload = (header + _PREFIX + fragment + _SUFFIX).encode("utf-8")

    assert len(header.encode("utf-8")) == header_len, "header length must not vary"
    return payload + b"\x00"


def unwrap_cf_html(payload: bytes) -> dict:
    """Parse a CF_HTML payload back into its header fields and fragment.

    Used to verify our own output and to read what Teams itself puts on the
    clipboard. Slices by the declared byte offsets rather than by the marker
    comments, so a payload whose offsets lie is caught instead of tolerated.
    """
    text = payload.rstrip(b"\x00").decode("utf-8", errors="replace")
    header: dict[str, str] = {}
    for line in text.split("\r\n"):
        if ":" not in line:
            break
        key, _, value = line.partition(":")
        if key not in {"Version", "SourceURL", *_HEADER_FIELDS, "StartSelection", "EndSelection"}:
            break
        header[key] = value

    raw = payload.rstrip(b"\x00")
    result: dict = {"header": header, "fragment": None, "html": None, "offsets_consistent": None}

    def _slice(start_key: str, end_key: str) -> str | None:
        try:
            start, end = int(header[start_key]), int(header[end_key])
        except (KeyError, ValueError):
            return None
        if start < 0 or end < 0 or start > end or end > len(raw):
            return None
        return raw[start:end].decode("utf-8", errors="replace")

    result["html"] = _slice("StartHTML", "EndHTML")
    result["fragment"] = _slice("StartFragment", "EndFragment")

    marked = None
    if "<!--StartFragment-->" in text and "<!--EndFragment-->" in text:
        marked = text.split("<!--StartFragment-->", 1)[1].rsplit("<!--EndFragment-->", 1)[0]
    result["offsets_consistent"] = (marked is not None) and (marked == result["fragment"])
    return result

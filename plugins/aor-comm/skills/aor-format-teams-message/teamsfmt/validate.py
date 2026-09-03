"""Manifest-driven validation of generated HTML.

This is the architectural safeguard, not a lint. The renderer could in principle be
wrong; capabilities.json is the evidence. Validating the renderer's OUTPUT against
the manifest means an unsupported element cannot reach the clipboard even if a bug
or a future edit introduces one -- the failure mode is a refusal, not a message that
looks wrong after you have already pasted and sent it.

Because it reads the same file SKILL.md documents, the enforced set and the
documented set cannot drift apart.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

__all__ = ["ValidationError", "load_manifest", "validate_html"]

_MANIFEST = Path(__file__).resolve().parent.parent / "capabilities.json"

# Structural children named inside a supported element's own entry.
#
# This set is the ENFORCED one. The manifest's per-element "children" lists are
# documentation only -- nothing reads them -- so a child added there and not here
# is refused at validation time with a message blaming the tool. Keep the two in
# step by hand until one of them is deleted.
_IMPLIED = {"tbody", "thead", "tr", "td", "th", "li"}


class ValidationError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def load_manifest(path: Path | None = None) -> dict:
    return json.loads((path or _MANIFEST).read_text(encoding="utf-8"))


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.seen: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag, attrs):
        self.seen.append((tag, attrs))

    handle_startendtag = handle_starttag


def validate_html(fragment: str, manifest: dict | None = None) -> None:
    """Raise ValidationError unless every element and attribute is manifest-approved."""
    manifest = manifest or load_manifest()
    supported: dict = manifest["supported"]
    rejected: dict = manifest.get("rejected", {})
    allowed_props = set(supported.get("span", {}).get("style_properties", []))

    parser = _Collector()
    parser.feed(fragment)

    problems: list[str] = []
    for tag, attrs in parser.seen:
        if tag not in supported and tag not in _IMPLIED:
            why = rejected.get(tag)
            problems.append(
                f"<{tag}> is not in the verified-supported set"
                + (f" -- {why}" if why else f" (see capabilities.json, verified {manifest['verified_on']})")
            )
            continue

        spec = supported.get(tag, {})
        permitted = set(spec.get("attrs", []))
        for name, value in attrs:
            if name not in permitted:
                problems.append(f"<{tag} {name}=...> -- attribute not permitted on <{tag}>")
                continue
            if tag == "img" and name == "src":
                # The ONE value-level check in this validator, and it earns its
                # exception: everything that constrains an image lives in the src
                # value, not in the element or attribute names this file otherwise
                # checks. An http(s) src does not degrade the message, it makes
                # Teams reject the entire paste -- and nothing here can observe
                # that, because nothing here observes Teams.
                if not (value or "").lower().startswith("data:image/"):
                    problems.append(
                        f"<img src=...> must be a data:image/ URI, not {(value or '')[:40]!r} "
                        f"-- an external src makes Teams reject the whole paste"
                    )
            if tag == "span" and name == "style":
                for decl in (value or "").split(";"):
                    if not decl.strip():
                        continue
                    prop = decl.split(":", 1)[0].strip().lower()
                    if prop not in allowed_props:
                        problems.append(
                            f"span style '{prop}' is stripped by Teams; only "
                            f"{', '.join(sorted(allowed_props))} survive"
                        )

    if problems:
        raise ValidationError(problems)

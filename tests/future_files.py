"""Test helper: SLDC files as a newer or older program would write them, for the compatibility tests of the applications that read .sldc.

Real packages are made with src.utils.sldc and then rewritten the way a newer program would (another version, extra required features,
content this reader does not know), with every checksum restored, so the container checks pass and only the unknown content is left.
Nothing here is mocked: the application under test reads these files through the real SLDC module.
"""
from __future__ import annotations

import hashlib
import io
import json
import struct
import zipfile
import zlib
from typing import Callable


def sldc():
    import importlib
    return importlib.import_module("src.utils.sldc")


def png(width: int = 40, height: int = 20) -> bytes:
    raw = b"".join(b"\x00" + bytes([200, 50, 50]) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def modern_package():
    """A 1.1 document: heading, paragraph with an inline equation, display equation, a turned/cropped/floating picture, header and footer positions."""
    s = sldc()
    asset = s.make_asset(png())
    text = "Energy ￼ is conserved."
    inline = s.make_block("paragraph", text, spans=[{"start": 7, "end": 8, "type": "equation", "equation": {"syntax": "latex", "source": "E=mc^2"}}])
    blocks = [s.make_block("heading", "Title", attributes={"level": 1}), inline,
              s.make_block("equation", "", equation={"syntax": "latex", "source": "a^2+b^2=c^2"}),
              s.make_block("figure", "", figure={"asset": asset.id, "alt": "red box", "width_mm": 60, "rotation_deg": 90, "flip_horizontal": True,
                                                  "crop": {"left": 0.1, "top": 0, "right": 1, "bottom": 1},
                                                  "placement": {"mode": "floating", "anchor": "page", "x_mm": 20, "y_mm": 30, "wrap": "none", "layer": "in_front_of_text"}}),
              s.make_block("paragraph", "After.")]
    package = s.new_package("Modern", blocks, assets=[asset],
                            sections=[{"id": "sec-1", "page": {"size": "A4", "margins_mm": {"top": 25, "right": 25, "bottom": 25, "left": 25}, "header_mm": 14, "footer_mm": 6}}])
    package.version = "1.1.0"
    return package


def rewrite(source: bytes, *, version: str, requires: tuple[str, ...] = (), edit: Callable[[dict], None] | None = None) -> bytes:
    archive = zipfile.ZipFile(io.BytesIO(source))
    parts = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(parts["manifest.json"])
    document = json.loads(parts["document.json"])
    if edit:
        edit(document)
    parts["document.json"] = json.dumps(document).encode()
    manifest["version"] = version
    manifest["requires"] = sorted(set(manifest["requires"]) | set(requires))
    for entry in manifest["parts"]:
        if entry["name"] == "document.json":
            entry["size"], entry["sha256"] = len(parts["document.json"]), hashlib.sha256(parts["document.json"]).hexdigest()
    manifest["revision_id"] = sldc()._revision_digest([(e["name"], e["size"], e["sha256"]) for e in manifest["parts"] if e["role"] in ("content", "asset")])
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        target.writestr(info, parts["mimetype"])
        target.writestr("manifest.json", json.dumps(manifest))
        for entry in manifest["parts"]:
            target.writestr(entry["name"], parts[entry["name"]])
    return out.getvalue()


def modern_bytes() -> bytes:
    return sldc().dumps_package(modern_package())


def future_requiring_bytes(edit: Callable[[dict], None] | None = None, requires: tuple[str, ...] = ("holo-layout",)) -> bytes:
    """Written by a newer program (1.2.0) that needs a feature nobody here knows. Optionally with content the schema here does not know."""
    return rewrite(modern_bytes(), version="1.2.0", requires=requires, edit=edit)


def future_additive_bytes() -> bytes:
    """Written by a newer program (1.2.0) that only added things readers may ignore: it declares nothing new as required."""
    return rewrite(modern_bytes(), version="1.2.0")


def future_major_bytes() -> bytes:
    return rewrite(modern_bytes(), version="2.0.0")


def add_unknown_content(document: dict) -> None:
    document["blocks"].append({"id": "bfuture", "type": "hologram", "text": "text of a block type from the future", "holo": {"depth": 3}})

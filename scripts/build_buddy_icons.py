#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Builds avd-buddy's app icons from the configured icon_source in buddy/app/brand.json (docs/buddy-app.md section 6).

Pure Python stdlib only (zlib + struct): no third-party image library required.
Generates into buddy/app/icons/ (git-ignored):
- icon-192.png, icon-512.png (purpose: any, transparent background allowed, RGBA)
- icon-maskable-512.png (purpose: maskable, full-bleed #0d1015 --brand-ink background, central 80% safe zone, RGB)
- icon-monochrome-512.png (purpose: monochrome, one color with alpha, RGBA)
- apple-touch-icon.png (180x180, no transparency, full-bleed #0d1015 background, RGB)
- favicon.svg, favicon-32.png (character's head or shield mark at 32px, RGBA)
- avatar.svg (chat avatar beside avd-buddy's messages)
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAND_RGBA = (28, 169, 152, 255)   # #1ca998 (--brand)
INK_RGBA = (13, 16, 21, 255)       # #0d1015 (--brand-ink)
WHITE_RGBA = (255, 255, 255, 255)  # #ffffff
CLEAR_RGBA = (0, 0, 0, 0)
SOURCE_JPG_SHA256 = "eabcf7d6e58554e0c6f3cc3e5637495c2fdc0d288898ccfc54da4788dbbcd920"


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _encode_png(width: int, height: int, rows: list[bytearray], *, rgba: bool) -> bytes:
    color_type = 6 if rgba else 2  # 6 = RGBA, 2 = RGB
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    compressed = zlib.compress(raw, level=9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )


def _dist_to_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_sq))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _render_icon_bytes(
    size: int,
    *,
    source: str,
    opaque_bg: bool,
    safe_zone_scale: float = 1.0,
    monochrome: bool = False,
) -> bytes:
    """Renders the mascot or placeholder icon in normalized 512x512 coordinates and encodes as PNG."""
    rgba = not opaque_bg
    fg = WHITE_RGBA if monochrome else BRAND_RGBA
    ink = CLEAR_RGBA if monochrome else INK_RGBA
    bg = INK_RGBA if opaque_bg else CLEAR_RGBA

    # Pre-build scanline buffer initialized to background color
    rows: list[bytearray] = []
    bg_pixel = bytes(bg if rgba else bg[:3])
    for _ in range(size):
        rows.append(bytearray(bg_pixel * size))

    inv_scale = 512.0 / (size * safe_zone_scale)
    segments = [
        # Outer paperclip vertical legs & top/bottom arcs approximated as capsule segments
        (198.0, 156.0, 198.0, 346.0),
        (296.0, 156.0, 296.0, 346.0),
        (198.0, 156.0, 296.0, 156.0),
        (198.0, 346.0, 296.0, 346.0),
        # Inner loop
        (228.0, 210.0, 228.0, 316.0),
        (268.0, 210.0, 268.0, 316.0),
        (228.0, 316.0, 268.0, 316.0),
        # Eyebrows
        (170.0, 148.0, 226.0, 142.0),
        (266.0, 146.0, 320.0, 160.0),
    ]

    for y in range(size):
        ny = 256.0 + ((y + 0.5) * 512.0 / size - 256.0) / safe_zone_scale
        row = rows[y]
        for x in range(size):
            nx = 256.0 + ((x + 0.5) * 512.0 / size - 256.0) / safe_zone_scale
            color: tuple[int, int, int, int] | None = None

            if source == "mascot":
                # Eyes (highest z-order)
                for ex, ey in ((202.0, 194.0), (282.0, 198.0)):
                    d_eye = math.hypot(nx - ex, ny - ey)
                    if d_eye <= 14.0:
                        color = CLEAR_RGBA if monochrome else INK_RGBA
                        break
                    if d_eye <= 34.0:
                        color = fg if monochrome else WHITE_RGBA
                        break
                    if d_eye <= 44.0 and not monochrome:
                        color = ink
                        break

                if color is None:
                    # Paperclip & eyebrows
                    min_d = min(_dist_to_segment(nx, ny, *seg) for seg in segments)
                    if min_d <= 9.0:
                        color = fg
                    elif min_d <= 18.0 and not monochrome:
                        color = ink

                if color is None:
                    # Shield on the right (x in [276, 402], y in [204, 374])
                    if 276.0 <= nx <= 402.0 and 204.0 <= ny <= 326.0:
                        if 294.0 <= nx <= 384.0 and 224.0 <= ny <= 294.0:
                            color = CLEAR_RGBA if monochrome else WHITE_RGBA
                        else:
                            color = fg
            else:
                # Placeholder shield mark
                if 120.0 <= nx <= 392.0 and 100.0 <= ny <= 380.0:
                    if 160.0 <= nx <= 352.0 and 140.0 <= ny <= 290.0:
                        color = CLEAR_RGBA if monochrome else INK_RGBA
                    else:
                        color = fg

            if color is not None and color != CLEAR_RGBA:
                offset = x * (4 if rgba else 3)
                row[offset : offset + (4 if rgba else 3)] = bytes(color if rgba else color[:3])
            elif color == CLEAR_RGBA and not opaque_bg:
                offset = x * 4
                row[offset : offset + 4] = b"\x00\x00\x00\x00"

    return _encode_png(size, size, rows, rgba=rgba)


def _placeholder_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="img" aria-label="avd-buddy">\n'
        '  <rect width="100" height="100" rx="18" fill="#0d1015"/>\n'
        '  <path d="M17 14H83V63L50 86L17 63Z" fill="#1ca998"/>\n'
        '  <path d="M24 20H77V57H24ZM27.5 23.5V53.5H73.5V23.5Z" fill="#0d1015" fill-rule="evenodd"/>\n'
        '  <circle cx="76" cy="24" r="14" fill="#1ca998" stroke="#0d1015" stroke-width="4"/>\n'
        '</svg>\n'
    )


def _mascot_favicon_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="avd-buddy">\n'
        '  <rect width="64" height="64" rx="14" fill="#0d1015"/>\n'
        '  <path d="M20 20C20 9 44 9 44 20V46C44 55 24 55 24 46" fill="none" stroke="#1ca998" stroke-width="6" stroke-linecap="round"/>\n'
        '  <circle cx="23" cy="28" r="9" fill="#ffffff" stroke="#0d1015" stroke-width="3"/>\n'
        '  <circle cx="24" cy="28" r="4" fill="#0d1015"/>\n'
        '  <circle cx="41" cy="29" r="9" fill="#ffffff" stroke="#0d1015" stroke-width="3"/>\n'
        '  <circle cx="40" cy="29" r="4" fill="#0d1015"/>\n'
        '  <path d="M15 17C19 13 25 13 29 15M35 16C39 14 45 15 49 18" fill="none" stroke="#1ca998" stroke-width="4" stroke-linecap="round"/>\n'
        '</svg>\n'
    )


def _rgba_png_to_rgb(png_bytes: bytes, bg: tuple[int, int, int] = (13, 16, 21)) -> bytes:
    """Flatten an 8-bit RGBA (or RGB) PNG onto a solid RGB background using pure stdlib."""
    pos = 8
    width = height = color_type = 0
    idat_parts: list[bytes] = []
    while pos < len(png_bytes):
        length = struct.unpack(">I", png_bytes[pos : pos + 4])[0]
        ctype = png_bytes[pos + 4 : pos + 8]
        data = png_bytes[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, _bit, color_type = struct.unpack(">IIBB", data[:10])
        elif ctype == b"IDAT":
            idat_parts.append(data)
    if color_type == 2:
        return png_bytes
    if color_type != 6:
        raise ValueError(f"Unsupported PNG color type {color_type}")
    raw = zlib.decompress(b"".join(idat_parts))
    bpp = 4
    stride = width * bpp
    prev = bytearray(stride)
    rows: list[bytearray] = []
    idx = 0
    for _ in range(height):
        ftype = raw[idx]
        idx += 1
        scan = bytearray(raw[idx : idx + stride])
        idx += stride
        for i in range(stride):
            a = scan[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ftype == 1:
                scan[i] = (scan[i] + a) & 0xFF
            elif ftype == 2:
                scan[i] = (scan[i] + b) & 0xFF
            elif ftype == 3:
                scan[i] = (scan[i] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                scan[i] = (scan[i] + pr) & 0xFF
        prev = scan
        rgb_row = bytearray(width * 3)
        for x in range(width):
            r, g, b, al = scan[x * 4 : x * 4 + 4]
            inv = 255 - al
            rgb_row[x * 3] = (r * al + bg[0] * inv) // 255
            rgb_row[x * 3 + 1] = (g * al + bg[1] * inv) // 255
            rgb_row[x * 3 + 2] = (b * al + bg[2] * inv) // 255
        rows.append(rgb_row)
    return _encode_png(width, height, rows, rgba=False)


def build_icons(root: Path = ROOT) -> Path:
    brand_path = root / "buddy/app/brand.json"
    if not brand_path.is_file():
        raise FileNotFoundError(f"Missing {brand_path}")
    brand = json.loads(brand_path.read_text(encoding="utf-8"))
    source = brand.get("icon_source")
    if source not in ("assistant", "mascot", "placeholder"):
        raise ValueError(f"icon_source must be 'assistant', 'mascot', or 'placeholder', got {source!r}")

    out_dir = root / "buddy/app/icons"
    out_dir.mkdir(parents=True, exist_ok=True)

    if source == "assistant":
        review_path = root / "assets/assistant/REVIEW.md"
        if not review_path.is_file():
            raise RuntimeError("icon_source is 'assistant' but assets/assistant/REVIEW.md does not exist")
        (out_dir / "icon-192.png").write_bytes((root / "assets/assistant/png/icon-pwa-192.png").read_bytes())
        (out_dir / "icon-512.png").write_bytes((root / "assets/assistant/png/icon-pwa-512.png").read_bytes())
        (out_dir / "icon-maskable-512.png").write_bytes(_rgba_png_to_rgb((root / "assets/assistant/png/icon-pwa-512.png").read_bytes()))
        (out_dir / "icon-monochrome-512.png").write_bytes((root / "assets/assistant/png/icon-monochrome-white-512.png").read_bytes())
        (out_dir / "apple-touch-icon.png").write_bytes(_rgba_png_to_rgb((root / "assets/assistant/png/apple-touch-icon-180.png").read_bytes()))
        (out_dir / "favicon-32.png").write_bytes((root / "assets/assistant/png/favicon-32.png").read_bytes())
        (out_dir / "favicon.svg").write_text((root / "assets/assistant/svg/favicon-32.svg").read_text(encoding="utf-8"), encoding="utf-8")
        (out_dir / "avatar.svg").write_text((root / "assets/assistant/svg/avatar-chat.svg").read_text(encoding="utf-8"), encoding="utf-8")
        return out_dir

    if source == "mascot":
        review_path = root / "assets/buddy/REVIEW.md"
        if not review_path.is_file():
            raise RuntimeError("icon_source is 'mascot' but assets/buddy/REVIEW.md does not exist (docs/buddy-app.md section 6.2)")
        jpg_path = root / "assets/buddy/avd-buddy-icon-source.jpg"
        if jpg_path.is_file():
            digest = hashlib.sha256(jpg_path.read_bytes()).hexdigest()
            if digest != SOURCE_JPG_SHA256:
                raise RuntimeError(f"assets/buddy/avd-buddy-icon-source.jpg SHA-256 mismatch: {digest}")
        svg_path = root / "assets/buddy/avd-buddy-icon.svg"
        if not svg_path.is_file():
            raise FileNotFoundError(f"Missing {svg_path}")
        avatar_svg = svg_path.read_text(encoding="utf-8")
        favicon_svg = _mascot_favicon_svg()
    else:
        avatar_svg = _placeholder_svg()
        favicon_svg = _placeholder_svg()

    out_dir = root / "buddy/app/icons"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "icon-192.png").write_bytes(
        _render_icon_bytes(192, source=source, opaque_bg=False, safe_zone_scale=0.92)
    )
    (out_dir / "icon-512.png").write_bytes(
        _render_icon_bytes(512, source=source, opaque_bg=False, safe_zone_scale=0.92)
    )
    (out_dir / "icon-maskable-512.png").write_bytes(
        _render_icon_bytes(512, source=source, opaque_bg=True, safe_zone_scale=0.76)
    )
    (out_dir / "icon-monochrome-512.png").write_bytes(
        _render_icon_bytes(512, source=source, opaque_bg=False, safe_zone_scale=0.86, monochrome=True)
    )
    (out_dir / "apple-touch-icon.png").write_bytes(
        _render_icon_bytes(180, source=source, opaque_bg=True, safe_zone_scale=0.84)
    )
    (out_dir / "favicon-32.png").write_bytes(
        _render_icon_bytes(32, source=source, opaque_bg=False, safe_zone_scale=0.95)
    )
    (out_dir / "favicon.svg").write_text(favicon_svg, encoding="utf-8")
    (out_dir / "avatar.svg").write_text(avatar_svg, encoding="utf-8")

    return out_dir


def main(argv: list[str]) -> int:
    try:
        out_dir = build_icons(ROOT)
    except Exception as exc:
        print(f"build_buddy_icons: {exc}", file=sys.stderr)
        return 1
    print(f"Generated 8 icon files in {out_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

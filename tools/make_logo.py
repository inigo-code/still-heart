"""Generate the Stills brand mark from scratch.

Draws a clean, self-contained app icon on a violet gradient tile and writes:

  web/logo.png     1024x1024 master icon (full illustration)
  web/stills.ico   multi-size Windows icon

Icons are rendered per size, not downscaled from one big image: small sizes
(<=32px, used by the taskbar / Explorer) get a bold, simplified photo glyph so
they stay legible, while large sizes get the full illustration (stacked photo
cards + scene + download badge). Everything is drawn with Pillow, supersampled
for crisp anti-aliased edges. No external image is needed.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PNG = WEB / "logo.png"
ICO = WEB / "stills.ico"

# Brand palette — Aurora teal
VIOLET_TOP = (45, 212, 191)      # #2dd4bf  (kept var names; now teal)
VIOLET_BOTTOM = (13, 148, 136)   # #0d9488
INDIGO = (15, 118, 110)          # #0f766e  (download badge)
CARD_WHITE = (255, 255, 255)
CARD_BACK_1 = (196, 245, 238)    # #c4f5ee
CARD_BACK_2 = (156, 233, 222)    # #9ce9de
SUN = (255, 200, 84)             # #ffc854  (warm pop against teal)
HILL_FRONT = (13, 148, 136)      # #0d9488
HILL_BACK = (45, 212, 191)       # #2dd4bf

SS = 4  # supersample factor


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    px = grad.load()
    for y in range(size):
        px[0, y] = _lerp(top, bottom, y / max(1, size - 1))
    return grad.resize((size, size))


def _rounded_mask(size: int, box: tuple[float, float, float, float], radius: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    return mask


def _tile(S: int, u) -> tuple[Image.Image, Image.Image]:
    """Return (base image with gradient tile + sheen, tile mask)."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile_box = (u(40), u(40), u(984), u(984))
    tile_radius = u(232)
    grad = _vertical_gradient(S, VIOLET_TOP, VIOLET_BOTTOM).convert("RGBA")
    tile_mask = _rounded_mask(S, tile_box, tile_radius)
    img.paste(grad, (0, 0), tile_mask)
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).ellipse((u(60), u(-360), u(964), u(360)), fill=(255, 255, 255, 34))
    img.alpha_composite(Image.composite(sheen, Image.new("RGBA", (S, S), (0, 0, 0, 0)), tile_mask))
    return img, tile_mask


def build(size: int = 1024, detail: str = "full") -> Image.Image:
    S = size * SS

    def u(v: float) -> float:  # 1024-space -> supersampled px
        return v * (S / 1024)

    img, _ = _tile(S, u)
    draw = ImageDraw.Draw(img)

    def rrect(box, radius, fill, outline=None, width=0):
        draw.rounded_rectangle(
            [u(box[0]), u(box[1]), u(box[2]), u(box[3])],
            radius=u(radius), fill=fill, outline=outline, width=round(u(width)) if width else 0,
        )

    if detail == "simple":
        # One bold, centred photo card — reads at 16px.
        card = (250, 288, 774, 736)
        card_radius = 74
        rrect(card, card_radius, CARD_WHITE)
        scene = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(scene)
        sdraw.ellipse((u(338), u(360), u(430), u(452)), fill=SUN)               # sun
        sdraw.polygon([(u(250), u(736)), (u(470), u(486)), (u(624), u(690)),    # back hill
                       (u(624), u(736))], fill=HILL_BACK)
        sdraw.polygon([(u(430), u(736)), (u(624), u(520)), (u(774), u(736))],   # front hill
                      fill=HILL_FRONT)
        card_mask = _rounded_mask(S, (u(card[0]), u(card[1]), u(card[2]), u(card[3])), u(card_radius))
        img.paste(scene, (0, 0), Image.composite(scene.split()[3], Image.new("L", (S, S), 0), card_mask))
        return img.resize((size, size), Image.Resampling.LANCZOS)

    # --- full illustration --------------------------------------------------
    rrect((214, 196, 648, 512), 48, CARD_BACK_2)   # stacked back cards
    rrect((252, 224, 700, 552), 50, CARD_BACK_1)

    card = (296, 258, 748, 604)
    card_radius = 52
    rrect(card, card_radius, CARD_WHITE)

    scene = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(scene)
    sdraw.ellipse((u(360), u(316), u(432), u(388)), fill=SUN)
    sdraw.polygon([(u(300), u(604)), (u(470), u(430)), (u(590), u(560)), (u(590), u(604))], fill=HILL_BACK)
    sdraw.polygon([(u(430), u(604)), (u(590), u(452)), (u(748), u(604))], fill=HILL_FRONT)
    card_mask = _rounded_mask(S, (u(card[0]), u(card[1]), u(card[2]), u(card[3])), u(card_radius))
    img.paste(scene, (0, 0), Image.composite(scene.split()[3], Image.new("L", (S, S), 0), card_mask))

    bc = (712, 636)  # download badge
    br = 118
    draw.ellipse((u(bc[0] - br), u(bc[1] - br), u(bc[0] + br), u(bc[1] + br)),
                 fill=INDIGO, outline=CARD_WHITE, width=round(u(20)))
    draw.line((u(bc[0]), u(bc[1] - 54), u(bc[0]), u(bc[1] + 24)), fill=CARD_WHITE, width=round(u(26)))
    draw.polygon([(u(bc[0] - 46), u(bc[1] - 2)), (u(bc[0] + 46), u(bc[1] - 2)), (u(bc[0]), u(bc[1] + 46))],
                 fill=CARD_WHITE)
    draw.line((u(bc[0] - 52), u(bc[1] + 74), u(bc[0] + 52), u(bc[1] + 74)), fill=CARD_WHITE, width=round(u(22)))

    return img.resize((size, size), Image.Resampling.LANCZOS)


def _write_ico(path: Path, images: list[Image.Image]) -> None:
    """Write a real multi-frame ICO with PNG-compressed frames (Vista+).

    Pillow's own ICO writer resizes one source image to every size; we want a
    distinct render per size, so we assemble the ICO container by hand.
    """
    blobs = []
    for im in images:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        blobs.append(buf.getvalue())
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)  # reserved, type=icon, count
    entries = b""
    offset = 6 + 16 * count
    for im, blob in zip(images, blobs):
        w = 0 if im.width >= 256 else im.width
        h = 0 if im.height >= 256 else im.height
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(header + entries + b"".join(blobs))


def main() -> None:
    WEB.mkdir(parents=True, exist_ok=True)
    build(1024, "full").save(PNG, "PNG")
    # Per-size ICO frames: a simplified glyph for the small sizes Windows shows
    # in the taskbar / Explorer, the full illustration where there is room.
    frames = [
        build(16, "simple"),
        build(24, "simple"),
        build(32, "simple"),
        build(48, "simple"),
        build(64, "full"),
        build(128, "full"),
        build(256, "full"),
    ]
    _write_ico(ICO, frames)
    print(f"wrote {PNG}")
    print(f"wrote {ICO}")


if __name__ == "__main__":
    main()

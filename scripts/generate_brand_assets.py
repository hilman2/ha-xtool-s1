"""Generate the integration brand assets (icon + logo) with Pillow.

The output PNGs are committed to ``custom_components/xtool_s1/brand/``,
which is where Home Assistant 2026.3+ reads custom integration brands
from. Re-run this script if you ever want to tweak the artwork.

Sizes follow the home-assistant/brands rules:

* icon.png      256x256
* icon@2x.png   512x512
* logo.png      640x256  (landscape, shortest side >= 128)
* logo@2x.png   1280x512 (shortest side >= 256)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "xtool_s1" / "brand"
)
OUT_DIR.mkdir(parents=True, exist_ok=True)

BG = (24, 24, 28, 255)
LASER = (255, 96, 32, 255)
LASER_GLOW = (255, 140, 60, 110)
WHITE = (245, 245, 245, 255)
ACCENT = (190, 190, 195, 255)
HEAD = (210, 210, 215, 255)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/mnt/c/Windows/Fonts/arialbd.ttf",
        "/mnt/c/Windows/Fonts/segoeuib.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rounded_bg(size: tuple[int, int], radius_ratio: float = 0.16) -> Image.Image:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = int(min(size) * radius_ratio)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=BG)
    return img


def _draw_laser(
    img: Image.Image,
    *,
    bbox: tuple[int, int, int, int],
) -> None:
    """Draw a centered laser-head + vertical beam + workpiece inside ``bbox``.

    Layout (within the box):
        +---------------------+
        |   [HEAD]            |  ← head at top-center
        |     |               |  ← vertical beam
        |     |               |
        |     ◉               |  ← spark
        |  ─────────          |  ← workpiece line
        +---------------------+
    """
    x0, y0, x1, y1 = bbox
    w = x1 - x0
    h = y1 - y0
    cx = (x0 + x1) // 2

    head_w = max(6, int(w * 0.42))
    head_h = max(4, int(h * 0.13))
    head_x0 = cx - head_w // 2
    head_y0 = y0 + int(h * 0.05)

    beam_w = max(3, int(w * 0.06))
    beam_top = head_y0 + head_h
    workpiece_y = y0 + int(h * 0.78)
    spark_r = max(3, int(w * 0.06))

    workpiece_w = int(w * 0.62)
    workpiece_x0 = cx - workpiece_w // 2
    workpiece_x1 = cx + workpiece_w // 2

    # ---------- glow layer (Gaussian-blurred orange beam) ----------
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.rectangle(
        (cx - beam_w * 2, beam_top, cx + beam_w * 2, workpiece_y),
        fill=LASER_GLOW,
    )
    gdraw.ellipse(
        (
            cx - spark_r * 2,
            workpiece_y - spark_r * 2,
            cx + spark_r * 2,
            workpiece_y + spark_r * 2,
        ),
        fill=LASER_GLOW,
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(2, w // 32)))
    img.alpha_composite(glow)

    draw = ImageDraw.Draw(img)

    # ---------- head ----------
    draw.rounded_rectangle(
        (head_x0, head_y0, head_x0 + head_w, head_y0 + head_h),
        radius=max(2, head_h // 3),
        fill=HEAD,
    )
    # head lens
    lens_r = head_h // 3
    draw.ellipse(
        (
            cx - lens_r,
            head_y0 + head_h - lens_r // 2,
            cx + lens_r,
            head_y0 + head_h + lens_r * 3 // 2,
        ),
        fill=LASER,
    )

    # ---------- beam ----------
    draw.rectangle(
        (cx - beam_w // 2, beam_top, cx + beam_w // 2, workpiece_y),
        fill=LASER,
    )

    # ---------- spark ----------
    draw.ellipse(
        (cx - spark_r, workpiece_y - spark_r, cx + spark_r, workpiece_y + spark_r),
        fill=(255, 200, 80, 255),
    )

    # ---------- workpiece ----------
    line_h = max(3, h // 30)
    draw.rectangle(
        (
            workpiece_x0,
            workpiece_y + spark_r - line_h // 2,
            workpiece_x1,
            workpiece_y + spark_r + line_h // 2,
        ),
        fill=ACCENT,
    )


def make_icon(size: int) -> Image.Image:
    """Square icon: laser motif on top, ``S1`` text underneath."""
    img = _rounded_bg((size, size))
    pad = int(size * 0.10)

    # Laser fills the upper ~70 % of the canvas; "S1" sits in the lower 30 %.
    laser_box = (pad, pad, size - pad, int(size * 0.72))
    _draw_laser(img, bbox=laser_box)

    draw = ImageDraw.Draw(img)
    label = "S1"
    font = _font(int(size * 0.22))
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_x = (size - text_w) // 2 - bbox[0]
    text_y = int(size * 0.78) - bbox[1]
    # subtle shadow for contrast against the dark bg
    draw.text((text_x + 1, text_y + 1), label, font=font, fill=(0, 0, 0, 180))
    draw.text((text_x, text_y), label, font=font, fill=WHITE)
    return img


def make_logo(width: int, height: int) -> Image.Image:
    """Landscape logo: laser motif on the left, wordmark on the right."""
    img = _rounded_bg((width, height), radius_ratio=0.20)

    pad = int(height * 0.10)
    icon_box = (pad, pad, height - pad, height - pad)
    _draw_laser(img, bbox=icon_box)

    draw = ImageDraw.Draw(img)
    text_main = "xTool S1"
    text_sub = "Home Assistant"

    # Choose a font size that lets the wordmark fit the available width.
    available_w = width - height - pad
    main_size = int(height * 0.34)
    main_font = _font(main_size)
    while main_size > 20:
        bbox = draw.textbbox((0, 0), text_main, font=main_font)
        if bbox[2] - bbox[0] <= available_w - pad:
            break
        main_size -= 2
        main_font = _font(main_size)

    sub_font = _font(int(main_size * 0.45))

    main_bbox = draw.textbbox((0, 0), text_main, font=main_font)
    sub_bbox = draw.textbbox((0, 0), text_sub, font=sub_font)

    text_x = height + pad // 2
    main_y = int(height * 0.30) - main_bbox[1]
    sub_y = int(height * 0.62) - sub_bbox[1]
    draw.text((text_x, main_y), text_main, font=main_font, fill=WHITE)
    draw.text((text_x, sub_y), text_sub, font=sub_font, fill=ACCENT)
    return img


def main() -> None:
    icon_1x = make_icon(256)
    icon_2x = make_icon(512)
    logo_1x = make_logo(640, 256)
    logo_2x = make_logo(1280, 512)

    icon_1x.save(OUT_DIR / "icon.png", optimize=True)
    icon_2x.save(OUT_DIR / "icon@2x.png", optimize=True)
    logo_1x.save(OUT_DIR / "logo.png", optimize=True)
    logo_2x.save(OUT_DIR / "logo@2x.png", optimize=True)

    for path in sorted(OUT_DIR.iterdir()):
        if path.suffix == ".png":
            relative = path.relative_to(OUT_DIR.parents[2])
            print(f"  wrote {relative} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

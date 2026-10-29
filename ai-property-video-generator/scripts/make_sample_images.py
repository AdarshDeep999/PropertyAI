"""Generate the six synthetic property illustrations shipped in sample_data/images.

These are deliberately *illustrations*, not photographs: no licence issues, no
real addresses, fully reproducible from this script. They are drawn to be
plausible 16:9 property shots so the demo video looks like a real listing.

Usage:  python scripts/make_sample_images.py [--out sample_data/images]
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

W, H = 2400, 1350


# --------------------------------------------------------------------------- #
# Drawing helpers
# --------------------------------------------------------------------------- #
def gradient(size, stops):
    """Vertical gradient from a list of (position 0..1, (r,g,b))."""
    width, height = size
    image = Image.new("RGB", (1, height))
    pixels = image.load()
    for y in range(height):
        ratio = y / max(1, height - 1)
        for index in range(len(stops) - 1):
            p0, c0 = stops[index]
            p1, c1 = stops[index + 1]
            if p0 <= ratio <= p1:
                span = max(1e-6, p1 - p0)
                t = (ratio - p0) / span
                pixels[0, y] = tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))
                break
        else:
            pixels[0, y] = stops[-1][1]
    return image.resize((width, height), Image.BILINEAR)


def soft_shadow(canvas, box, radius=24, blur=22, alpha=80, offset=(0, 16)):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    shifted = [box[0] + offset[0], box[1] + offset[1], box[2] + offset[0], box[3] + offset[1]]
    draw.rounded_rectangle(shifted, radius=radius, fill=(0, 0, 0, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(layer)


def glow(canvas, centre, radius, colour=(255, 214, 150), alpha=110):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.ellipse([centre[0] - radius, centre[1] - radius,
                  centre[0] + radius, centre[1] + radius], fill=colour + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.65))
    canvas.alpha_composite(layer)


def vignette(canvas, strength=90):
    width, height = canvas.size
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse([-width * 0.25, -height * 0.3, width * 1.25, height * 1.3], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(min(width, height) * 0.12))
    dark = Image.new("RGBA", (width, height), (8, 12, 20, strength))
    inverted = Image.eval(mask, lambda value: 255 - value)
    canvas.alpha_composite(Image.composite(dark, Image.new("RGBA", (width, height), (0, 0, 0, 0)),
                                           inverted))


def floor(canvas, draw, horizon_y, colour=(196, 172, 140)):
    draw.rectangle([0, horizon_y, W, H], fill=colour)
    for index in range(10):
        y = horizon_y + (H - horizon_y) * (index / 9) ** 1.35
        shade = max(0, 26 - index * 2)
        draw.line([(0, y), (W, y)], fill=(colour[0] - shade, colour[1] - shade, colour[2] - shade), width=2)


def window_view(draw, box, sky_top=(122, 168, 198), sky_bottom=(196, 220, 232), sea=False):
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=sky_bottom)
    steps = 40
    for i in range(steps):
        ratio = i / steps
        colour = tuple(int(sky_top[c] + (sky_bottom[c] - sky_top[c]) * ratio) for c in range(3))
        draw.rectangle([x0, y0 + (y1 - y0) * ratio, x1, y0 + (y1 - y0) * (ratio + 1 / steps) + 1],
                       fill=colour)
    if sea:
        sea_y = y0 + (y1 - y0) * 0.62
        draw.rectangle([x0, sea_y, x1, y1], fill=(112, 154, 172))
        for i in range(14):
            yy = sea_y + (y1 - sea_y) * (i / 14) ** 1.4
            draw.line([(x0, yy), (x1, yy)], fill=(134, 176, 192), width=2)


def tree(draw, x, base_y, height, trunk=(92, 66, 46), leaf=(72, 104, 74)):
    draw.rectangle([x - height * 0.035, base_y - height * 0.55, x + height * 0.035, base_y], fill=trunk)
    for index, (dx, dy, scale) in enumerate([(0, -0.66, 1.0), (-0.16, -0.52, 0.78), (0.17, -0.55, 0.82)]):
        r = height * 0.26 * scale
        shade = tuple(max(0, c - index * 6) for c in leaf)
        draw.ellipse([x + height * dx - r, base_y + height * (dy - 0.12) - r,
                      x + height * dx + r, base_y + height * (dy - 0.12) + r], fill=shade)


def plant(draw, x, base_y, scale=1.0, pot=(168, 130, 100)):
    pw, ph = 70 * scale, 78 * scale
    draw.polygon([(x - pw / 2, base_y - ph), (x + pw / 2, base_y - ph),
                  (x + pw * 0.38, base_y), (x - pw * 0.38, base_y)], fill=pot)
    for angle in (-58, -30, 0, 28, 56):
        rad = math.radians(angle - 90)
        length = 120 * scale
        tip = (x + math.cos(rad) * length, base_y - ph + math.sin(rad) * length)
        draw.line([(x, base_y - ph), tip], fill=(58, 92, 62), width=int(7 * scale))
        r = 20 * scale
        draw.ellipse([tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r], fill=(70, 108, 72))


# --------------------------------------------------------------------------- #
# Scenes
# --------------------------------------------------------------------------- #
def exterior():
    canvas = Image.new("RGBA", (W, H))
    canvas.paste(gradient((W, H), [
        (0.0, (249, 205, 168)), (0.35, (250, 222, 193)), (0.55, (236, 226, 208)),
        (0.56, (146, 178, 190)), (0.78, (104, 141, 158)), (1.0, (78, 112, 130)),
    ]).convert("RGBA"), (0, 0))
    draw = ImageDraw.Draw(canvas)

    glow(canvas, (W * 0.78, H * 0.30), 210, (255, 226, 178), 120)
    draw.ellipse([W * 0.755, H * 0.245, W * 0.805, H * 0.355], fill=(255, 236, 198))
    draw.polygon([(0, H * 0.58), (W * 0.16, H * 0.50), (W * 0.30, H * 0.57),
                  (W * 0.44, H * 0.52), (W * 0.62, H * 0.58), (W, H * 0.55), (W, H * 0.62), (0, H * 0.64)],
                 fill=(112, 128, 128))

    # paving
    draw.rectangle([0, H * 0.80, W, H], fill=(214, 202, 188))
    for i in range(7):
        y = H * 0.80 + (H * 0.20) * (i / 7)
        draw.line([(0, y), (W, y)], fill=(198, 186, 172), width=3)

    # main building
    body = [W * 0.20, H * 0.20, W * 0.72, H * 0.82]
    soft_shadow(canvas, body, radius=6, blur=28, alpha=70, offset=(0, 22))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(body, fill=(243, 237, 228))
    draw.rectangle([body[0], body[1], body[2], body[1] + H * 0.035], fill=(72, 78, 86))

    # window grid with warm interior light
    cols, rows = 4, 3
    for row in range(rows):
        for col in range(cols):
            x0 = body[0] + W * 0.035 + col * W * 0.108
            y0 = body[1] + H * 0.09 + row * H * 0.19
            w, h = W * 0.072, H * 0.125
            draw.rectangle([x0, y0, x0 + w, y0 + h], fill=(74, 94, 108))
            draw.rectangle([x0 + 6, y0 + 6, x0 + w - 6, y0 + h - 6],
                           fill=(238, 196, 132) if (row + col) % 3 == 0 else (108, 132, 146))
            draw.line([(x0 + w / 2, y0), (x0 + w / 2, y0 + h)], fill=(64, 80, 92), width=4)

    # balcony rails
    for row in range(rows):
        y = body[1] + H * 0.235 + row * H * 0.19
        draw.rectangle([body[0] + W * 0.025, y, body[2] - W * 0.025, y + 10], fill=(214, 206, 196))
        for i in range(18):
            x = body[0] + W * 0.03 + i * (body[2] - body[0] - W * 0.06) / 17
            draw.line([(x, y), (x, y + H * 0.028)], fill=(206, 198, 188), width=3)

    # side wing
    wing = [W * 0.72, H * 0.36, W * 0.86, H * 0.82]
    soft_shadow(canvas, wing, radius=4, blur=18, alpha=60, offset=(0, 14))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(wing, fill=(232, 224, 213))
    for row in range(2):
        for col in range(2):
            x0 = wing[0] + W * 0.018 + col * W * 0.052
            y0 = wing[1] + H * 0.06 + row * H * 0.17
            draw.rectangle([x0, y0, x0 + W * 0.034, y0 + H * 0.10], fill=(238, 200, 140))

    # landscaping
    draw.rectangle([0, H * 0.82, W, H * 0.86], fill=(88, 112, 84))
    tree(draw, W * 0.12, H * 0.84, H * 0.42)
    tree(draw, W * 0.90, H * 0.85, H * 0.36)
    for i in range(9):
        x = W * 0.03 + i * W * 0.045
        draw.ellipse([x, H * 0.80, x + W * 0.05, H * 0.84], fill=(78, 102, 76))

    vignette(canvas, 70)
    return canvas.convert("RGB")


def living_room():
    canvas = Image.new("RGBA", (W, H))
    canvas.paste(gradient((W, H), [(0.0, (246, 240, 231)), (1.0, (228, 216, 200))]).convert("RGBA"), (0, 0))
    draw = ImageDraw.Draw(canvas)
    floor(canvas, draw, H * 0.68, (198, 168, 132))

    # window
    window = [W * 0.52, H * 0.10, W * 0.93, H * 0.66]
    soft_shadow(canvas, window, radius=4, blur=18, alpha=55, offset=(0, 10))
    draw = ImageDraw.Draw(canvas)
    window_view(draw, window, (150, 188, 210), (215, 232, 238), sea=True)
    draw.rectangle(window, outline=(58, 58, 56), width=16)
    for i in range(1, 3):
        x = window[0] + (window[2] - window[0]) * i / 3
        draw.line([(x, window[1]), (x, window[3])], fill=(58, 58, 56), width=10)
    # light spill on the floor
    draw.polygon([(window[0], window[3]), (window[2], window[3]),
                  (W * 0.98, H), (W * 0.42, H)], fill=(236, 224, 202))

    # rug
    draw.ellipse([W * 0.10, H * 0.78, W * 0.72, H * 0.97], fill=(214, 197, 172))
    draw.ellipse([W * 0.14, H * 0.80, W * 0.68, H * 0.94], outline=(198, 178, 150), width=8)

    # sofa
    sofa = [W * 0.10, H * 0.52, W * 0.50, H * 0.85]
    soft_shadow(canvas, sofa, radius=26, blur=26, alpha=75, offset=(0, 20))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([sofa[0], sofa[1] + H * 0.10, sofa[2], sofa[3]], radius=26, fill=(176, 158, 136))
    draw.rounded_rectangle([sofa[0], sofa[1], sofa[2], sofa[1] + H * 0.22], radius=26, fill=(190, 172, 150))
    for i in range(3):
        x0 = sofa[0] + W * 0.02 + i * W * 0.117
        draw.rounded_rectangle([x0, sofa[1] + H * 0.13, x0 + W * 0.105, sofa[1] + H * 0.30],
                               radius=18, fill=(203, 186, 165))
    draw.rounded_rectangle([sofa[0] + W * 0.30, sofa[1] + H * 0.08, sofa[0] + W * 0.375,
                            sofa[1] + H * 0.21], radius=14, fill=(206, 174, 126))
    draw.rectangle([sofa[0] + W * 0.02, sofa[3] - 6, sofa[2] - W * 0.02, sofa[3]], fill=(120, 106, 90))

    # coffee table
    table = [W * 0.24, H * 0.86, W * 0.46, H * 0.93]
    soft_shadow(canvas, table, radius=10, blur=16, alpha=60, offset=(0, 10))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(table, radius=10, fill=(138, 104, 72))
    draw.rounded_rectangle([table[0] + 8, table[1] + 6, table[2] - 8, table[1] + 18], radius=6,
                           fill=(158, 122, 88))

    # pendant
    draw.line([(W * 0.36, 0), (W * 0.36, H * 0.18)], fill=(60, 60, 60), width=5)
    draw.polygon([(W * 0.325, H * 0.18), (W * 0.395, H * 0.18), (W * 0.372, H * 0.26),
                  (W * 0.348, H * 0.26)], fill=(62, 62, 62))
    glow(canvas, (W * 0.36, H * 0.28), 90, (255, 226, 170), 90)

    # plant + side unit
    draw.rectangle([W * 0.86, H * 0.74, W * 0.99, H * 0.93], fill=(126, 108, 88))
    draw.rectangle([W * 0.855, H * 0.72, W * 0.995, H * 0.75], fill=(148, 128, 106))
    plant(draw, W * 0.90, H * 0.74, 1.25)

    vignette(canvas, 55)
    return canvas.convert("RGB")


def kitchen():
    canvas = Image.new("RGBA", (W, H))
    canvas.paste(gradient((W, H), [(0.0, (242, 238, 231)), (0.62, (232, 226, 216))]).convert("RGBA"), (0, 0))
    draw = ImageDraw.Draw(canvas)
    floor(canvas, draw, H * 0.72, (206, 196, 182))
    # splashback tiles
    for row in range(5):
        for col in range(14):
            x0 = W * 0.02 + col * W * 0.070
            y0 = H * 0.26 + row * H * 0.045
            draw.rectangle([x0, y0, x0 + W * 0.066, y0 + H * 0.041],
                           fill=(236, 233, 226), outline=(219, 215, 206), width=2)

    # upper cabinets
    draw.rectangle([W * 0.02, H * 0.10, W * 0.44, H * 0.25], fill=(52, 62, 72))
    for i in range(3):
        x0 = W * 0.03 + i * W * 0.137
        draw.rectangle([x0, H * 0.11, x0 + W * 0.128, H * 0.24], outline=(74, 86, 98), width=4)
        draw.line([(x0 + W * 0.115, H * 0.17), (x0 + W * 0.115, H * 0.19)], fill=(196, 200, 205), width=6)

    # window
    window = [W * 0.60, H * 0.09, W * 0.97, H * 0.42]
    window_view(draw, window, (152, 190, 212), (214, 232, 238), sea=False)
    draw.rectangle(window, outline=(62, 62, 60), width=14)
    draw.line([(W * 0.785, window[1]), (W * 0.785, window[3])], fill=(62, 62, 60), width=10)

    # base cabinets + worktop
    draw.rectangle([W * 0.02, H * 0.55, W * 0.53, H * 0.80], fill=(48, 58, 68))
    draw.rectangle([W * 0.015, H * 0.52, W * 0.535, H * 0.555], fill=(228, 221, 208))
    for i in range(4):
        x0 = W * 0.03 + i * W * 0.125
        draw.rectangle([x0, H * 0.57, x0 + W * 0.117, H * 0.79], outline=(66, 78, 90), width=4)
        draw.line([(x0 + W * 0.108, H * 0.66), (x0 + W * 0.108, H * 0.70)], fill=(198, 202, 206), width=5)

    # sink + tap
    draw.rounded_rectangle([W * 0.20, H * 0.525, W * 0.32, H * 0.555], radius=6, fill=(196, 200, 204))
    draw.line([(W * 0.255, H * 0.52), (W * 0.255, H * 0.475)], fill=(160, 166, 172), width=8)
    draw.line([(W * 0.255, H * 0.477), (W * 0.285, H * 0.477)], fill=(160, 166, 172), width=8)

    # island
    island = [W * 0.46, H * 0.72, W * 0.88, H * 0.93]
    soft_shadow(canvas, island, radius=10, blur=24, alpha=80, offset=(0, 18))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([island[0], island[1], island[2], island[3]], fill=(46, 56, 66))
    draw.rectangle([island[0] - 12, island[1] - 16, island[2] + 12, island[1] + 8], fill=(232, 226, 214))
    for i in range(2):
        x = island[0] + W * 0.10 + i * W * 0.18
        draw.rounded_rectangle([x, island[3] + 12, x + W * 0.075, island[3] + 42], radius=12,
                               fill=(150, 126, 98))
        draw.line([(x + W * 0.037, island[3] + 42), (x + W * 0.037, H * 0.99)],
                  fill=(120, 100, 78), width=8)

    # pendant lights — kept clear of the window so the drawing stays legible
    for i in range(3):
        x = W * 0.455 + i * W * 0.045
        draw.line([(x, 0), (x, H * 0.13)], fill=(58, 58, 58), width=4)
        draw.polygon([(x - 26, H * 0.13), (x + 26, H * 0.13), (x + 15, H * 0.18), (x - 15, H * 0.18)],
                     fill=(58, 58, 58))
        glow(canvas, (x, H * 0.19), 55, (255, 224, 168), 95)

    # herbs on the counter
    for i in range(3):
        x = W * 0.40 + i * W * 0.035
        draw.rectangle([x, H * 0.485, x + 26, H * 0.52], fill=(176, 140, 108))
        draw.ellipse([x - 8, H * 0.445, x + 34, H * 0.49], fill=(74, 112, 74))

    vignette(canvas, 55)
    return canvas.convert("RGB")


def bedroom():
    canvas = Image.new("RGBA", (W, H))
    canvas.paste(gradient((W, H), [(0.0, (238, 232, 223)), (1.0, (226, 217, 205))]).convert("RGBA"), (0, 0))
    draw = ImageDraw.Draw(canvas)
    floor(canvas, draw, H * 0.74, (186, 156, 122))

    # window with sheer curtain
    window = [W * 0.64, H * 0.10, W * 0.96, H * 0.56]
    window_view(draw, window, (154, 188, 208), (219, 232, 238), sea=True)
    draw.rectangle(window, outline=(70, 68, 64), width=12)
    draw.line([(W * 0.80, window[1]), (W * 0.80, window[3])], fill=(70, 68, 64), width=8)
    curtain = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(curtain)
    for x0, x1 in ((W * 0.595, W * 0.715), (W * 0.925, W * 0.998)):
        cdraw.rectangle([x0, H * 0.06, x1, H * 0.74], fill=(248, 244, 236, 96))
        for fold in range(4):
            fx = x0 + (x1 - x0) * (fold + 1) / 5
            cdraw.line([(fx, H * 0.07), (fx, H * 0.73)], fill=(236, 230, 220, 70), width=6)
    canvas.alpha_composite(curtain)

    # headboard + bed
    headboard = [W * 0.10, H * 0.30, W * 0.56, H * 0.62]
    soft_shadow(canvas, headboard, radius=14, blur=22, alpha=70, offset=(0, 14))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(headboard, radius=16, fill=(138, 118, 98))
    for i in range(6):
        x = headboard[0] + 20 + i * (headboard[2] - headboard[0] - 40) / 5
        draw.line([(x, headboard[1] + 14), (x, headboard[3] - 14)], fill=(122, 104, 86), width=4)

    bed = [W * 0.08, H * 0.56, W * 0.60, H * 0.88]
    soft_shadow(canvas, bed, radius=18, blur=26, alpha=85, offset=(0, 20))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(bed, radius=18, fill=(242, 238, 230))
    draw.rounded_rectangle([bed[0] + 10, bed[1] + 10, bed[2] - 10, bed[1] + H * 0.12], radius=12,
                           fill=(250, 247, 241))
    for i in range(2):
        x0 = bed[0] + W * 0.04 + i * W * 0.22
        draw.rounded_rectangle([x0, bed[1] + 18, x0 + W * 0.19, bed[1] + H * 0.115], radius=12,
                               fill=(255, 253, 248))
    draw.rounded_rectangle([bed[0] + 12, bed[1] + H * 0.20, bed[2] - 12, bed[3] - 10], radius=10,
                           fill=(224, 206, 184))
    draw.rounded_rectangle([bed[0] + 40, bed[1] + H * 0.20, bed[0] + W * 0.20, bed[3] - 20],
                           radius=8, fill=(206, 176, 140))

    # nightstands + lamps
    for x in (W * 0.005, W * 0.60):
        draw.rectangle([x, H * 0.70, x + W * 0.075, H * 0.90], fill=(120, 98, 78))
        draw.rectangle([x + 8, H * 0.72, x + W * 0.075 - 8, H * 0.78], fill=(142, 118, 96))
        draw.line([(x + W * 0.037, H * 0.70), (x + W * 0.037, H * 0.62)], fill=(90, 84, 74), width=6)
        draw.polygon([(x + W * 0.012, H * 0.62), (x + W * 0.062, H * 0.62),
                      (x + W * 0.050, H * 0.54), (x + W * 0.024, H * 0.54)], fill=(246, 236, 214))
        glow(canvas, (x + W * 0.037, H * 0.60), 70, (255, 226, 172), 85)

    # rug + plant
    draw.ellipse([W * 0.20, H * 0.86, W * 0.72, H * 0.99], fill=(214, 198, 176))
    plant(draw, W * 0.92, H * 0.88, 1.15)

    vignette(canvas, 55)
    return canvas.convert("RGB")


def bathroom():
    canvas = Image.new("RGBA", (W, H))
    canvas.paste(Image.new("RGBA", (W, H), (226, 231, 230)), (0, 0))
    draw = ImageDraw.Draw(canvas)
    # tiles
    for row in range(11):
        for col in range(19):
            x0 = col * (W / 19)
            y0 = row * (H / 11)
            draw.rectangle([x0 + 2, y0 + 2, x0 + W / 19 - 2, y0 + H / 11 - 2],
                           outline=(210, 216, 215), width=2)
    floor(canvas, draw, H * 0.78, (206, 200, 190))

    # window
    window = [W * 0.70, H * 0.08, W * 0.95, H * 0.36]
    window_view(draw, window, (168, 198, 214), (222, 234, 238))
    draw.rectangle(window, outline=(86, 88, 86), width=10)

    # bathtub
    tub = [W * 0.50, H * 0.58, W * 0.95, H * 0.80]
    soft_shadow(canvas, tub, radius=40, blur=24, alpha=60, offset=(0, 14))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(tub, radius=52, fill=(250, 249, 246))
    draw.rounded_rectangle([tub[0] + 22, tub[1] + 18, tub[2] - 22, tub[3] - 26], radius=40,
                           fill=(236, 240, 240))
    # tap inside the tub rim, and feet so the bath sits on the floor
    tap_x = tub[0] + 70
    draw.line([(tap_x, tub[1] + 4), (tap_x, tub[1] - H * 0.07)], fill=(176, 180, 182), width=10)
    draw.line([(tap_x, tub[1] - H * 0.07), (tap_x + 42, tub[1] - H * 0.07)],
              fill=(176, 180, 182), width=10)
    for foot_x in (tub[0] + 90, tub[2] - 110):
        draw.rounded_rectangle([foot_x, H * 0.795, foot_x + 46, H * 0.815], radius=8,
                               fill=(198, 200, 198))

    # vanity
    vanity = [W * 0.04, H * 0.52, W * 0.42, H * 0.86]
    soft_shadow(canvas, vanity, radius=12, blur=22, alpha=75, offset=(0, 18))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(vanity, fill=(92, 76, 62))
    draw.rectangle([vanity[0] - 10, vanity[1] - 14, vanity[2] + 10, vanity[1] + 10], fill=(240, 238, 232))
    draw.ellipse([W * 0.14, H * 0.50, W * 0.30, H * 0.56], fill=(250, 250, 248))
    draw.ellipse([W * 0.155, H * 0.505, W * 0.285, H * 0.548], fill=(222, 228, 230))
    draw.line([(W * 0.22, H * 0.505), (W * 0.22, H * 0.47)], fill=(166, 170, 172), width=8)
    draw.line([(W * 0.22, H * 0.47), (W * 0.245, H * 0.472)], fill=(166, 170, 172), width=8)

    # mirror
    mirror = [W * 0.09, H * 0.10, W * 0.36, H * 0.45]
    draw.rounded_rectangle(mirror, radius=10, fill=(198, 212, 214))
    draw.rounded_rectangle([mirror[0] + 10, mirror[1] + 10, mirror[2] - 10, mirror[3] - 10],
                           radius=8, fill=(212, 224, 226))
    draw.line([(mirror[0] + 30, mirror[3] - 20), (mirror[2] - 40, mirror[1] + 24)],
              fill=(232, 240, 240), width=14)

    # towels + plant
    for i, colour in enumerate([(214, 202, 186), (196, 180, 160)]):
        draw.rounded_rectangle([W * 0.44 + i * W * 0.035, H * 0.56, W * 0.47 + i * W * 0.035, H * 0.80],
                               radius=8, fill=colour)
    plant(draw, W * 0.47, H * 0.505, 0.85)

    vignette(canvas, 45)
    return canvas.convert("RGB")


def terrace():
    canvas = Image.new("RGBA", (W, H))
    canvas.paste(gradient((W, H), [
        (0.0, (250, 214, 178)), (0.32, (250, 226, 198)), (0.52, (196, 214, 220)),
        (0.54, (120, 160, 176)), (0.78, (94, 132, 150)), (1.0, (70, 104, 122)),
    ]).convert("RGBA"), (0, 0))
    draw = ImageDraw.Draw(canvas)
    glow(canvas, (W * 0.22, H * 0.24), 230, (255, 220, 168), 130)
    draw.ellipse([W * 0.19, H * 0.19, W * 0.25, H * 0.31], fill=(255, 234, 194))
    # sea shimmer
    for i in range(26):
        y = H * 0.56 + i * H * 0.016
        width = W * (0.05 + 0.03 * (i % 5))
        x = W * 0.12 + (i * 97) % int(W * 0.7)
        draw.line([(x, y), (x + width, y)], fill=(150, 186, 198), width=2)

    # deck
    draw.rectangle([0, H * 0.78, W, H], fill=(198, 172, 138))
    for i in range(9):
        y = H * 0.78 + i * H * 0.026
        draw.line([(0, y), (W, y)], fill=(182, 156, 124), width=3)

    # glass railing
    draw.rectangle([0, H * 0.62, W, H * 0.78], fill=(186, 208, 214))
    glass = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glass)
    gdraw.rectangle([0, H * 0.62, W, H * 0.78], fill=(214, 232, 236, 90))
    canvas.alpha_composite(glass)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, H * 0.615, W, H * 0.632], fill=(86, 92, 96))
    for i in range(7):
        x = i * W / 6
        draw.rectangle([x - 5, H * 0.615, x + 5, H * 0.79], fill=(86, 92, 96))

    # furniture
    def lounger(x, width):
        draw.polygon([(x, H * 0.90), (x + width, H * 0.90), (x + width * 0.86, H * 0.80),
                      (x + width * 0.12, H * 0.80)], fill=(240, 236, 228))
        draw.polygon([(x + width * 0.06, H * 0.80), (x + width * 0.30, H * 0.80),
                      (x + width * 0.22, H * 0.68), (x + width * 0.04, H * 0.68)],
                     fill=(232, 226, 216))
        draw.line([(x + width * 0.15, H * 0.90), (x + width * 0.15, H * 0.97)], fill=(120, 104, 88), width=8)
        draw.line([(x + width * 0.80, H * 0.90), (x + width * 0.80, H * 0.97)], fill=(120, 104, 88), width=8)

    lounger(W * 0.08, W * 0.28)
    lounger(W * 0.42, W * 0.26)

    # table + parasol
    draw.ellipse([W * 0.70, H * 0.82, W * 0.86, H * 0.88], fill=(150, 124, 96))
    draw.rectangle([W * 0.777, H * 0.86, W * 0.783, H * 0.97], fill=(130, 106, 82))
    draw.line([(W * 0.86, H * 0.70), (W * 0.86, H * 0.84)], fill=(150, 140, 126), width=8)
    draw.polygon([(W * 0.72, H * 0.70), (W * 0.995, H * 0.70), (W * 0.94, H * 0.60),
                  (W * 0.78, H * 0.60)], fill=(226, 206, 176))

    plant(draw, W * 0.95, H * 0.82, 1.2)
    plant(draw, W * 0.03, H * 0.80, 0.9)

    vignette(canvas, 70)
    return canvas.convert("RGB")


SCENES = [
    ("01-exterior.jpg", exterior),
    ("02-living-room.jpg", living_room),
    ("03-kitchen.jpg", kitchen),
    ("04-bedroom.jpg", bedroom),
    ("05-bathroom.jpg", bathroom),
    ("06-terrace.jpg", terrace),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic sample property images.")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent.parent /
                                             "sample_data" / "images"))
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, builder in SCENES:
        image = builder()
        destination = out_dir / filename
        image.save(destination, format="JPEG", quality=92, optimize=True)
        print(f"wrote {destination} ({destination.stat().st_size // 1024} KB)")
    print(f"\n{len(SCENES)} synthetic illustrations written to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

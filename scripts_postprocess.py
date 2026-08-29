"""Sprite cleaner: green-screen raws -> tight transparent PNG cutouts.

Keys out everything whose colour is close to the image's own border colour
(the green screen), which also drops enclosed holes (mug handle) and dark
green contact shadows, then keeps the largest remaining component, erodes the
fringe, trims and normalises the size.
"""
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

RAW = "static/captcha/raw"
OUT = "static/captcha/objects"

ITEM_TARGET = 300          # max edge in px for draggable items
CONTAINER_TARGET = 380     # containers render a bit bigger


def largest_component(mask):
    lab, n = ndi.label(mask, structure=np.ones((3, 3), int))
    if n == 0:
        return mask
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return lab == np.argmax(sizes)


def finish(fg, rgb, name, target):
    fg = largest_component(fg)
    # 1 px erosion cuts the anti-aliased fringe left by the keying
    fg = ndi.binary_erosion(fg, iterations=1)
    ys, xs = np.where(fg)
    if len(xs) == 0:
        print(f"{name}: EMPTY MASK", flush=True)
        return
    # soft anti-aliased alpha edge
    alpha = ndi.gaussian_filter(fg.astype(np.float32), 0.7)
    alpha = np.clip(alpha * 1.5, 0, 1)
    pad = 2
    y0, y1 = max(int(ys.min()) - pad, 0), min(int(ys.max()) + pad, fg.shape[0] - 1)
    x0, x1 = max(int(xs.min()) - pad, 0), min(int(xs.max()) + pad, fg.shape[1] - 1)
    rgba = np.dstack([rgb, (alpha * 255).astype(np.uint8)])[y0:y1 + 1, x0:x1 + 1]
    im = Image.fromarray(rgba, "RGBA")
    w, h = im.size
    s = target / max(w, h)
    im = im.resize((max(8, int(w * s)), max(8, int(h * s))), Image.LANCZOS)
    im.save(os.path.join(OUT, name + ".png"))
    print(f"{name}: {im.size}", flush=True)


def process_green(name, target):
    im = Image.open(os.path.join(RAW, name + ".png")).convert("RGB")
    rgb = np.asarray(im)
    a = rgb.astype(int)
    border = np.concatenate([a[0, :], a[-1, :], a[:, 0], a[:, -1]])
    c = np.median(border, axis=0)
    dist = np.sqrt(((a - c) ** 2).sum(-1))
    fg = dist >= 110                      # anything near the screen colour dies
    finish(fg, rgb, name, target)


if __name__ == "__main__":
    names = sys.argv[1:] or ["bear", "cat", "dog", "ball", "apple", "cup",
                             "duck", "fish", "flower"]
    for name in names:
        process_green(name, ITEM_TARGET)
    if not sys.argv[1:]:
        process_green("car", CONTAINER_TARGET)

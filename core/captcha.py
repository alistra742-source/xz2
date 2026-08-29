"""Custom drag-and-drop photo captcha.

The user is shown a photographic scene ("drag the bear into the car").  The
instruction is *rendered into the image* — never sent as text — so a scripted
client has to do object recognition instead of reading JSON.

Verification is fully server side:
  * the drop point must land inside a radius (~2 cm) of the real target,
  * it must be closer to the right target than to any decoy container,
  * the pointer trace must look like a human hand (timing, curvature, jitter).
"""
import base64
import io
import json
import math
import os
import random
import secrets
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config, db

OBJ_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "static", "captcha", "objects")

CONTAINERS = {
    "car": "car",
    "basket": "basket",
    "box": "box",
    "bucket": "bucket",
    "bag": "bag",
}
ITEMS = {
    "bear": "bear",
    "cat": "cat",
    "dog": "dog",
    "ball": "ball",
    "apple": "apple",
    "cup": "cup",
    "duck": "duck",
    "fish": "fish",
    "flower": "flower",
}
VERB = {"car": "into the car", "basket": "into the basket", "box": "into the box",
        "bucket": "into the bucket", "bag": "into the bag"}

_font_cache = {}


def _font(size):
    if size in _font_cache:
        return _font_cache[size]
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ):
        if os.path.exists(path):
            f = ImageFont.truetype(path, size)
            _font_cache[size] = f
            return f
    f = ImageFont.load_default()
    _font_cache[size] = f
    return f


def _load(name):
    return Image.open(os.path.join(OBJ_DIR, f"{name}.png")).convert("RGBA")


def _backdrop(w, h, seed):
    rnd = random.Random(seed)
    base_a = (rnd.randint(30, 80), rnd.randint(60, 120), rnd.randint(90, 150))
    base_b = (rnd.randint(150, 210), rnd.randint(160, 215), rnd.randint(170, 225))
    img = Image.new("RGB", (w, h), base_a)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=(
            int(base_a[0] + (base_b[0] - base_a[0]) * t),
            int(base_a[1] + (base_b[1] - base_a[1]) * t),
            int(base_a[2] + (base_b[2] - base_a[2]) * t),
        ))
    # soft blobs so plain colour matching cannot segment the scene
    blob = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(blob)
    for _ in range(18):
        cx, cy = rnd.randint(0, w), rnd.randint(0, h)
        r = rnd.randint(40, 150)
        bd.ellipse([cx - r, cy - r, cx + r, cy + r],
                   fill=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255),
                         rnd.randint(12, 34)))
    blob = blob.filter(ImageFilter.GaussianBlur(28))
    img = Image.alpha_composite(img.convert("RGBA"), blob)
    # ground plane (drawn on its own layer so the alpha actually blends)
    ground = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(ground)
    gy = int(h * rnd.uniform(0.68, 0.8))
    gd.rectangle([0, gy, w, h], fill=(20, 18, 30, 46))
    gd.line([(0, gy), (w, gy)], fill=(255, 255, 255, 26), width=2)
    img = Image.alpha_composite(img, ground)
    return img


def _paste(scene, sprite, cx, cy, scale=1.0, rot=0.0):
    if scale != 1.0:
        sprite = sprite.resize((max(8, int(sprite.width * scale)),
                                max(8, int(sprite.height * scale))), Image.LANCZOS)
    if rot:
        sprite = sprite.rotate(rot, expand=True, resample=Image.BICUBIC)
    x = int(cx - sprite.width / 2)
    y = int(cy - sprite.height / 2)
    # soft shadow
    shadow = Image.new("RGBA", sprite.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 90), (0, 0), sprite)
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    scene.alpha_composite(shadow, (x + 4, y + 8))
    scene.alpha_composite(sprite, (x, y))
    return (cx, cy, sprite.width, sprite.height)


def _rects_overlap(a, b, pad=22):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (abs(ax - bx) * 2 < (aw + bw + pad * 2)) and (abs(ay - by) * 2 < (ah + bh + pad * 2))


def _b64(img, fmt="PNG"):
    buf = io.BytesIO()
    img.save(buf, fmt, optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def build_challenge(session_token, purpose="verify"):
    W, H = config.CAPTCHA_SCENE_W, config.CAPTCHA_SCENE_H
    seed = secrets.randbelow(1 << 30)
    rnd = random.Random(seed)
    scene = _backdrop(W, H, seed)

    target_name = rnd.choice(list(CONTAINERS))
    decoys = [c for c in CONTAINERS if c != target_name]
    rnd.shuffle(decoys)
    decoys = decoys[:rnd.randint(1, 2)]

    placed = []
    spots = []

    def free_spot(halfw, halfh, tries=90):
        for _ in range(tries):
            cx = rnd.randint(halfw + 20, W - halfw - 20)
            cy = rnd.randint(int(H * 0.32), H - halfh - 16)
            cand = (cx, cy, halfw * 2, halfh * 2)
            if all(not _rects_overlap(cand, p) for p in placed):
                return cx, cy
        return rnd.randint(80, W - 80), rnd.randint(int(H * 0.4), H - 70)

    # containers (target + decoys)
    container_points = {}
    for name in [target_name] + decoys:
        sprite = _load(CONTAINERS[name])
        scale = rnd.uniform(0.4, 0.55)
        cw, ch = int(sprite.width * scale), int(sprite.height * scale)
        cx, cy = free_spot(cw // 2, ch // 2)
        rect = _paste(scene, sprite, cx, cy, scale, rnd.uniform(-4, 4))
        placed.append(rect)
        container_points[name] = (cx, cy)
        spots.append(rect)

    # scenery items (distractors already sitting in the scene)
    item_name = rnd.choice(list(ITEMS))
    scenery_pool = [i for i in ITEMS if i != item_name]
    rnd.shuffle(scenery_pool)
    for name in scenery_pool[:rnd.randint(1, 3)]:
        sprite = _load(ITEMS[name])
        scale = rnd.uniform(0.24, 0.38)
        cw, ch = int(sprite.width * scale), int(sprite.height * scale)
        cx, cy = free_spot(cw // 2, ch // 2)
        placed.append(_paste(scene, sprite, cx, cy, scale, rnd.uniform(-10, 10)))

    # instruction burnt into the picture, slightly warped
    prompt = f"Drag the {item_name} {VERB[target_name]}"
    banner = Image.new("RGBA", (W, 46), (0, 0, 0, 0))
    bd = ImageDraw.Draw(banner)
    bd.rounded_rectangle([10, 6, W - 10, 42], 12, fill=(12, 14, 22, 205))
    f = _font(22)
    tw = bd.textlength(prompt, font=f)
    x = (W - tw) / 2
    for ch in prompt:
        dy = rnd.uniform(-2.5, 2.5)
        bd.text((x, 11 + dy), ch, font=f, fill=(255, 255, 255, 240))
        x += bd.textlength(ch, font=f) + rnd.uniform(-0.4, 1.1)
    scene.alpha_composite(banner, (0, 2))

    # light global noise: kills naive template matching
    noise = Image.effect_noise((W, H), 22).convert("L")
    scene = Image.blend(scene.convert("RGB"), Image.merge("RGB", (noise, noise, noise)), 0.045)

    # the draggable sprite, rendered separately for the tray
    item_sprite = _load(ITEMS[item_name])
    item_scale = rnd.uniform(0.34, 0.46)
    item_sprite = item_sprite.resize(
        (int(item_sprite.width * item_scale), int(item_sprite.height * item_scale)),
        Image.LANCZOS)

    cid = secrets.token_urlsafe(18)
    payload = {
        "target": list(container_points[target_name]),
        "decoys": [list(container_points[n]) for n in decoys],
        "tolerance": config.CAPTCHA_TOLERANCE_PX,
        "item": item_name,
        "container": target_name,
        "w": W, "h": H,
        "issued": time.time(),
        "purpose": purpose,
    }
    db.execute(
        "INSERT INTO captchas (id, session_tok, purpose, payload, created_at, solved, attempts)"
        " VALUES (?, ?, ?, ?, ?, ?, 0)",
        (cid, session_token or "", purpose, json.dumps(payload), time.time(),
         False if db.IS_PG else 0),
    )
    _gc()
    return {
        "id": cid,
        "scene": _b64(scene.convert("RGB")),
        "item": _b64(item_sprite),
        "item_w": item_sprite.width,
        "item_h": item_sprite.height,
        "w": W,
        "h": H,
        "purpose": purpose,
    }


def _gc():
    try:
        db.execute("DELETE FROM captchas WHERE created_at < ?",
                   (time.time() - config.CAPTCHA_TTL_SECONDS * 4,))
    except Exception:
        pass


def _human_trace(trace):
    """Heuristics that a real hand passes and a scripted drag does not."""
    if not isinstance(trace, list) or len(trace) < 6:
        return False, "trace-too-short"
    pts = []
    for p in trace[:600]:
        try:
            pts.append((float(p[0]), float(p[1]), float(p[2])))
        except Exception:
            return False, "trace-malformed"
    duration = pts[-1][2] - pts[0][2]
    if duration < 180 or duration > 180000:
        return False, "trace-timing"
    # monotonic timestamps
    if any(pts[i][2] < pts[i - 1][2] for i in range(1, len(pts))):
        return False, "trace-time-order"
    straight = math.dist(pts[0][:2], pts[-1][:2])
    path = sum(math.dist(pts[i][:2], pts[i - 1][:2]) for i in range(1, len(pts)))
    if straight < 25:
        return False, "trace-no-motion"
    if path < straight * 1.02:
        return False, "trace-too-straight"
    # inter-sample interval variance (robots emit perfectly even steps)
    dts = [pts[i][2] - pts[i - 1][2] for i in range(1, len(pts))]
    mean_dt = sum(dts) / len(dts)
    if mean_dt <= 0:
        return False, "trace-dt"
    var = sum((d - mean_dt) ** 2 for d in dts) / len(dts)
    if var < 0.35 and len(dts) > 10:
        return False, "trace-uniform"
    # speed profile must not be constant
    speeds = [math.dist(pts[i][:2], pts[i - 1][:2]) / max(1e-3, dts[i - 1]) for i in range(1, len(pts))]
    if max(speeds) > 12.0:
        return False, "trace-too-fast"
    smean = sum(speeds) / len(speeds)
    svar = sum((s - smean) ** 2 for s in speeds) / len(speeds)
    if smean > 0 and (svar ** 0.5) / smean < 0.12:
        return False, "trace-constant-speed"
    return True, "ok"


def verify(cid, drop_x, drop_y, trace, trusted=True):
    row = db.query_one("SELECT * FROM captchas WHERE id = ?", (cid,))
    if not row:
        return False, "expired"
    if row["solved"]:
        return False, "already-used"
    if time.time() - float(row["created_at"]) > config.CAPTCHA_TTL_SECONDS:
        db.execute("DELETE FROM captchas WHERE id = ?", (cid,))
        return False, "expired"
    attempts = int(row["attempts"] or 0) + 1
    db.execute("UPDATE captchas SET attempts = ? WHERE id = ?", (attempts, cid))
    if attempts > 3:
        db.execute("DELETE FROM captchas WHERE id = ?", (cid,))
        return False, "too-many-attempts"

    payload = json.loads(row["payload"])
    if not trusted:
        return False, "untrusted-input"
    ok, why = _human_trace(trace)
    if not ok:
        return False, why

    try:
        dx, dy = float(drop_x), float(drop_y)
    except Exception:
        return False, "bad-drop"
    if not (0 <= dx <= payload["w"] and 0 <= dy <= payload["h"]):
        return False, "out-of-scene"

    tx, ty = payload["target"]
    dist = math.dist((dx, dy), (tx, ty))
    if dist > payload["tolerance"]:
        return False, "missed"
    for cxy in payload.get("decoys", []):
        if math.dist((dx, dy), tuple(cxy)) < dist:
            return False, "wrong-container"

    # the last trace point must agree with the reported drop point
    last = trace[-1]
    if math.dist((float(last[0]), float(last[1])), (dx, dy)) > 60:
        return False, "drop-mismatch"

    db.execute("UPDATE captchas SET solved = ? WHERE id = ?", (True if db.IS_PG else 1, cid))
    return True, "ok"

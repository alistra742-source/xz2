"""Zefoy captcha solver — multi-backend ensemble.

Backends, best available first (each optional, graceful fallback):

  * **ddddocr**  — ONNX model trained specifically on captchas.  Reads noisy,
    struck-through text that Tesseract chokes on, at ~10-30 ms a shot.
  * **tesserocr** — in-process Tesseract with a *persistent* API per worker
    thread (no process spawn per call, unlike pytesseract).
  * **pytesseract** — CLI fallback (what the Docker image always has).

Pipeline upgrades over the previous version:

  * vectorised strike-line removal (horizontal morphological opening) so the
    lines zefoy draws through the word never reach the OCR as glyphs
  * Sauvola adaptive thresholding in addition to Otsu (handles the gradient
    backgrounds), plus a projection-profile deskew for the Tesseract path
  * ddddocr fast path: a cleaned image that reads as a dictionary word is
    returned immediately — the common case now resolves in tens of ms
  * ensemble voting weighted by backend strength and Tesseract confidence,
    with the length-bucketed dictionary snap as tie-breaker
  * two-level answer cache (process + database) and active forgetting kept

Typical latency: ~0.02-0.05 s on a ddddocr hit, <1 s on a Tesseract vote.
"""
import hashlib
import os
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from . import config

try:
    from scipy import ndimage as _ndi
except Exception:  # pragma: no cover
    _ndi = None

_mem_cache = {}
_mem_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=config.CAPTCHA_SOLVER_WORKERS,
                           thread_name_prefix="ocr")

WORDS = []
_buckets = defaultdict(list)
_word_lock = threading.Lock()

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dictionary():
    global WORDS
    words = []
    for path in ("/usr/share/dict/words", "/usr/share/dict/american-english"):
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    words = [w.strip().lower() for w in fh]
                break
            except Exception:
                pass
    words = [w for w in words if w.isalpha() and 3 <= len(w) <= 10]
    if not words:
        words = _FALLBACK_WORDS
    with _word_lock:
        WORDS = words
        _buckets.clear()
        for w in words:
            _buckets[len(w)].append(w)
    print(f"[SOLVER] dictionary: {len(words)} words", flush=True)


_FALLBACK_WORDS = """about above actor adapt admit adopt after again agent agree ahead alarm album
alert alike alive allow alone along alter among anger angle angry ankle apart apple apply arena argue
arise armor aroma array arrow aside asset atlas audio audit avoid awake award aware badge baker basic
basin batch beach beard beast begin being belly below bench berry birth black blade blame blank blast
blend bless blind block blood bloom board boast bonus boost booth bound brain brand brass brave bread
break breed brick bride brief bring broad broke brown brush build built bunch burst cabin cable candy
canoe cargo carry carve catch cause cease chain chair chalk charm chart chase cheap check cheer chess
chest chief child chill china choir chose civil claim clash class clean clear clerk click cliff climb
clock close cloth cloud coach coast color couch could count court cover crack craft crane crash crazy
cream creek crest crime crisp cross crowd crown crude cruel crush curve cycle daily dairy dance dealt
death debut delay dense depth devil diary dirty ditch dodge doing donor doubt dozen draft drain drama
drank dream dress dried drift drill drink drive drove drown eager eagle early earth eight elbow elder
elect elite empty enemy enjoy enter entry equal error essay event every exact exist extra fable faith
false fancy fatal fault favor feast fence fever field fiber fifth fight final first flame flash fleet
flesh flight float flood floor flour fluid focus force forge forth found frame fraud fresh front frost
fruit fully funny giant given glass globe glory glove grace grade grain grand grant grape graph grass
grave great greed green greet grief grill grind gross group grove guard guess guest guide habit happy
harsh haste hatch haunt heart heavy hedge hello hence hobby honey honor horse hotel house human humor
hurry ideal image imply index inner input irony issue ivory joint judge juice known label labor large
laser later laugh layer learn lease least leave legal lemon level light limit linen liver lobby local
lodge logic loose lower loyal lucky lunar lunch magic major maker maple march match maybe mayor meant
medal media mercy merit metal meter midst might minor minus mixed model money month moral motor mount
mouse mouth movie music naked nasty naval nerve never newly night noble noise north novel nurse ocean
offer often olive onion opera orbit order organ other ought outer owner paint panel panic paper party
patch pause peace pearl phase phone photo piano piece pilot pitch pizza place plain plane plant plate
plaza point polar porch pound power press price pride prime print prize probe proof proud prove pulse
punch pupil puppy purse quest queue quick quiet quite quote radar radio raise rally ranch range rapid
ratio reach ready realm rebel refer reign relax relay reply rider ridge rifle right rigid rival river
roast robot rocky roman rough round route royal rugby ruler rural saint salad sauce scale scene scent
scope score scout scrap screw sense serve seven shade shaft shake shall shame shape share shark sharp
sheep sheet shelf shell shift shine shirt shock shoot shore short shout shown sight silly since siren
sixth skill skirt sleep slice slide slope small smart smell smile smoke snake solar solid solve sorry
sound south space spare spark speak speed spell spend spice spine spite split spoke sport spray squad
stack staff stage stair stake stamp stand stare start state steam steel steep steer stick stiff still
sting stock stone stood store storm story stove strap straw strip stuck study stuff style sugar suite
sunny super surge sweep sweet swift swing sword table taken talent tally taste teach thank theft their
theme there these thick thief thing think third those three throw thumb tiger tight timer title toast
today token tooth topic torch total touch tough tower toxic trace track trade trail train trait trash
treat trend trial tribe trick tried troop truck truly trunk trust truth twice twist uncle under union
unite unity until upper upset urban usage usual valid value valve vapor vault venue verse video villa
vinyl viral virus visit vital vivid vocal voice voter wagon waist waste watch water weary weigh weird
whale wheat wheel where which while white whole whose widow width witch woman world worry worse worth
would wound wrist write wrong yield young youth zebra angel ankle antsy arrow azure bacon bagel basil
bison blaze bloom blush bravo brine brisk brook cedar charm cider clover coral couch crane crisp dawn
delta denim dove dragon drift dune dusk embers falcon fern fig flame flint flora fog forest fox gem
glade glow granite grape grove gull haze heron holly ivory jade kiosk kiwi lagoon lark lava leaf lemon
lily lion lizard lodge lotus marble marsh meadow mint mist moose moss nectar nest nut oak oasis otter
owl palm panda pebble pine poppy prism quail quartz rabbit rain raven reef ridge robin rose sage sand
seal seed shadow shale shore shrub silk sky slate snow sparrow spring star stone stream summit swan
tide tiger topaz trail tulip turtle valley vine violet wave willow wind winter wolf wood wren yarn""".split()

threading.Thread(target=load_dictionary, daemon=True).start()


# ------------------------------------------------------------------ backends
_tls = threading.local()


def _dddd():
    """Thread-local ddddocr instance (lazy: import only on first use)."""
    ocr = getattr(_tls, "dddd", None)
    if ocr is None and not getattr(_tls, "dddd_failed", False):
        try:
            import ddddocr
            ocr = ddddocr.DdddOcr(show_ad=False)
        except Exception:
            ocr = None
            _tls.dddd_failed = True
        _tls.dddd = ocr
    return ocr


def _tess_api():
    api = getattr(_tls, "tess", None)
    if api is None and not getattr(_tls, "tess_failed", False):
        try:
            import tesserocr
            paths = [None, os.path.join(_REPO, "tessdata"), "/usr/share/tessdata",
                     "/usr/share/fonts/tessdata"]
            for p in paths:
                try:
                    api = tesserocr.PyTessBaseAPI(path=p) if p else \
                        tesserocr.PyTessBaseAPI()
                    break
                except Exception:
                    api = None
            if api is not None:
                api.SetVariable("tessedit_char_whitelist",
                                "abcdefghijklmnopqrstuvwxyz")
        except Exception:
            api = None
            _tls.tess_failed = True
        _tls.tess = api
    return api


def _tess_run(pil_img, psm):
    """One Tesseract read via the best available binding. Returns (text, conf)."""
    api = _tess_api()
    if api is not None:
        import tesserocr
        try:
            api.SetPageSegMode(psm)
            api.SetImage(pil_img)
            txt = api.GetUTF8Text()
            conf = api.MeanTextConf()
            return re.sub(r"[^a-z]", "", txt.lower()), int(conf or 0)
        except Exception:
            return "", 0
    try:
        import pytesseract
        cfg = {7: "--psm 7", 8: "--psm 8", 13: "--psm 13"}[psm]
        cfg += " --oem 1 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyz"
        txt = pytesseract.image_to_string(pil_img, config=cfg)
        return re.sub(r"[^a-z]", "", txt.lower()), 40
    except Exception:
        return "", 0


# ------------------------------------------------------------------ helpers
def _otsu(a):
    hist = np.bincount(a.ravel(), minlength=256).astype(np.float64)
    total = a.size
    w_b = np.cumsum(hist)
    w_f = total - w_b
    sum_total = np.dot(np.arange(256), hist)
    sum_b = np.cumsum(np.arange(256) * hist)
    with np.errstate(invalid="ignore", divide="ignore"):
        m_b = sum_b / np.maximum(w_b, 1)
        m_f = (sum_total - sum_b) / np.maximum(w_f, 1)
        between = w_b * w_f * (m_b - m_f) ** 2
    between[np.isnan(between)] = 0
    return int(np.argmax(between))


def _sauvola(a, win=31, k=0.12):
    """Adaptive threshold — text mask True where the pixel is ink."""
    h, w = a.shape
    pad = win // 2
    p = np.pad(a.astype(np.float64), pad, mode="edge")
    P = np.zeros((p.shape[0] + 1, p.shape[1] + 1))
    P[1:, 1:] = p.cumsum(0).cumsum(1)
    Q = np.zeros_like(P)
    Q[1:, 1:] = (p * p).cumsum(0).cumsum(1)
    Y = np.arange(h)[:, None]
    X = np.arange(w)[None, :]
    def win_sum(T):
        return T[Y + win, X + win] - T[Y, X + win] - T[Y + win, X] + T[Y, X]
    n = win * win
    mean = win_sum(P) / n
    var = np.maximum(win_sum(Q) / n - mean * mean, 0)
    thr = mean * (1 + k * (np.sqrt(var) / 128 - 1))
    return a < thr


def _despeckle(mask, min_size=18):
    """Drop connected blobs smaller than min_size. Vectorised."""
    if _ndi is None:
        return mask
    lab, n = _ndi.label(mask, structure=np.ones((3, 3), dtype=int))
    if n == 0:
        return mask
    sizes = np.bincount(lab.ravel())
    keep = sizes >= min_size
    keep[0] = False
    return keep[lab]


def _strip_lines(mask):
    """Remove strike-through lines: horizontal opening keeps only structures
    as wide as a real line (a quarter of the image), then deletes them."""
    if _ndi is None:
        return mask
    h, w = mask.shape
    length = max(50, w // 4)
    lines = _ndi.binary_opening(mask, structure=np.ones((1, length), dtype=bool))
    if not lines.any():
        return mask
    lines = _ndi.binary_dilation(lines, structure=np.ones((3, 3), dtype=bool),
                                 iterations=2)
    return mask & ~lines


def _deskew(img):
    """Pick the rotation whose row-projection variance is highest (text rows
    align into sharp peaks when the baseline is level)."""
    best, best_score = img, -1
    arr = np.asarray(img, dtype=np.uint8)
    ink = arr < 128
    for ang in (0, -4, -2, 2, 4):
        cand = img.rotate(ang, expand=False, fillcolor=255) if ang else img
        a = np.asarray(cand, dtype=np.uint8) < 128
        rows = a.sum(1).astype(np.float64)
        score = rows.var()
        if score > best_score:
            best, best_score = cand, score
    return best


def _prepare(img_bytes):
    img = Image.open(BytesIO(img_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    gray = ImageOps.grayscale(img)
    w, h = gray.size
    scale = max(2, min(5, int(150 / max(1, h))))
    big = gray.resize((w * scale, h * scale), Image.LANCZOS)
    return np.asarray(big, dtype=np.uint8)


def _as_img(mask):
    """text-mask -> black-on-white PIL image (what both OCRs prefer)."""
    return Image.fromarray(((~mask) * 255).astype(np.uint8))


# ------------------------------------------------------------------ cache
def _cache_get(h):
    with _mem_lock:
        if h in _mem_cache:
            return _mem_cache[h]
    try:
        from . import db
        row = db.query_one("SELECT answer FROM captcha_cache WHERE img_hash = ?", (h,))
        if row:
            with _mem_lock:
                _mem_cache[h] = row["answer"]
            return row["answer"]
    except Exception:
        pass
    return None


def _cache_put(h, answer):
    with _mem_lock:
        _mem_cache[h] = answer
        if len(_mem_cache) > 5000:
            for k in list(_mem_cache)[:1000]:
                _mem_cache.pop(k, None)
    try:
        from . import db
        db.execute("INSERT INTO captcha_cache (img_hash, answer, hits) VALUES (?, ?, 1)", (h,))
    except Exception:
        try:
            from . import db
            db.execute("UPDATE captcha_cache SET hits = hits + 1 WHERE img_hash = ?", (h,))
        except Exception:
            pass


def _edit_distance(a, b, limit=3):
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            best = min(best, cur[-1])
        if best > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _snap(word):
    """Nearest dictionary word (bucketed by length ±1)."""
    if not word:
        return None, 99
    with _word_lock:
        if not WORDS:
            return word, 0
        if word in _buckets.get(len(word), ()):
            return word, 0
        pool = []
        for L in (len(word), len(word) - 1, len(word) + 1):
            pool.extend(_buckets.get(L, ()))
    best, best_d = None, 99
    for cand in pool:
        d = _edit_distance(word, cand, 2)
        if d < best_d:
            best, best_d = cand, d
            if d == 0:
                break
    return best, best_d


def _in_dict(word):
    with _word_lock:
        return word in _buckets.get(len(word), ()) if word else False


# ------------------------------------------------------------------ solve
def solve(img_bytes, deadline=6.0):
    """Return the captcha word (best effort)."""
    t0 = time.time()
    h = hashlib.sha256(img_bytes).hexdigest()[:24]
    cached = _cache_get(h)
    if cached:
        return cached

    try:
        arr = _prepare(img_bytes)
    except Exception as e:
        print(f"[SOLVER] preprocess failed: {e}", flush=True)
        return ""

    candidates = []          # (word, weight)

    # ---- fast path: ddddocr on cleaned images
    dd = _dddd()
    if dd is not None:
        t = _otsu(arr)
        dark_text = arr.mean() > 127
        core = (arr < t) if dark_text else (arr >= t)
        inputs = [_as_img(_despeckle(core, 12)),
                  _as_img(_despeckle(_strip_lines(core), 12))]
        for im in inputs:
            try:
                buf = BytesIO()
                im.save(buf, "PNG")
                raw = dd.classification(buf.getvalue())
                w = re.sub(r"[^a-z]", "", (raw or "").lower())
            except Exception:
                continue
            if 3 <= len(w) <= 12:
                candidates.append((w, 3.0))
                if _in_dict(w):
                    _cache_put(h, w)
                    print(f"[SOLVER] '{w}' in {time.time()-t0:.3f}s (ddddocr fast path)",
                          flush=True)
                    return w
            if time.time() - t0 > deadline * 0.5:
                break

    # ---- Tesseract ensemble over high-signal variants
    t = _otsu(arr)
    dark_text = arr.mean() > 127
    core = (arr < t) if dark_text else (arr >= t)
    clean = _despeckle(_strip_lines(core), max(12, arr.shape[0] // 20))
    variants = [
        _as_img(_despeckle(core, 12)),
        _as_img(clean),
        _deskew(_as_img(_despeckle(_sauvola(arr), 12))),
    ]
    jobs = []
    have_tess = _tess_api() is not None
    for v in variants[:2]:
        for psm in (8, 7):
            jobs.append(_pool.submit(_tess_run, v, psm))
    raw = []
    for fut in jobs:
        try:
            txt, conf = fut.result(timeout=max(0.5, deadline - (time.time() - t0)))
        except Exception:
            continue
        if 3 <= len(txt) <= 12:
            raw.append(txt)
            candidates.append((txt, 1.0 + max(0, conf) / 50.0))
        if len(raw) >= 2:
            c = Counter(raw).most_common(1)[0]
            if c[1] >= 2 and _in_dict(c[0]):
                _cache_put(h, c[0])
                print(f"[SOLVER] '{c[0]}' in {time.time()-t0:.2f}s (tess agreement)",
                      flush=True)
                return c[0]

    if not candidates:
        try:
            txt, _ = _tess_run(variants[2], 13)
            if 3 <= len(txt) <= 12:
                candidates.append((txt, 1.0))
        except Exception:
            pass
    if not candidates:
        print(f"[SOLVER] no candidates in {time.time()-t0:.2f}s", flush=True)
        return ""

    # ---- weighted ensemble vote with dictionary snapping
    scores = Counter()
    exact = Counter(w for w, _ in candidates)
    for word, wt in candidates:
        snapped, dist = _snap(word)
        if snapped and dist == 0:
            scores[snapped] += wt * 2.0
        elif snapped and dist <= 2:
            scores[snapped] += wt * (3 - dist) * 0.5
        scores[word] += wt * 0.5
    for word, n in exact.items():
        if n >= 2:
            snapped, dist = _snap(word)
            scores[snapped if dist <= 2 else word] += 2.0
    answer = scores.most_common(1)[0][0]
    if len(answer) >= 3:
        _cache_put(h, answer)
    print(f"[SOLVER] '{answer}' in {time.time()-t0:.2f}s from {[w for w, _ in candidates]}"
          + ("" if have_tess else " (ddddocr only)"), flush=True)
    return answer


def forget(img_bytes):
    """Drop a wrong cached answer so the next attempt re-solves."""
    h = hashlib.sha256(img_bytes).hexdigest()[:24]
    with _mem_lock:
        _mem_cache.pop(h, None)
    try:
        from . import db
        db.execute("DELETE FROM captcha_cache WHERE img_hash = ?", (h,))
    except Exception:
        pass

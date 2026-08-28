"""Public metric readers (TikTok views / likes / favourites / shares).

Multiple independent sources are tried in order and the first plausible answer
wins, so a single upstream outage does not stall the queue.
"""
import json
import re
import threading
import time

import requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/126.0.0.0 Safari/537.36")
_cache = {}
_lock = threading.Lock()
CACHE_TTL = 20


def normalise_tiktok(url):
    url = (url or "").strip()
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    if "tiktok.com" not in url:
        return None
    return url.split("?")[0]


def _resolve_short(url):
    try:
        r = requests.head(url, headers={"User-Agent": _UA}, allow_redirects=True, timeout=12)
        return r.url.split("?")[0]
    except Exception:
        return url


def video_id(url):
    m = re.search(r"/video/(\d+)", url or "")
    if m:
        return m.group(1)
    m = re.search(r"/photo/(\d+)", url or "")
    return m.group(1) if m else None


# ------------------------------------------------------------------ sources
def _src_tikwm(url):
    r = requests.get("https://www.tikwm.com/api/", params={"url": url, "hd": 0},
                     headers={"User-Agent": _UA}, timeout=15)
    j = r.json()
    d = (j or {}).get("data") or {}
    if not d:
        return None
    return {
        "views": int(d.get("play_count") or 0),
        "hearts": int(d.get("digg_count") or 0),
        "favorites": int(d.get("collect_count") or 0),
        "shares": int(d.get("share_count") or 0),
        "comments": int(d.get("comment_count") or 0),
    }


def _src_tiklydown(url):
    r = requests.get("https://api.tiklydown.eu.org/api/download",
                     params={"url": url}, headers={"User-Agent": _UA}, timeout=15)
    j = r.json()
    st = (j or {}).get("stats") or {}
    if not st:
        return None
    return {
        "views": int(st.get("playCount") or 0),
        "hearts": int(st.get("diggCount") or 0),
        "favorites": int(st.get("collectCount") or 0),
        "shares": int(st.get("shareCount") or 0),
        "comments": int(st.get("commentCount") or 0),
    }


def _src_html(url):
    r = requests.get(url, headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
                     timeout=20)
    html = r.text
    m = re.search(r'"stats":\{(.*?)\}', html)
    blob = None
    if m:
        try:
            blob = json.loads("{" + m.group(1) + "}")
        except Exception:
            blob = None
    if not blob:
        def grab(key):
            mm = re.search(rf'"{key}":(\d+)', html)
            return int(mm.group(1)) if mm else 0
        blob = {"playCount": grab("playCount"), "diggCount": grab("diggCount"),
                "collectCount": grab("collectCount"), "shareCount": grab("shareCount"),
                "commentCount": grab("commentCount")}
    if not blob.get("playCount"):
        return None
    return {
        "views": int(blob.get("playCount") or 0),
        "hearts": int(blob.get("diggCount") or 0),
        "favorites": int(str(blob.get("collectCount") or 0).strip('"') or 0),
        "shares": int(blob.get("shareCount") or 0),
        "comments": int(blob.get("commentCount") or 0),
    }


SOURCES = (_src_tikwm, _src_tiklydown, _src_html)


def tiktok_stats(url, force=False):
    """Return {'views':..,'hearts':..,...} or None."""
    url = normalise_tiktok(url)
    if not url:
        return None
    if "/video/" not in url and "/photo/" not in url:
        url = _resolve_short(url)
    key = url
    now = time.time()
    if not force:
        with _lock:
            hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    for src in SOURCES:
        try:
            data = src(url)
            if data and data.get("views", 0) >= 0 and any(v for v in data.values()):
                with _lock:
                    _cache[key] = (now, data)
                return data
        except Exception:
            continue
    return None


def metric(url, key, force=False):
    stats = tiktok_stats(url, force=force)
    if not stats:
        return None
    return int(stats.get(key, 0))


def valid_tiktok_link(url):
    u = normalise_tiktok(url)
    if not u:
        return False
    return bool(re.search(r"tiktok\.com/(@[\w.\-]+/(video|photo)/\d+|t/\w+|[\w]+)", u))


def valid_instagram_link(url):
    u = (url or "").strip().lower()
    return u.startswith("http") and "instagram.com/" in u

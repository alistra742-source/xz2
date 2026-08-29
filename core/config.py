"""Central configuration. Everything is env-driven so Railway/Render/Docker just works."""
import os


def _b(name, default="false"):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _i(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


# ---------------------------------------------------------------- app
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip() or os.urandom(32).hex()
PORT = _i("PORT", 8080)
SITE_NAME = os.environ.get("SITE_NAME", "COINFLOW")

# ---------------------------------------------------------------- database
# Postgres when DATABASE_URL / DATABASE is present, otherwise a local sqlite file.
DATABASE_URL = (
    os.environ.get("DATABASE_URL")
    or os.environ.get("DATABASE")
    or os.environ.get("POSTGRES_URL")
    or ""
).strip()
SQLITE_PATH = os.environ.get("SQLITE_PATH", os.path.join(os.getcwd(), "data", "app.db"))

# ---------------------------------------------------------------- ad provider
# These are public placement script URLs, not credentials. Keep any provider API
# key server-side and out of templates or browser JavaScript.
ADSTERRA_SOCIALBAR_SRC = os.environ.get(
    "ADSTERRA_SOCIALBAR_SRC",
    "https://screwbedriddenheadline.com/77/58/bb/7758bb6f597e78f4f3aabb5e5e05c6d7.js",
).strip()
ADSTERRA_POPUNDER_SRC = os.environ.get(
    "ADSTERRA_POPUNDER_SRC",
    "https://screwbedriddenheadline.com/3a/80/83/3a80835883717f0372e091ba19face4c.js",
).strip()

# ---------------------------------------------------------------- economy
COIN_PACKS = {
    "ads1": {"ads": 1, "coins": 5, "label": "Watch 1 ad"},
    "ads5": {"ads": 5, "coins": 30, "label": "Watch 5 ads"},
    "ads10": {"ads": 10, "coins": 65, "label": "Watch 10 ads"},
}
AD_MIN_SECONDS = _i("AD_MIN_SECONDS", 15)        # each ad must be watched fully
AD_HEARTBEAT_SECONDS = 2                          # client ping interval
AD_MAX_MISSED_BEATS = 2                           # tolerance before suspicion

# ---------------------------------------------------------------- captcha
CAPTCHA_SCENE_W = 640
CAPTCHA_SCENE_H = 380
CAPTCHA_TOLERANCE_PX = _i("CAPTCHA_TOLERANCE_PX", 78)   # ~2 cm of slack
CAPTCHA_REQUIRED_SOLVES = 2
CAPTCHA_TTL_SECONDS = 300
CAPTCHA_INTERVAL_SECONDS = _i("CAPTCHA_INTERVAL_SECONDS", 3600)   # re-verify hourly

# ---------------------------------------------------------------- admin
ADMIN_CODE = os.environ.get("ADMIN_CODE", "Tagys322@")

# ---------------------------------------------------------------- bot engine
ZEFOY_URL = "https://zefoy.com"
ZEFAME_IG_VIEWS_URL = "https://zefame.com/en/free-instagram-views"
BROWSERS = _i("BROWSERS", 4)
PAGES_PER_BROWSER = _i("PAGES_PER_BROWSER", 3)
WORKER_ENABLED = _b("WORKER_ENABLED", "true")
MONITOR_ENABLED = _b("MONITOR_ENABLED", "true")
MONITOR_INTERVAL = 5                      # zefoy up/down probe cadence (seconds)
LINK_LOCK_SECONDS = _i("LINK_LOCK_SECONDS", 300)   # 5 min global lock per link
# Zefame runs its own ~1 minute counter after "Get Now" is pressed: sit through
# it, then hold a little longer before the browser leaves the page.
ZEFAME_TIMER_WAIT = _i("ZEFAME_TIMER_WAIT", 105)
ZEFAME_FINAL_WAIT = _i("ZEFAME_FINAL_WAIT", 20)
# One zefame batch delivers ~300 views, and a new batch can only start every
# 5.3 minutes (the site's per-link cooldown).  Bigger IG orders therefore run
# several batches back to back: 1000 views = 4 batches ≈ 22 minutes.
ZEFAME_VIEWS_PER_RUN = _i("ZEFAME_VIEWS_PER_RUN", 300)
ZEFAME_CYCLE_SECONDS = _i("ZEFAME_CYCLE_SECONDS", 318)

# How many worker pages may hammer the SAME order at once, per service.
# Likes benefit the most from fan-out, so they get the full four.
PARALLEL_PAGES = {
    "hearts": _i("PARALLEL_HEARTS", 4),
    "favorites": _i("PARALLEL_FAVORITES", 2),
    "shares": _i("PARALLEL_SHARES", 2),
    "views": _i("PARALLEL_VIEWS", 1),
}
PARALLEL_DEFAULT = 1

PROXY_URL = os.environ.get("PROXY_URL", "").strip()
USE_TOR = _b("USE_TOR", "false")
TOR_BASE_PORT = _i("TOR_BASE_PORT", 9050)

CAPTCHA_SOLVER_WORKERS = _i("CAPTCHA_SOLVER_WORKERS", 6)

# ---------------------------------------------------------------- catalogue
# Buyers pick HOW MUCH they want; price scales linearly from base_amount /
# base_cost (rounded up).  min/max/step bound and quantise the chooser.
REWARDS = {
    "tiktok": {
        "label": "TikTok",
        "engine": "zefoy",
        "services": {
            "hearts": {"label": "Likes", "unit": "likes", "zefoy_key": "hearts",
                       "base_amount": 25, "base_cost": 10,
                       "min": 25, "max": 1000, "step": 25},
            "views": {"label": "Views", "unit": "views", "zefoy_key": "views",
                      "base_amount": 1000, "base_cost": 5,
                      "min": 500, "max": 10000, "step": 500},
            "favorites": {"label": "Favorites", "unit": "favorites",
                          "zefoy_key": "favorites",
                          "base_amount": 100, "base_cost": 10,
                          "min": 50, "max": 1000, "step": 50},
            "shares": {"label": "Shares", "unit": "shares", "zefoy_key": "shares",
                       "base_amount": 50, "base_cost": 8,
                       "min": 10, "max": 500, "step": 10},
        },
    },
    "instagram": {
        "label": "Instagram",
        "engine": "zefame",
        "services": {
            "ig_views": {"label": "Views", "unit": "views", "zefame": "views",
                         "base_amount": 300, "base_cost": 5,
                         "min": 300, "max": 3000, "step": 100},
        },
    },
    "x": {"label": "X", "engine": "none", "services": {}},
    "telegram": {"label": "Telegram", "engine": "none", "services": {}},
}

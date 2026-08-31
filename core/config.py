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

# Adsterra SmartLinks cycled onto the rewarded-ad buttons, one per ad slot
ADSTERRA_SMARTLINKS = [
    u.strip() for u in os.environ.get(
        "ADSTERRA_SMARTLINKS",
        "https://screwbedriddenheadline.com/ggxnb1mm?key=f9862fe342e3c188837c915e20b66334,"
        "https://screwbedriddenheadline.com/ncq9qmr5k5?key=1a118a008303fcd7dd412e69532ebcbc,"
        "https://screwbedriddenheadline.com/py7ycr6i37?key=4d31cab3fe4f7b7eddb6f1e9d3cd5ea9,"
        "https://screwbedriddenheadline.com/t78vnwxx?key=1fdc549af3cd4fc2fcf16143c19d4a9e,"
        "https://screwbedriddenheadline.com/tqbxiqs4k?key=bc091e88730e1c3d36bc96f002787282",
    ).split(",") if u.strip()
]

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
REWARDS = {
    "tiktok": {
        "label": "TikTok",
        "engine": "zefoy",
        "services": {
            "hearts": {"label": "25 Likes", "amount": 25, "cost": 10, "unit": "likes",
                       "zefoy_key": "hearts"},
            "views": {"label": "1000 Views", "amount": 1000, "cost": 5, "unit": "views",
                      "zefoy_key": "views"},
            "favorites": {"label": "100 Favorites", "amount": 100, "cost": 10, "unit": "favorites",
                          "zefoy_key": "favorites"},
            "shares": {"label": "50 Shares", "amount": 50, "cost": 8, "unit": "shares",
                       "zefoy_key": "shares"},
        },
    },
    "instagram": {
        "label": "Instagram",
        "engine": "zefame",
        "services": {
            "ig_views": {"label": "300 Views", "amount": 300, "cost": 5, "unit": "views",
                         "zefame": "views"},
        },
    },
    "x": {"label": "X", "engine": "none", "services": {}},
    "telegram": {"label": "Telegram", "engine": "none", "services": {}},
}

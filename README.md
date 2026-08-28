# COINFLOW

Coin-economy growth site: watch sponsored ads → earn coins → spend coins on real
TikTok / Instagram engagement delivered by an in-house Playwright worker pool
(Zefoy for TikTok, Zefame for Instagram).

The Zefoy DOM selectors and submit/cooldown flow were ported from the reference
project, then the captcha solver, the page-load path and the session handling
were rewritten for speed (see **Solver** below).

---

## Run it

```bash
pip install -r requirements.txt
playwright install chromium
python start.py            # http://localhost:8080
```

Docker (what Railway/Render/Fly should use):

```bash
docker build -t coinflow . && docker run -p 8080:8080 --env-file .env coinflow
```

### Environment

| Variable | Meaning |
|---|---|
| `DATABASE_URL` / `DATABASE` | Postgres DSN. **Empty → local SQLite** at `data/app.db`. |
| `SECRET_KEY` | HMAC key for signed tickets. Set it in production. |
| `ADMIN_CODE` | Promo-field code that grants admin. Default `Tagys322@`. |
| `BROWSERS` / `PAGES_PER_BROWSER` | Worker pool size. Default **4 × 3 = 12 pages**. |
| `PARALLEL_HEARTS` / `PARALLEL_FAVORITES` / `PARALLEL_SHARES` / `PARALLEL_VIEWS` | Pages allowed on the *same* order at once. Defaults **4 / 2 / 2 / 1**. |
| `ZEFAME_TIMER_WAIT` / `ZEFAME_FINAL_WAIT` | Seconds to sit through Zefame's own counter, then hold. Defaults **105 / 20**. |
| `WORKER_ENABLED` / `MONITOR_ENABLED` | Turn the browser pool / Zefoy health probe on or off. |
| `ADSENSE_CLIENT`, `ADSENSE_SLOTS` | Google AdSense client + comma-separated slot ids; slots are picked at random per ad. Empty → house ads. |
| `USE_TOR`, `PROXY_URL` | Optional egress proxying for the worker pool. |
| `AD_MIN_SECONDS`, `CAPTCHA_TOLERANCE_PX`, `CAPTCHA_INTERVAL_SECONDS`, `LINK_LOCK_SECONDS` | Tuning knobs. |

---

## Accounts — key only, no password

1. **Create Account** → confirm → the server generates a long `CF-…` string and
   shows it **once** (copy / download buttons plus an explicit "if you lose it
   you lose the account" warning).
2. **Log in** with that string. Only `sha256(salt|key)` is stored; the account row
   with a random 5-letter username is materialised on first login.
3. Logged out you can only see **Main**, which carries the pinned
   "Log in / Sign up to see other tabs" notice. `Get Coins` and `Rewards` are
   locked, `Admin` is invisible.

## Captcha (ours, not a third party)

* Photographic scene: real object cut-outs (bear / cat / dog / ball / apple / cup)
  and containers (car / basket / box) composited at random positions over a
  procedural backdrop with noise.
* The instruction ("Drag the bear into the car") is **burnt into the image**,
  jittered per character — it is never sent as text, so a scripted client has to
  do OCR + object recognition.
* Drop tolerance ≈ 78 px (~2 cm): you do not need the perfect spot, only inside
  the object's neighbourhood — and closer to the right container than to any decoy.
* Human-motion checks on the pointer trace: duration bounds, monotonic
  timestamps, path/straight-line ratio, inter-sample jitter variance, speed
  variance, max speed, drop/last-point agreement, `isTrusted`.
* **2 solves** to unlock a session, **1 wrong answer resets you back to 2**,
  and a fresh challenge is forced **every hour** on the site — the modal cannot
  be dismissed while it is mandatory.

## Get Coins — ad flow

| Pack | Ads | Reward |
|---|---|---|
| `ads1` | 1 | 5 coins |
| `ads5` | 5 back to back | 30 coins |
| `ads10` | 10 back to back | 65 coins |

Anti-bypass, all server-side:

* one slot at a time, bound to an HMAC-signed ticket,
* completion requires real wall-clock time **and** the matching number of
  heartbeats reporting a visible + focused tab; hiding the tab restarts the slot,
* replayed / out-of-order heartbeats and early `complete` calls are rejected,
* **ad-block detection is server-observed**: each slot ships a probe URL
  (`/pagead/js/adsbygoogle-<id>.js`) that every filter list blocks. If the server
  never sees that request, no coins. A DOM bait element and a fetch probe run
  client side too,
* any violation raises a suspicion counter → the user is dropped straight into a
  mandatory captcha challenge; three strikes void the run,
* multi-ad packs only pay out after the **last** ad completes.

## Rewards

Pick platform → service → pay → paste link.

| Platform | Service | Price | Engine |
|---|---|---|---|
| TikTok | 25 likes | 10 coins | Zefoy |
| TikTok | 1000 views | 5 coins | Zefoy |
| TikTok | 25 favorites / 50 shares | 10 / 8 coins | Zefoy |
| Instagram | 300 views | 5 coins | Zefame |
| X, Telegram | — | — | "Soon will update" |

* A background browser probes **zefoy.com every 5 seconds** and records which
  services are up. A service that is down on Zefoy is unselectable here (badge
  reads `DOWN — Soon will update`, cards stay greyed) until it is verified up
  again. Unknown/stale status shows `CHECKING…` and is also not purchasable.
* **Target maths**: before charging, the current public counter is read
  (tikwm → tiklydown → HTML scrape fallback chain). Buying 1000 views on a video
  with 753 views sets `target = 1753`. The worker keeps re-submitting through the
  Zefoy flow, re-reading the live counter every ~25 s, and only marks the order
  `done` when the live number **reaches or exceeds** the target. Everything else
  in the queue is served in FIFO order by the 12 worker pages.
* **5-minute global link lock**: while an order for a link is live, no other
  account, device or session can order that same link — enforced by a unique row
  in `link_locks`, not by the client.
* **Parallel fan-out**: likes orders are worked by **up to 4 worker pages at the
  same time** (favorites/shares 2, views 1) so they land much faster. Pages join
  a running order as slots free up, share the counter cache so the metric API is
  not hammered, and the moment any page reaches the target the rest see the
  order is no longer live and drop it.
* Every account gets **one free demo delivery** (no ads, no coins).
* Instagram orders open <https://zefame.com/en/free-instagram-views>, fill the
  link field and click *Get Now*. Zefame then runs its own ~1 minute counter, so
  the page waits **105 s** for it to finish and holds a further **20 s** before
  the browser leaves.

## Admin

Type the admin code into the **promo field** in Rewards — nothing on the site
mentions it. The Admin tab then appears with:

* accounts total / created, coins used globally, coins earned, ads watched,
  orders completed,
* promo-code generator (coins + number of uses + optional custom code) and the
  list of live codes,
* **live cams**: a JPEG feed per worker page (4 browsers × 3 pages) refreshed
  every second, plus the Zefoy service-monitor feed with its 5-second screenshot,
* engine log tail and the latest orders.

---

## Solver — what changed vs. the reference

| | reference | here |
|---|---|---|
| OCR passes | ~40 sequential Tesseract calls | 6 calls over 3 high-signal variants, **run in parallel** |
| Early exit | none, always scored everything | stops the moment two passes agree on a dictionary word |
| De-speckling | Python BFS per pixel | vectorised `scipy.ndimage.label` |
| Dictionary fix-up | `SequenceMatcher` over 100k words | length-bucketed bounded Levenshtein |
| Caching | in-process dict only | in-process **+ database** cache; wrong answers are actively forgotten |
| Page load | full page, images/fonts/ads loaded | request routing blocks images, fonts, CSS, and all ad/analytics hosts (captcha image whitelisted) |
| Session | captcha solved per page | **one shared browser context per browser** → solve once, all 3 pages inherit the cookie |

Typical solve time drops from ~8-15 s to well under a second on a cache miss,
and to microseconds on a repeat image.

---

## Layout

```
app.py                 Flask routes (auth, captcha, ads, rewards, admin)
start.py               entrypoint (+ optional Tor bootstrap)
core/config.py         env-driven configuration
core/db.py             Postgres/SQLite layer + schema
core/security.py       HMAC tickets, key generation, rate limiting
core/accounts.py       key-only auth, sessions, coin ledger
core/captcha.py        photo drag-and-drop captcha (build + verify)
core/ads.py            ad-run state machine and anti-bypass rules
core/solver.py         fast Zefoy word-captcha solver
core/counters.py       TikTok public counter readers
core/orders.py         pricing, link locks, order creation
core/engine.py         browser pool, Zefoy/Zefame flows, service monitor, live cams
templates/index.html   single-page UI
static/js/particles.js live coin/particle background
static/js/app.js       front-end logic
```

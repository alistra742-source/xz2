"""Headless engine: Zefoy (TikTok) + Zefame (Instagram) fulfilment, service
monitor, and the live-cam frame registry the admin panel streams from.

Layout: BROWSERS chromium instances, each with ONE browser context shared by
PAGES_PER_BROWSER pages.  Sharing the context is the big speed win — the Zefoy
captcha is solved once per browser and every page inherits the cookie, so page
2 and 3 land straight on the service menu.
"""
import asyncio
import base64
import json
import re
import threading
import time
from collections import deque

from . import config, counters, db, solver

# ---------------------------------------------------------------- live cams
FRAMES = {}                 # key -> {"jpeg": bytes, "ts": float, "label": str, "status": str}
FRAMES_LOCK = threading.Lock()
LOGS = deque(maxlen=400)
SERVICE_STATUS = {          # zefoy service availability, refreshed every 5 s
    "state": {},
    "checked": 0,
    "error": "",
}
_loop = None
_started = False


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    LOGS.append(line)
    print(f"[ENGINE] {msg}", flush=True)


def put_frame(key, jpeg, label, status=""):
    with FRAMES_LOCK:
        FRAMES[key] = {"jpeg": jpeg, "ts": time.time(), "label": label, "status": status}


def set_status(key, status):
    with FRAMES_LOCK:
        if key in FRAMES:
            FRAMES[key]["status"] = status
        else:
            FRAMES[key] = {"jpeg": None, "ts": time.time(), "label": key, "status": status}


def frame_list():
    with FRAMES_LOCK:
        return [{"key": k, "label": v["label"], "status": v.get("status", ""),
                 "ts": v["ts"], "has_image": bool(v.get("jpeg"))}
                for k, v in sorted(FRAMES.items())]


def get_frame(key):
    with FRAMES_LOCK:
        v = FRAMES.get(key)
        return v.get("jpeg") if v else None


# ---------------------------------------------------------------- zefoy DOM
ZEFOY_SERVICES = {
    "hearts": {"button": "t-hearts-button", "menu": "t-hearts-menu", "label": "Hearts"},
    "views": {"button": "t-views-button", "menu": "t-views-menu", "label": "Views"},
    "favorites": {"button": "t-favorites-button", "menu": "t-favorites-menu", "label": "Favorites"},
    "shares": {"button": "t-shares-button", "menu": "t-shares-menu", "label": "Shares"},
    "followers": {"button": "t-followers-button", "menu": "t-followers-menu", "label": "Followers"},
    "comment_hearts": {"button": "t-chearts-button", "menu": "t-chearts-menu",
                       "label": "Comment Hearts"},
}
ANY_SERVICE_BUTTON = ", ".join(f".{s['button']}" for s in ZEFOY_SERVICES.values())
CAPTCHA_IMG = "#captcha-img, img[src*='captcha'], img[src*='CAPTCHA']"
CAPTCHA_INPUT = ("input[name='captchalogin'], input.captcha-login-input, "
                 "input[placeholder='Enter the word']")
CAPTCHA_SUBMIT = "button.submit-captcha"
CAPTCHA_REFRESH = ".refresh-capthca-btn-new, [onclick*='refresh']"
SEND_BUTTON = "button.wbutton.btn-dark"

DISMISS_ALERTS_JS = "window.alert=()=>true;window.confirm=()=>true;window.print=()=>true;"

CLEAN_JS = """(() => {
  const clean = () => {
    document.querySelectorAll('iframe').forEach(el => el.remove());
    document.querySelectorAll('.fc-dialog-overlay,.fc-monetization-dialog-container,.fc-message-root,.fc-consent-root,.adsbygoogle').forEach(el => el.remove());
    document.querySelectorAll('button').forEach(b => {
      if (b.textContent.includes('Consent') && b.offsetParent !== null) b.click();
    });
  };
  clean();
  const mo = new MutationObserver(clean);
  if (document.body) mo.observe(document.body, {childList: true, subtree: true});
})();"""

MOUSE_JS = """(() => {
  function gen() {
    const pts = [];
    const n = Math.floor(Math.random() * 16) + 12;
    for (let i = 0; i < n; i++) {
      pts.push(`x=${Math.floor(Math.random()*1850)+50}&y=${Math.floor(Math.random()*950)+50}` +
               `&d=${(Math.random()*2.75+0.05).toFixed(4)}&g=${Math.random()>0.65?'True':'False'}`);
    }
    const raw = pts.join('|');
    let x = '';
    for (let i = 0; i < raw.length; i++) x += String.fromCharCode(raw.charCodeAt(i) ^ ((i % 5) + 77));
    let enc = btoa('K9x!' + x + 'K9x!').split('').reverse().join('');
    while (enc.length % 4 !== 0) enc += '=';
    return enc;
  }
  function inject() {
    const d = gen();
    document.querySelectorAll('input[type="hidden"]').forEach(i => {
      if (!i.value && i.name !== 'captcha_encoded') i.value = d;
    });
    window.__zefoyMouseData = d;
  }
  inject(); setTimeout(inject, 600); setTimeout(inject, 1800);
  document.addEventListener('submit', inject, true);
  document.addEventListener('click', e => {
    if (e.target.closest('button')) setTimeout(inject, 40);
  }, true);
})();"""

CF_COOKIE_JS = """(() => {
  const set = () => {
    const v = btoa('Kod: DOMContentLoaded\\nsource: HTMLButtonElement.onclick@https://zefoy.com/:1:1');
    document.cookie = 'cf_ob_te=' + v + '; Path=/; Expires=' +
      new Date(Date.now() + 5*3600*1000).toUTCString();
  };
  set(); setInterval(set, 60000);
})();"""

STEALTH_JS = """(() => {
  Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
  Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
  Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
  window.chrome = window.chrome || {runtime: {}};
})();"""

# Everything we never need — blocking it is the single biggest load-time win.
BLOCK_RES = {"image", "media", "font", "stylesheet"}
BLOCK_HOSTS = ("googlesyndication", "doubleclick", "googletagmanager", "google-analytics",
               "adservice", "adsystem", "facebook.net", "hotjar", "criteo", "taboola",
               "propellerads", "onclickads", "popads", "adsterra")


def is_dead(e):
    s = str(e).lower()
    return any(x in s for x in ("target page", "browser has been closed", "target closed",
                                "crash", "disposed", "connection closed", "frame was detached",
                                "net::err", "browser disconnected"))


async def _route(route, request):
    try:
        url = request.url
        rtype = request.resource_type
        if any(h in url for h in BLOCK_HOSTS):
            return await route.abort()
        if rtype in BLOCK_RES:
            # captcha image must always load
            if "captcha" in url.lower():
                return await route.continue_()
            return await route.abort()
        return await route.continue_()
    except Exception:
        try:
            await route.continue_()
        except Exception:
            pass


async def new_fast_page(context):
    page = await context.new_page()
    page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
    await page.route("**/*", _route)
    await page.add_init_script(DISMISS_ALERTS_JS + STEALTH_JS)
    return page


async def shoot(page, key, label, status="", quality=42):
    try:
        if page.is_closed():
            return
        buf = await page.screenshot(type="jpeg", quality=quality, timeout=5000)
        put_frame(key, buf, label, status)
    except Exception:
        pass


async def solve_zefoy_captcha(page, key, label, attempts=6):
    """Solve the Zefoy word captcha. Returns True when the service menu is up."""
    loop = asyncio.get_event_loop()
    for i in range(attempts):
        try:
            if await page.locator(ANY_SERVICE_BUTTON).count() > 0:
                return True
            img = page.locator(CAPTCHA_IMG).first
            try:
                await img.wait_for(state="visible", timeout=8000)
            except Exception:
                await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(1.5)
                continue
            shot = await img.screenshot()
            set_status(key, f"solving captcha {i+1}/{attempts}")
            answer = await loop.run_in_executor(None, solver.solve, shot)
            if not answer:
                try:
                    await page.locator(CAPTCHA_REFRESH).first.click(timeout=3000)
                except Exception:
                    await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(1.2)
                continue
            inp = page.locator(CAPTCHA_INPUT).first
            await inp.fill(answer, timeout=6000)
            await page.locator(CAPTCHA_SUBMIT).first.click(timeout=6000)
            try:
                await page.locator(ANY_SERVICE_BUTTON).first.wait_for(timeout=7000)
                log(f"{label}: captcha solved -> '{answer}'")
                return True
            except Exception:
                solver.forget(shot)      # never trust that answer again
                try:
                    await page.locator(CAPTCHA_REFRESH).first.click(timeout=3000)
                except Exception:
                    await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(1.0)
        except Exception as e:
            if is_dead(e):
                return False
            await asyncio.sleep(1.0)
    return False


async def open_zefoy(page, key, label):
    """Load zefoy fast and get past the captcha gate."""
    for attempt in range(4):
        try:
            set_status(key, "loading zefoy")
            await page.goto(config.ZEFOY_URL, wait_until="domcontentloaded", timeout=45000)
            for js in (CLEAN_JS, MOUSE_JS, CF_COOKIE_JS):
                try:
                    await page.evaluate(js)
                except Exception:
                    pass
            body = ""
            try:
                body = (await page.inner_text("body"))[:300].lower()
            except Exception:
                pass
            if "just a moment" in body or "502" in body or "bad gateway" in body:
                await asyncio.sleep(4 + attempt * 3)
                continue
            if await page.locator(ANY_SERVICE_BUTTON).count() > 0:
                return True
            if await solve_zefoy_captcha(page, key, label):
                return True
        except Exception as e:
            if is_dead(e):
                return False
            log(f"{label}: load error {e}")
        await asyncio.sleep(2 + attempt * 2)
    return False


def parse_wait_time(text):
    mins = re.search(r"(\d+)\s*minute", text)
    secs = re.search(r"(\d+)\s*second", text)
    total = (int(mins.group(1)) * 60 if mins else 0) + (int(secs.group(1)) if secs else 0)
    return total


# ---------------------------------------------------------------- order queue
_claim_lock = threading.Lock()


def claim_order(engine_kind):
    """Atomically grab the oldest queued order for this engine."""
    with _claim_lock:
        platforms = ("tiktok",) if engine_kind == "zefoy" else ("instagram",)
        rows = db.query(
            "SELECT * FROM orders WHERE status = 'queued' AND platform = ?"
            " ORDER BY id ASC LIMIT 1", (platforms[0],))
        if not rows:
            return None
        order = rows[0]
        db.execute("UPDATE orders SET status = 'running', updated_at = ?, message = ?"
                   " WHERE id = ? AND status = 'queued'",
                   (time.time(), "picked up by worker", order["id"]))
        fresh = db.query_one("SELECT * FROM orders WHERE id = ?", (order["id"],))
        return fresh if fresh and fresh["status"] == "running" else None


def finish_order(order_id, status, message, current=None):
    if current is None:
        db.execute("UPDATE orders SET status = ?, message = ?, updated_at = ? WHERE id = ?",
                   (status, message[:400], time.time(), order_id))
    else:
        db.execute("UPDATE orders SET status = ?, message = ?, current = ?, updated_at = ?"
                   " WHERE id = ?", (status, message[:400], current, time.time(), order_id))
    if status == "done":
        db.bump_stat("orders_done", 1)
    try:
        db.execute("DELETE FROM link_locks WHERE order_id = ?", (order_id,))
    except Exception:
        pass


def progress(order_id, current, message=""):
    db.execute("UPDATE orders SET current = ?, message = ?, updated_at = ? WHERE id = ?",
               (current, message[:400], time.time(), order_id))


# ---------------------------------------------------------------- zefoy job
async def run_zefoy_order(page, key, label, order):
    svc_key = order["service"]
    svc = ZEFOY_SERVICES.get(svc_key)
    if not svc:
        finish_order(order["id"], "failed", f"unknown service {svc_key}")
        return
    menu = f".{svc['menu']}"
    input_sel = f"{menu} input[placeholder='Enter Video URL']"
    submit_sel = f"{menu} button[type='submit']"
    link = order["link"]
    target = int(order["target"])
    metric_key = svc_key if svc_key in ("views", "hearts", "favorites", "shares") else "views"

    log(f"{label}: order #{order['id']} {svc_key} -> target {target}")
    set_status(key, f"#{order['id']} {svc_key}")

    deadline = time.time() + 3 * 3600
    last_check = 0
    current = int(order["current"] or 0)

    if not await open_zefoy(page, key, label):
        finish_order(order["id"], "queued", "worker could not reach zefoy, requeued")
        return

    # open the service panel
    try:
        btn = page.locator(f".{svc['button']}").first
        await btn.wait_for(timeout=20000)
        await btn.click(timeout=8000)
        await asyncio.sleep(1.2)
    except Exception as e:
        finish_order(order["id"], "queued", f"panel unavailable ({e})")
        return

    filled = False
    while time.time() < deadline:
        if page.is_closed():
            finish_order(order["id"], "queued", "page died, requeued")
            return
        # ---- verify progress against the real counter
        if time.time() - last_check > 25:
            last_check = time.time()
            got = await asyncio.get_event_loop().run_in_executor(
                None, counters.metric, link, metric_key, True)
            if got is not None:
                current = got
                progress(order["id"], current, f"{current}/{target} {metric_key}")
                set_status(key, f"#{order['id']} {current}/{target}")
                if current >= target:
                    finish_order(order["id"], "done", f"reached {current}/{target}", current)
                    log(f"{label}: order #{order['id']} COMPLETE {current}>={target}")
                    return

        try:
            url_input = page.locator(input_sel).first
            try:
                await url_input.wait_for(state="visible", timeout=6000)
            except Exception:
                await page.locator(f".{svc['button']}").first.click(force=True, timeout=6000)
                await url_input.wait_for(state="visible", timeout=8000)
                filled = False
            if not filled:
                await url_input.fill(link)
                filled = True
                await asyncio.sleep(0.4)
            await page.locator(submit_sel).first.click(timeout=8000)
            await asyncio.sleep(1.6)
        except Exception as e:
            if is_dead(e):
                finish_order(order["id"], "queued", "browser died, requeued")
                return
            await asyncio.sleep(2)
            continue

        # ---- read the response panel
        for _ in range(40):
            try:
                body = (await page.inner_text("body")).lower()
            except Exception:
                break
            if "too many" in body or "slow down" in body:
                await asyncio.sleep(3)
                break
            if "please wait" in body and ("minute" in body or "second" in body):
                wait_s = parse_wait_time(body) or 60
                set_status(key, f"#{order['id']} cooldown {wait_s}s")
                progress(order["id"], current, f"zefoy cooldown {wait_s}s ({current}/{target})")
                await asyncio.sleep(min(wait_s + 2, 300))
                break
            if "successfully" in body:
                log(f"{label}: submit ok (#{order['id']})")
                await asyncio.sleep(2)
                break
            # click the actual "Send" button when it appears
            clicked = False
            for sel in (f"{menu} {SEND_BUTTON}", SEND_BUTTON, f"{menu} button.wbutton"):
                try:
                    b = page.locator(sel).first
                    if await b.is_visible(timeout=800):
                        await b.click(timeout=5000)
                        clicked = True
                        await asyncio.sleep(2.5)
                        break
                except Exception:
                    continue
            if clicked:
                continue
            if await page.locator(CAPTCHA_IMG).count() > 0:
                await solve_zefoy_captcha(page, key, label)
                filled = False
                break
            await asyncio.sleep(1)
        await asyncio.sleep(1)

    got = await asyncio.get_event_loop().run_in_executor(None, counters.metric, link, metric_key, True)
    if got is not None and got >= target:
        finish_order(order["id"], "done", f"reached {got}/{target}", got)
    else:
        finish_order(order["id"], "partial",
                     f"stopped at {got if got is not None else current}/{target}")


# ---------------------------------------------------------------- zefame job
async def run_zefame_order(context, key, label, order):
    page = None
    try:
        page = await new_fast_page(context)
        set_status(key, f"#{order['id']} instagram views")
        await page.goto(config.ZEFAME_IG_VIEWS_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2)
        await shoot(page, key, label)
        # find the link field
        field = None
        for sel in ("input[type='url']", "input[name*='link']", "input[name*='url']",
                    "input[placeholder*='link' i]", "input[placeholder*='url' i]",
                    "form input[type='text']", "input[type='text']"):
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=2500):
                    field = loc
                    break
            except Exception:
                continue
        if field is None:
            finish_order(order["id"], "queued", "zefame input not found, requeued")
            return
        await field.fill(order["link"])
        await asyncio.sleep(0.6)
        clicked = False
        for sel in ("button:has-text('Get Now')", "button:has-text('Get now')",
                    "a:has-text('Get Now')", "a:has-text('Get now')",
                    "input[type='submit']", "button[type='submit']"):
            try:
                b = page.locator(sel).first
                if await b.is_visible(timeout=2500):
                    await b.click(timeout=8000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            finish_order(order["id"], "queued", "zefame button not found, requeued")
            return
        # let the site finish its job before we close the browser
        for i in range(config.ZEFAME_WAIT_SECONDS):
            await asyncio.sleep(1)
            if i % 5 == 0:
                await shoot(page, key, label, f"#{order['id']} waiting {config.ZEFAME_WAIT_SECONDS - i}s")
        body = ""
        try:
            body = (await page.inner_text("body")).lower()
        except Exception:
            pass
        ok = any(w in body for w in ("success", "delivered", "processing", "thank", "order"))
        finish_order(order["id"], "done" if ok else "partial",
                     "submitted to zefame" if ok else "submitted, response unclear")
        log(f"{label}: zefame order #{order['id']} submitted")
    except Exception as e:
        finish_order(order["id"], "queued", f"zefame error: {e}")
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


# ---------------------------------------------------------------- workers
async def page_worker(context, browser_idx, page_idx):
    key = f"b{browser_idx+1}p{page_idx+1}"
    label = f"Browser {browser_idx+1} · Page {page_idx+1}"
    page = None
    set_status(key, "idle")
    cam_stop = asyncio.Event()

    async def cam():
        while not cam_stop.is_set():
            if page is not None and not page.is_closed():
                await shoot(page, key, label)
            await asyncio.sleep(0.7)

    cam_task = asyncio.ensure_future(cam())
    try:
        while True:
            order = None
            try:
                order = await asyncio.get_event_loop().run_in_executor(None, claim_order, "zefoy")
            except Exception as e:
                log(f"{label}: claim error {e}")
            if order is None:
                # instagram jobs are handled by page 1 of each browser
                if page_idx == 0:
                    try:
                        ig = await asyncio.get_event_loop().run_in_executor(
                            None, claim_order, "zefame")
                    except Exception:
                        ig = None
                    if ig:
                        await run_zefame_order(context, key, label, ig)
                        continue
                set_status(key, "idle")
                await asyncio.sleep(3)
                continue
            if page is None or page.is_closed():
                page = await new_fast_page(context)
            try:
                await run_zefoy_order(page, key, label, order)
            except Exception as e:
                log(f"{label}: order error {e}")
                finish_order(order["id"], "queued", f"worker error: {e}")
                try:
                    if page and not page.is_closed():
                        await page.close()
                except Exception:
                    pass
                page = None
    except asyncio.CancelledError:
        pass
    finally:
        cam_stop.set()
        cam_task.cancel()


async def browser_worker(pw, browser_idx):
    from playwright.async_api import Error as PWError
    args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-extensions",
            "--disable-background-networking", "--disable-sync", "--no-first-run",
            "--disable-features=TranslateUI,BlinkGenPropertyTrees",
            "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
            "--js-flags=--max-old-space-size=192", "--mute-audio", "--disable-logging"]
    launch = {"headless": True, "args": args}
    if config.USE_TOR:
        launch["proxy"] = {"server": f"socks5://127.0.0.1:{config.TOR_BASE_PORT + browser_idx}"}
    elif config.PROXY_URL:
        launch["proxy"] = {"server": config.PROXY_URL}

    while True:
        browser = None
        try:
            browser = await pw.chromium.launch(**launch)
            context = await browser.new_context(
                viewport={"width": 1000, "height": 680},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
                locale="en-US",
            )
            await context.add_init_script(STEALTH_JS)
            log(f"Browser {browser_idx+1} up ({config.PAGES_PER_BROWSER} pages, shared session)")
            tasks = [asyncio.ensure_future(page_worker(context, browser_idx, i))
                     for i in range(config.PAGES_PER_BROWSER)]
            await asyncio.gather(*tasks)
        except Exception as e:
            log(f"Browser {browser_idx+1} crashed: {e}")
        finally:
            try:
                if browser:
                    await browser.close()
            except Exception:
                pass
        await asyncio.sleep(8)


# ---------------------------------------------------------------- monitor
async def status_monitor(pw):
    """Every 5 s: screenshot zefoy and record which services are up."""
    key = "monitor"
    label = "Zefoy service monitor"
    args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--mute-audio"]
    launch = {"headless": True, "args": args}
    if config.USE_TOR:
        launch["proxy"] = {"server": f"socks5://127.0.0.1:{config.TOR_BASE_PORT + 9}"}
    elif config.PROXY_URL:
        launch["proxy"] = {"server": config.PROXY_URL}

    while True:
        browser = None
        try:
            browser = await pw.chromium.launch(**launch)
            context = await browser.new_context(viewport={"width": 1000, "height": 760})
            page = await new_fast_page(context)
            ok = await open_zefoy(page, key, label)
            if not ok:
                SERVICE_STATUS["error"] = "zefoy unreachable"
                raise RuntimeError("zefoy unreachable")
            SERVICE_STATUS["error"] = ""
            while True:
                try:
                    state = await page.evaluate("""(map) => {
                        const out = {};
                        for (const [name, cls] of Object.entries(map)) {
                            const el = document.querySelector('.' + cls);
                            if (!el) { out[name] = 'down'; continue; }
                            const txt = (el.innerText || '').toLowerCase();
                            const disabled = el.disabled || el.classList.contains('disabled') ||
                                             el.getAttribute('aria-disabled') === 'true';
                            const bad = /unavailable|not working|offline|soon|maintenance/.test(txt);
                            out[name] = (disabled || bad) ? 'down' : 'up';
                        }
                        return out;
                    }""", {k: v["button"] for k, v in ZEFOY_SERVICES.items()})
                    SERVICE_STATUS["state"] = state
                    SERVICE_STATUS["checked"] = time.time()
                except Exception as e:
                    if is_dead(e):
                        raise
                    SERVICE_STATUS["error"] = str(e)[:120]
                await shoot(page, key, label,
                            "up: " + ",".join(k for k, v in SERVICE_STATUS["state"].items()
                                              if v == "up"), quality=45)
                # a captcha can reappear at any time — solve it and keep going
                try:
                    if await page.locator(CAPTCHA_IMG).count() > 0:
                        await solve_zefoy_captcha(page, key, label)
                except Exception:
                    pass
                await asyncio.sleep(config.MONITOR_INTERVAL)
        except Exception as e:
            SERVICE_STATUS["error"] = str(e)[:160]
            log(f"Monitor restart: {e}")
        finally:
            try:
                if browser:
                    await browser.close()
            except Exception:
                pass
        await asyncio.sleep(10)


def service_status():
    """What the UI consumes. Unknown == treat as down but say 'checking'."""
    age = time.time() - (SERVICE_STATUS["checked"] or 0)
    fresh = age < 60
    out = {}
    for name in ZEFOY_SERVICES:
        raw = SERVICE_STATUS["state"].get(name)
        if not fresh or raw is None:
            out[name] = "checking"
        else:
            out[name] = raw
    return {"services": out, "age": round(age, 1), "error": SERVICE_STATUS["error"],
            "monitor_running": fresh}


# ---------------------------------------------------------------- bootstrap
async def _main():
    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        log(f"playwright unavailable, engine disabled: {e}")
        return
    async with async_playwright() as pw:
        tasks = []
        if config.MONITOR_ENABLED:
            tasks.append(asyncio.ensure_future(status_monitor(pw)))
        if config.WORKER_ENABLED:
            for i in range(config.BROWSERS):
                tasks.append(asyncio.ensure_future(browser_worker(pw, i)))
                await asyncio.sleep(1.5)
        if not tasks:
            return
        await asyncio.gather(*tasks)


def start():
    global _loop, _started
    if _started:
        return
    _started = True

    def runner():
        global _loop
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        try:
            _loop.run_until_complete(_main())
        except Exception as e:
            log(f"engine stopped: {e}")

    threading.Thread(target=runner, daemon=True, name="engine").start()
    log(f"engine starting: {config.BROWSERS} browsers x {config.PAGES_PER_BROWSER} pages")

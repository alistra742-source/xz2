"""Reward orders: pricing, global link locks, queueing."""
import hashlib
import re
import time

from . import config, counters, db, engine


def link_key(platform, service, link):
    base = (link or "").strip().lower().split("?")[0].rstrip("/")
    return hashlib.sha256(f"{platform}|{base}".encode()).hexdigest()[:32]


def lock_state(platform, service, link):
    key = link_key(platform, service, link)
    row = db.query_one("SELECT * FROM link_locks WHERE link_key = ?", (key,))
    if not row:
        return None
    remaining = float(row["locked_until"]) - time.time()
    if remaining <= 0:
        db.execute("DELETE FROM link_locks WHERE link_key = ?", (key,))
        return None
    return int(remaining)


def take_lock(platform, service, link, order_id, seconds=None):
    key = link_key(platform, service, link)
    until = time.time() + (seconds or config.LINK_LOCK_SECONDS)
    try:
        db.execute("INSERT INTO link_locks (link_key, locked_until, order_id) VALUES (?, ?, ?)",
                   (key, until, order_id))
        return True
    except Exception:
        row = db.query_one("SELECT * FROM link_locks WHERE link_key = ?", (key,))
        if row and float(row["locked_until"]) <= time.time():
            db.execute("UPDATE link_locks SET locked_until = ?, order_id = ? WHERE link_key = ?",
                       (until, order_id, key))
            return True
        return False


def catalogue():
    """Public menu with live zefoy availability folded in."""
    status = engine.service_status()
    out = {}
    for pid, plat in config.REWARDS.items():
        services = []
        for sid, svc in plat["services"].items():
            if plat["engine"] == "zefoy":
                state = status["services"].get(svc["zefoy_key"], "checking")
            else:
                state = "up"
            services.append({
                "id": sid, "label": svc["label"], "cost": svc["cost"],
                "amount": svc["amount"], "unit": svc["unit"], "state": state,
            })
        out[pid] = {
            "label": plat["label"],
            "engine": plat["engine"],
            "available": bool(services),
            "services": services,
        }
    out["_monitor"] = status
    return out


def create_order(account, platform, service_id, link):
    """Validate -> price -> charge -> enqueue. Returns (order, error)."""
    from . import accounts

    plat = config.REWARDS.get(platform)
    if not plat or not plat["services"]:
        return None, "This platform is not available yet — Soon will update."
    svc = plat["services"].get(service_id)
    if not svc:
        return None, "Unknown service."

    link = (link or "").strip()
    if plat["engine"] == "zefoy":
        if not counters.valid_tiktok_link(link):
            return None, "That does not look like a TikTok video link."
        status = engine.service_status()
        if status["services"].get(svc["zefoy_key"]) != "up":
            return None, "That service is currently DOWN on the provider. Try again shortly."
    else:
        if not counters.valid_instagram_link(link):
            return None, "That does not look like an Instagram link."

    busy = lock_state(platform, service_id, link)
    if busy:
        return None, f"This link is locked for another {busy}s (one order per link every 5 minutes)."

    baseline, target = 0, 0
    metric_key = svc.get("zefoy_key")
    if plat["engine"] == "zefoy":
        stats = counters.tiktok_stats(link, force=True)
        if not stats:
            return None, "Could not read that video's public counters. Check the link is public."
        baseline = int(stats.get(metric_key, 0))
        target = baseline + int(svc["amount"])

    # every account gets exactly one free demo delivery, no ads, no coins
    fresh = accounts.get_account(account["id"])
    used_demo = bool(fresh["demo_used"])
    charged = int(svc["cost"])
    if not used_demo:
        charged = 0
        db.execute("UPDATE accounts SET demo_used = ? WHERE id = ?",
                   (True if db.IS_PG else 1, account["id"]))
    elif not accounts.try_spend(account["id"], int(svc["cost"])):
        return None, "Not enough coins."

    now = time.time()
    order_id = db.insert_returning_id(
        "INSERT INTO orders (account_id, platform, service, link, link_key, cost, amount,"
        " baseline, target, current, status, message, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
        (account["id"], platform, metric_key or service_id, link,
         link_key(platform, service_id, link), charged, int(svc["amount"]),
         baseline, target, baseline, "queued", now, now),
    )
    if not take_lock(platform, service_id, link, order_id):
        if charged:
            accounts.add_coins(account["id"], charged)
        db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        return None, "This link was just locked by another order. Try again in 5 minutes."

    return db.query_one("SELECT * FROM orders WHERE id = ?", (order_id,)), None


def user_orders(account_id, limit=15):
    rows = db.query("SELECT * FROM orders WHERE account_id = ? ORDER BY id DESC LIMIT ?",
                    (account_id, limit))
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "platform": r["platform"], "service": r["service"],
            "link": r["link"], "status": r["status"], "message": r["message"],
            "baseline": r["baseline"], "target": r["target"], "current": r["current"],
            "amount": r["amount"], "cost": r["cost"], "created_at": r["created_at"],
        })
    return out

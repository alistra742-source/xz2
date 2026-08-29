"""Reward orders: pricing, atomic charging, link locks, queueing."""
import hashlib
import math
import re
import time

from . import config, counters, db, engine

# anti-replay request id: sent by the client on every submit and stored on the
# order, so a double-click, a retry or a second device can never double-charge.
_NONCE_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")

# statuses that count as "an order in flight" for the per-account cap
ACTIVE_STATUSES = ("queued", "running")


class OrderError(Exception):
    """Raised inside the order transaction; rolls it back and surfaces the
    message to the user."""


def link_key(platform, service, link):
    base = (link or "").strip().lower().split("?")[0].rstrip("/")
    return hashlib.sha256(f"{platform}|{base}".encode()).hexdigest()[:32]


def price_for(svc, amount):
    """Quantise a requested amount to the service step (clamped to min/max)
    and price it linearly off base_amount/base_cost, rounded up.

    Returns (amount, cost).  The server always recomputes this — the client's
    number is only a suggestion.
    """
    try:
        amount = int(amount)
    except Exception:
        amount = int(svc["base_amount"])
    step = max(1, int(svc.get("step", 1)))
    amount -= amount % step
    amount = max(int(svc["min"]), min(int(svc["max"]), amount))
    cost = max(1, math.ceil(amount / int(svc["base_amount"]) * int(svc["base_cost"])))
    return amount, cost


def ig_eta_seconds(amount):
    """Instagram views run in ~300-view batches, one batch per 5.3 min cycle."""
    runs = max(1, math.ceil(int(amount) / config.ZEFAME_VIEWS_PER_RUN))
    return runs * config.ZEFAME_CYCLE_SECONDS


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
            entry = {
                "id": sid, "label": svc["label"], "cost": svc["base_cost"],
                "amount": svc["base_amount"], "unit": svc["unit"], "state": state,
                "base_amount": svc["base_amount"], "base_cost": svc["base_cost"],
                "min": svc["min"], "max": svc["max"], "step": svc["step"],
            }
            if plat["engine"] == "zefame":
                entry["per_run"] = config.ZEFAME_VIEWS_PER_RUN
                entry["cycle_seconds"] = config.ZEFAME_CYCLE_SECONDS
            services.append(entry)
        out[pid] = {
            "label": plat["label"],
            "engine": plat["engine"],
            "available": bool(services),
            "services": services,
        }
    out["_monitor"] = status
    return out


def create_order(account, platform, service_id, link, nonce="", amount=None):
    """Validate -> price -> charge -> enqueue, all in ONE transaction.

    Anti-bypass guarantees:

    * **Atomic charge.** The coin deduction is a conditional
      ``UPDATE ... WHERE coins >= cost`` executed inside the same transaction
      as the order insert. Two purchases fired at the same instant (two
      devices, double-click, a replayed request) serialize on the account row:
      the second one sees the first one's balance and fails cleanly.
    * **Replay-proof.** Each submit carries a unique request id; a nonce can
      only ever create one order, so retries never double-charge.
    * **One active order per account.** While an order is queued or running,
      new orders are refused, which makes the "buy on two devices at once"
      attack impossible even with different links.
    * **Link lock + everything else** live in the same transaction, so any
      conflict rolls the charge back with it.

    Returns (order, error).
    """
    from . import accounts

    plat = config.REWARDS.get(platform)
    if not plat or not plat["services"]:
        return None, "This platform is not available yet — Soon will update."
    svc = plat["services"].get(service_id)
    if not svc:
        return None, "Unknown service."

    link = (link or "").strip()
    if len(link) > 512 or any(ord(c) < 32 for c in link):
        return None, "That link is not valid."
    if not _NONCE_RE.match((nonce or "").strip()):
        return None, "Missing or invalid request id — reload the page and try again."

    if plat["engine"] == "zefoy":
        if not counters.valid_tiktok_link(link):
            return None, "That does not look like a TikTok video link."
        status = engine.service_status()
        if status["services"].get(svc["zefoy_key"]) != "up":
            return None, "That service is currently DOWN on the provider. Try again shortly."
    else:
        if not counters.valid_instagram_link(link):
            return None, "That does not look like an Instagram link."

    qty, cost = price_for(svc, amount if amount is not None else svc["base_amount"])

    baseline, target = 0, 0
    metric_key = svc.get("zefoy_key")
    if plat["engine"] == "zefoy":
        stats = counters.tiktok_stats(link, force=True)
        if not stats:
            return None, "Could not read that video's public counters. Check the link is public."
        baseline = int(stats.get(metric_key, 0))
        target = baseline + qty

    key = link_key(platform, service_id, link)
    lock_until = time.time() + config.LINK_LOCK_SECONDS
    now = time.time()
    try:
        with db.transaction() as cur:
            # 1) serialize this account's orders: every order starts with a
            #    write on the account row, so concurrent requests queue up
            #    behind each other instead of racing (Postgres row lock /
            #    SQLite write lock) — the second buyer always sees the first
            #    one's committed balance and order.
            cur.execute(db._q("UPDATE accounts SET last_seen = last_seen WHERE id = ?"),
                        (account["id"],))
            fresh = cur.execute(db._q("SELECT * FROM accounts WHERE id = ?"),
                                (account["id"],)).fetchone()

            # 2) replay guard: a nonce may only ever create one order
            dup = cur.execute(db._q("SELECT id FROM orders WHERE nonce = ?"),
                              (nonce,)).fetchone()
            if dup:
                raise OrderError("This order was already submitted — check your orders list.")

            # 3) one active order per account at a time
            active = cur.execute(db._q("SELECT id FROM orders WHERE account_id = ?"
                                       " AND status IN ('queued', 'running') LIMIT 1"),
                                 (account["id"],)).fetchone()
            if active:
                raise OrderError("You already have an order in progress. "
                                 "Wait for it to finish, then order again.")

            # 4) global per-link lock (one order per link every 5 minutes)
            locked = cur.execute(db._q("SELECT locked_until FROM link_locks WHERE link_key = ?"),
                                 (key,)).fetchone()
            if locked and float(locked["locked_until"]) > time.time():
                raise OrderError(f"This link is locked for another "
                                 f"{int(float(locked['locked_until']) - time.time())}s "
                                 f"(one order per link every 5 minutes).")

            # 5) free demo once, otherwise charge — the spend is conditional
            #    and part of this transaction, so it can never overdraw
            charged = cost
            if not bool(fresh["demo_used"]):
                charged = 0
                cur.execute(db._q("UPDATE accounts SET demo_used = ? WHERE id = ?"),
                            (True if db.IS_PG else 1, account["id"]))
            elif not accounts.try_spend_on(cur, account["id"], charged):
                raise OrderError("Not enough coins.")

            # 6) insert the order, then take the link lock; either conflict
            #    rolls the whole transaction back (charge included)
            try:
                row = cur.execute(db._q(
                    "INSERT INTO orders (account_id, platform, service, link, link_key,"
                    " cost, amount, baseline, target, current, status, message, nonce,"
                    " created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)"
                    " RETURNING id"),
                    (account["id"], platform, metric_key or service_id, link, key, charged,
                     qty, baseline, target, baseline, "", nonce, now, now)).fetchone()
                order_id = row["id"]
            except Exception:
                raise OrderError("This order was already submitted — check your orders list.")

            try:
                cur.execute(db._q("INSERT INTO link_locks (link_key, locked_until, order_id)"
                                  " VALUES (?, ?, ?)"),
                            (key, lock_until, order_id))
            except Exception:
                raise OrderError("This link was just locked by another order. Try again in 5 minutes.")
    except OrderError as e:
        return None, str(e)

    if charged:
        db.bump_stat("coins_spent", charged)
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

"""Ad-watch reward engine.

Anti-bypass model (all state lives on the server):
  * one ad slot at a time, bound to a signed nonce,
  * the slot only completes after `AD_MIN_SECONDS` of *wall clock* time AND a
    matching number of heartbeats that reported a visible, focused tab,
  * an ad-blocker probe file must actually be fetched by the browser
    (server observes the request, the client cannot fake it),
  * any inconsistency raises the suspicion counter which forces a captcha
    challenge before the run may continue; three strikes voids the run.
"""
import secrets
import time

from . import config, db, security

_bait_hits = {}          # bait_id -> timestamp the browser actually fetched the probe
_BAIT_TTL = 300


def register_bait(bait_id):
    now = time.time()
    _bait_hits[bait_id] = now
    for k, v in list(_bait_hits.items()):
        if now - v > _BAIT_TTL:
            _bait_hits.pop(k, None)


def bait_seen(bait_id):
    ts = _bait_hits.get(bait_id)
    return bool(ts and time.time() - ts < _BAIT_TTL)


def open_session(account_id, pack):
    spec = config.COIN_PACKS.get(pack)
    if not spec:
        return None, "unknown-pack"
    db.execute("UPDATE ad_sessions SET state = 'void' WHERE account_id = ? AND state IN ('open','challenge')",
               (account_id,))
    sid = db.insert_returning_id(
        "INSERT INTO ad_sessions (account_id, pack, required, done, coins, state, created_at)"
        " VALUES (?, ?, ?, 0, ?, 'open', ?)",
        (account_id, pack, spec["ads"], spec["coins"], time.time()),
    )
    return db.query_one("SELECT * FROM ad_sessions WHERE id = ?", (sid,)), None


def get_open(account_id):
    return db.query_one(
        "SELECT * FROM ad_sessions WHERE account_id = ? AND state IN ('open','challenge')"
        " ORDER BY id DESC", (account_id,))


def _flag(run, reason):
    susp = int(run["suspicion"] or 0) + 1
    state = "void" if susp >= 3 else "challenge"
    db.execute("UPDATE ad_sessions SET suspicion = ?, state = ?, slot_nonce = NULL WHERE id = ?",
               (susp, state, run["id"]))
    return {"ok": False, "state": state, "reason": reason, "suspicion": susp}


def issue_slot(run):
    """Hand out the next ad slot."""
    if run["state"] != "open":
        return None, run["state"]
    if int(run["done"]) >= int(run["required"]):
        return None, "finished"
    nonce = secrets.token_urlsafe(18)
    bait_id = secrets.token_urlsafe(12)
    now = time.time()
    db.execute("UPDATE ad_sessions SET slot_nonce = ?, slot_start = ?, slot_beats = 0, slot_last = ?"
               " WHERE id = ?", (nonce, now, now, run["id"]))
    ticket = security.sign({"run": run["id"], "nonce": nonce, "idx": int(run["done"]),
                            "bait": bait_id}, ttl=1800)
    return {
        "ticket": ticket,
        "bait": bait_id,
        "bait_url": f"/pagead/js/adsbygoogle-{bait_id}.js",
        "index": int(run["done"]) + 1,
        "total": int(run["required"]),
        "seconds": config.AD_MIN_SECONDS,
        "beat": config.AD_HEARTBEAT_SECONDS,
    }, None


def heartbeat(run, ticket, visible, focused, seq):
    data = security.unsign(ticket or "")
    if not data or data.get("run") != run["id"] or data.get("nonce") != run["slot_nonce"]:
        return _flag(run, "bad-ticket")
    now = time.time()
    last = float(run["slot_last"] or 0)
    beats = int(run["slot_beats"] or 0)
    if not visible or not focused:
        # tab hidden -> the clock simply does not advance, but reset the slot so
        # the ad has to be watched in one continuous stretch
        db.execute("UPDATE ad_sessions SET slot_start = ?, slot_beats = 0, slot_last = ? WHERE id = ?",
                   (now, now, run["id"]))
        return {"ok": True, "paused": True, "elapsed": 0}
    gap = now - last
    if gap > config.AD_HEARTBEAT_SECONDS * (config.AD_MAX_MISSED_BEATS + 1) + 1.5:
        db.execute("UPDATE ad_sessions SET slot_start = ?, slot_beats = 0, slot_last = ? WHERE id = ?",
                   (now, now, run["id"]))
        return {"ok": True, "paused": True, "elapsed": 0, "reason": "gap"}
    if seq is not None and int(seq) < beats:
        return _flag(run, "replayed-beat")
    beats += 1
    db.execute("UPDATE ad_sessions SET slot_beats = ?, slot_last = ? WHERE id = ?",
               (beats, now, run["id"]))
    return {"ok": True, "elapsed": round(now - float(run["slot_start"] or now), 2), "beats": beats}


def complete_slot(run, ticket, bait_id, client_flags):
    data = security.unsign(ticket or "")
    if not data or data.get("run") != run["id"] or data.get("nonce") != run["slot_nonce"]:
        return _flag(run, "bad-ticket")
    if data.get("bait") != bait_id:
        return _flag(run, "bait-mismatch")

    elapsed = time.time() - float(run["slot_start"] or 0)
    need = config.AD_MIN_SECONDS
    if elapsed < need - 0.6:
        return _flag(run, "too-fast")
    expected_beats = int(need / config.AD_HEARTBEAT_SECONDS) - config.AD_MAX_MISSED_BEATS
    if int(run["slot_beats"] or 0) < max(1, expected_beats):
        return _flag(run, "missing-heartbeats")
    if not bait_seen(bait_id):
        return {"ok": False, "state": "adblock", "reason": "adblock-detected"}
    if client_flags.get("blocked"):
        return {"ok": False, "state": "adblock", "reason": "adblock-detected"}

    done = int(run["done"]) + 1
    finished = done >= int(run["required"])
    db.execute("UPDATE ad_sessions SET done = ?, slot_nonce = NULL, state = ? WHERE id = ?",
               (done, "paid" if finished else "open", run["id"]))
    db.execute("UPDATE accounts SET ads_watched = ads_watched + 1 WHERE id = ?", (run["account_id"],))
    db.bump_stat("ads_watched", 1)

    payload = {"ok": True, "done": done, "required": int(run["required"]), "finished": finished}
    if finished:
        from . import accounts
        accounts.add_coins(run["account_id"], int(run["coins"]))
        payload["coins_awarded"] = int(run["coins"])
    return payload


def clear_challenge(run):
    db.execute("UPDATE ad_sessions SET state = 'open', slot_nonce = NULL WHERE id = ?", (run["id"],))

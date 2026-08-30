"""COINFLOW — Flask entrypoint."""
import os
import secrets
import time

from flask import (Flask, Response, jsonify, make_response, render_template,
                   request, send_from_directory)

from core import accounts, ads, captcha, config, db, engine, orders, security

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["JSON_SORT_KEYS"] = False

COOKIE = "cf_sid"


# ------------------------------------------------------------------ helpers
def current():
    tok = request.cookies.get(COOKIE, "")
    sess = accounts.get_session(tok)
    if not sess:
        return None, None
    acct = accounts.get_account(sess["account_id"])
    if not acct:
        return None, None
    return sess, acct


def need_login():
    sess, acct = current()
    if not acct:
        return None, None, (jsonify({"error": "login-required"}), 401)
    return sess, acct, None


def need_verified():
    sess, acct, err = need_login()
    if err:
        return None, None, err
    if accounts.captcha_due(sess):
        return None, None, (jsonify({"error": "captcha-required",
                                     "pending": int(sess["pending_solves"] or 0) or
                                     config.CAPTCHA_REQUIRED_SOLVES}), 403)
    return sess, acct, None


def need_admin():
    sess, acct, err = need_login()
    if err:
        return None, None, err
    if not acct["is_admin"]:
        return None, None, (jsonify({"error": "forbidden"}), 403)
    return sess, acct, None


@app.after_request
def headers(resp):
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------ pages
@app.route("/")
def index():
    return render_template(
        "index.html",
        site_name=config.SITE_NAME,
        adsterra_socialbar_src=config.ADSTERRA_SOCIALBAR_SRC,
        adsterra_popunder_src=config.ADSTERRA_POPUNDER_SRC,
    )


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "ts": time.time()})


@app.get("/ads.txt")
def ads_txt():
    return app.send_static_file("ads.txt")


@app.get("/1495328.txt")
def rollerads_verification():
    return Response("1495328\n", mimetype="text/plain")


# ------------------------------------------------------------------ auth
@app.post("/api/account/new")
def api_account_new():
    ip = security.client_ip(request)
    if not security.limiter.hit(f"newkey:{ip}", 8, 3600):
        return jsonify({"error": "rate-limited"}), 429
    key = accounts.create_account_key()
    return jsonify({"key": key})


@app.post("/api/login")
def api_login():
    ip = security.client_ip(request)
    if not security.limiter.hit(f"login:{ip}", 20, 600):
        return jsonify({"error": "rate-limited"}), 429
    key = (request.json or {}).get("key", "")
    tok, acct, created = accounts.login_with_key(
        key, ip, request.headers.get("User-Agent", ""))
    if not tok:
        return jsonify({"error": "invalid-key"}), 400
    sess = accounts.get_session(tok)
    resp = make_response(jsonify({"account": accounts.public_account(acct, sess),
                                  "created": created}))
    resp.set_cookie(COOKIE, tok, httponly=True, samesite="Lax",
                    secure=request.is_secure, max_age=60 * 60 * 24 * 30)
    return resp


@app.post("/api/logout")
def api_logout():
    accounts.logout(request.cookies.get(COOKIE, ""))
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/api/me")
def api_me():
    sess, acct = current()
    if not acct:
        return jsonify({"account": None, "site": config.SITE_NAME})
    accounts.touch(acct["id"])
    return jsonify({"account": accounts.public_account(acct, sess)})


# ------------------------------------------------------------------ captcha
@app.post("/api/captcha/new")
def api_captcha_new():
    sess, acct, err = need_login()
    if err:
        return err
    ip = security.client_ip(request)
    if not security.limiter.hit(f"cap:{ip}", 40, 600):
        return jsonify({"error": "rate-limited"}), 429
    purpose = (request.json or {}).get("purpose", "verify")
    payload = captcha.build_challenge(sess["token"], purpose)
    payload["pending"] = int(sess["pending_solves"] or 0)
    return jsonify(payload)


@app.post("/api/captcha/solve")
def api_captcha_solve():
    sess, acct, err = need_login()
    if err:
        return err
    body = request.json or {}
    ok, why = captcha.verify(body.get("id"), body.get("x"), body.get("y"),
                             body.get("trace"), bool(body.get("trusted")))
    pending = accounts.mark_captcha_progress(sess["token"], ok)
    if ok and pending == 0:
        run = ads.get_open(acct["id"])
        if run and run["state"] == "challenge":
            ads.clear_challenge(run)
    return jsonify({"ok": ok, "reason": why, "pending": pending})


# ------------------------------------------------------------------ ads
def _ad_creative():
    """Describe the page-level Adsterra placement used during this slot."""
    return {"type": "adsterra"}


@app.get("/api/ads/state")
def api_ads_state():
    sess, acct, err = need_login()
    if err:
        return err
    run = ads.get_open(acct["id"])
    return jsonify({
        "run": None if not run else {
            "id": run["id"], "required": run["required"],
            "done": run["done"], "state": run["state"],
        },
    })


@app.post("/api/ads/start")
def api_ads_start():
    sess, acct, err = need_verified()
    if err:
        return err
    # one open-ended session fills the wait timer with ads; the client ends it
    # when the order dispatches.
    if not security.limiter.hit(f"adstart:{acct['id']}", 30, 3600):
        return jsonify({"error": "rate-limited"}), 429
    run, e = ads.open_session(acct["id"])
    if e:
        return jsonify({"error": e}), 400
    return jsonify({"run": {"id": run["id"], "required": run["required"], "done": 0,
                            "state": run["state"]}})


@app.post("/api/ads/slot")
def api_ads_slot():
    sess, acct, err = need_verified()
    if err:
        return err
    run = ads.get_open(acct["id"])
    if not run:
        return jsonify({"error": "no-run"}), 400
    if run["state"] == "challenge":
        return jsonify({"error": "challenge-required"}), 403
    slot, e = ads.issue_slot(run)
    if e:
        return jsonify({"error": e}), 400
    slot["creative"] = _ad_creative()
    return jsonify(slot)


@app.post("/api/ads/beat")
def api_ads_beat():
    sess, acct, err = need_verified()
    if err:
        return err
    run = ads.get_open(acct["id"])
    if not run:
        return jsonify({"error": "no-run"}), 400
    body = request.json or {}
    res = ads.heartbeat(run, body.get("ticket"), bool(body.get("visible")),
                        bool(body.get("focused")), body.get("seq"))
    return jsonify(res)


@app.post("/api/ads/complete")
def api_ads_complete():
    sess, acct, err = need_verified()
    if err:
        return err
    run = ads.get_open(acct["id"])
    if not run:
        return jsonify({"error": "no-run"}), 400
    body = request.json or {}
    res = ads.complete_slot(run, body.get("ticket"), body.get("bait"),
                            body.get("flags") or {})
    return jsonify(res)


@app.get("/pagead/js/adsbygoogle-<bait>.js")
def ad_bait(bait):
    """Ad-blocker probe: filter lists kill this path, so a fetch that reaches
    the server proves no blocker is active. Purely server-observed."""
    ads.register_bait(bait)
    return Response("window.__cf_ad_probe=1;", mimetype="application/javascript",
                    headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ rewards
@app.get("/api/rewards/catalogue")
def api_catalogue():
    return jsonify(orders.catalogue())


@app.post("/api/rewards/order")
def api_order():
    sess, acct, err = need_verified()
    if err:
        return err
    ip = security.client_ip(request)
    if not security.limiter.hit(f"orderip:{ip}", 10, 600):
        return jsonify({"error": "rate-limited"}), 429
    body = request.json or {}
    if not security.limiter.hit(f"order:{acct['id']}", 12, 600):
        return jsonify({"error": "rate-limited"}), 429
    order, e = orders.create_order(acct, body.get("platform"), body.get("service"),
                                   body.get("link"), body.get("nonce"),
                                   amount=body.get("amount"), ip=ip)
    if e:
        return jsonify({"error": e}), 400
    return jsonify({"order": {"id": order["id"], "status": order["status"],
                              "baseline": order["baseline"], "target": order["target"],
                              "amount": order["amount"],
                              "ready_at": order["ready_at"],
                              "wait": int(order["ready_at"] - time.time())}})


@app.get("/api/orders")
def api_orders():
    sess, acct, err = need_login()
    if err:
        return err
    return jsonify({"orders": orders.user_orders(acct["id"])})


@app.post("/api/promo")
def api_promo():
    sess, acct, err = need_verified()
    if err:
        return err
    code = ((request.json or {}).get("code") or "").strip()
    if not security.limiter.hit(f"promo:{acct['id']}", 12, 600):
        return jsonify({"error": "rate-limited"}), 429
    if not code:
        return jsonify({"error": "Enter a code."}), 400

    if secrets.compare_digest(code, config.ADMIN_CODE):
        accounts.grant_admin(acct["id"])
        return jsonify({"ok": True, "admin": True, "message": "Access granted."})
    return jsonify({"error": "Invalid code."}), 400


# ------------------------------------------------------------------ admin
@app.get("/api/admin/stats")
def api_admin_stats():
    sess, acct, err = need_admin()
    if err:
        return err
    stats = db.get_stats()
    total_accounts = (db.query_one("SELECT COUNT(*) AS c FROM accounts") or {}).get("c", 0)
    active_orders = (db.query_one("SELECT COUNT(*) AS c FROM orders WHERE status IN"
                                  " ('queued', 'running')") or {}).get("c", 0)
    completed_orders = (db.query_one("SELECT COUNT(*) AS c FROM orders WHERE status IN"
                                     " ('done', 'partial', 'failed')") or {}).get("c", 0)
    live_orders = db.query(
        "SELECT id, account_id, platform, service, status, current, target, message"
        " FROM orders ORDER BY id DESC LIMIT 25")
    codes = db.query("SELECT * FROM promo_codes ORDER BY created_at DESC LIMIT 30")
    return jsonify({
        "accounts_total": int(total_accounts),
        "accounts_created": stats.get("accounts_created", 0),
        "ads_watched": stats.get("ads_watched", 0),
        "orders_done": stats.get("orders_done", 0),
        "orders_running": int(active_orders),
        "orders_completed": int(completed_orders),
        "database": "postgres" if db.IS_PG else "sqlite",
        "orders": live_orders,
        "monitor": engine.service_status(),
        "logs": list(engine.LOGS)[-80:],
    })


@app.get("/api/admin/cams")
def api_admin_cams():
    sess, acct, err = need_admin()
    if err:
        return err
    return jsonify({"cams": engine.frame_list(), "monitor": engine.service_status()})


@app.get("/api/admin/cam/<key>.jpg")
def api_admin_cam(key):
    sess, acct, err = need_admin()
    if err:
        return err
    jpeg = engine.get_frame(key)
    if not jpeg:
        return Response(b"", status=404)
    return Response(jpeg, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/admin/accounts")
def api_admin_accounts():
    sess, acct, err = need_admin()
    if err:
        return err
    rows = db.query("SELECT id, username, coins, ads_watched, coins_spent, is_admin, created_at"
                    " FROM accounts ORDER BY id DESC LIMIT 100")
    return jsonify({"accounts": rows})


# ------------------------------------------------------------------ boot
def bootstrap():
    db.init_db()
    engine.start()


bootstrap()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, threaded=True)

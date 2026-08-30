"""Accounts: key-only signup / login, sessions, coin ledger."""
import time

from . import config, db, security


def create_account_key():
    """Generate a fresh (unused) secret key without creating the account yet.

    The account row is materialised on first login so a key that is never used
    does not pollute the accounts table.
    """
    for _ in range(5):
        key = security.new_secret_key()
        if not db.query_one("SELECT id FROM accounts WHERE secret_hash = ?",
                            (security.hash_secret(key),)):
            return key
    return security.new_secret_key()


def login_with_key(key, ip="", ua=""):
    """Returns (session_token, account, created_bool) or (None, None, False)."""
    key = (key or "").strip()
    if len(key) < 20:
        return None, None, False
    h = security.hash_secret(key)
    acct = db.query_one("SELECT * FROM accounts WHERE secret_hash = ?", (h,))
    created = False
    if not acct:
        username = security.random_username()
        db.execute(
            "INSERT INTO accounts (secret_hash, username, coins, created_at, last_seen)"
            " VALUES (?, ?, ?, ?, ?)",
            (h, username, 0, time.time(), time.time()),
        )
        db.bump_stat("accounts_created", 1)
        acct = db.query_one("SELECT * FROM accounts WHERE secret_hash = ?", (h,))
        created = True

    token = security.new_session_token()
    db.execute(
        "INSERT INTO sessions (token, account_id, created_at, last_captcha, pending_solves, ip, ua)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (token, acct["id"], time.time(), 0.0,
         config.CAPTCHA_REQUIRED_SOLVES, ip[:60], ua[:200]),
    )
    return token, acct, created


def get_session(token):
    if not token:
        return None
    return db.query_one("SELECT * FROM sessions WHERE token = ?", (token,))


def get_account(account_id):
    return db.query_one("SELECT * FROM accounts WHERE id = ?", (account_id,))


def touch(account_id):
    db.execute("UPDATE accounts SET last_seen = ? WHERE id = ?", (time.time(), account_id))


def logout(token):
    db.execute("DELETE FROM sessions WHERE token = ?", (token,))


def captcha_due(sess):
    """True when this session must solve captchas before using gated features."""
    if not sess:
        return True
    if int(sess.get("pending_solves") or 0) > 0:
        return True
    last = float(sess.get("last_captcha") or 0)
    return (time.time() - last) > config.CAPTCHA_INTERVAL_SECONDS


def mark_captcha_progress(token, solved_ok):
    sess = get_session(token)
    if not sess:
        return 0
    pending = int(sess.get("pending_solves") or 0)
    if solved_ok:
        pending = max(0, pending - 1)
        if pending == 0:
            db.execute("UPDATE sessions SET pending_solves = 0, last_captcha = ? WHERE token = ?",
                       (time.time(), token))
            db.execute("UPDATE accounts SET verified = ? WHERE id = ?",
                       (True if db.IS_PG else 1, sess["account_id"]))
        else:
            db.execute("UPDATE sessions SET pending_solves = ? WHERE token = ?", (pending, token))
    else:
        # a wrong answer always resets the requirement back to two solves
        pending = config.CAPTCHA_REQUIRED_SOLVES
        db.execute("UPDATE sessions SET pending_solves = ? WHERE token = ?", (pending, token))
    return pending


def grant_admin(account_id):
    db.execute("UPDATE accounts SET is_admin = ? WHERE id = ?",
               (True if db.IS_PG else 1, account_id))


def public_account(acct, sess=None):
    if not acct:
        return None
    return {
        "username": acct["username"],
        "is_admin": bool(acct["is_admin"]),
        "verified": bool(acct["verified"]),
        "captcha_due": captcha_due(sess) if sess is not None else False,
        "pending_solves": int(sess.get("pending_solves") or 0) if sess else 0,
    }

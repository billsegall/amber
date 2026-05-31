"""
SQLite user store with JSON preferences blob per user.
DB file: amber.db (gitignored).
On first run creates a default admin user (username: admin, password: amber)
with must_change_password=1.
"""
import json
import logging
import os
import sqlite3
import statistics
from contextlib import contextmanager
from werkzeug.security import generate_password_hash, check_password_hash

log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "amber.db")

DEFAULT_PREFERENCES = {
    # Location
    "location_state": "QLD",
    # Hardware — battery
    "battery_capacity_kwh": 42.0,
    "battery_min_soc_pct": 10.0,
    "battery_max_charge_kw": 10.0,
    "battery_max_discharge_kw": 10.0,
    # Hardware — EV
    "ev_capacity_kwh": 100.0,
    "ev_charge_kw": 7.0,
    "ev_target_soc": 85.0,
    # Solar forecast config
    "solar_lat":          -27.47,
    "solar_lon":          153.02,
    "solar_tilt":         20,
    "solar_azimuth":      180,
    "solar_kwp":          5.6,
    "solar_shade_factor": 1.0,
    # Notifications
    "ntfy_topic": "",
    # Alerts
    "alert_spike": True,
    "alert_cheap": True,
    "alert_cheap_descriptor": "extremelyLow",
    "alert_renewables": True,
    "alert_renewables_pct": 80.0,
    "alert_battery_charge_stop": True,
    "alert_battery_full": True,
    "alert_battery_full_pct": 95.0,
    "alert_soc_low": True,
    "alert_soc_low_pct": 20.0,
    "alert_daily_summary": True,
    "daily_summary_hour": 7,
    "poll_interval_seconds": 300,
}


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    """Create tables and default admin user if DB doesn't exist."""
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                username            TEXT UNIQUE NOT NULL,
                password_hash       TEXT NOT NULL,
                must_change_password INTEGER NOT NULL DEFAULT 1,
                preferences         TEXT NOT NULL DEFAULT '{}'
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS solar_actuals (
                date         TEXT PRIMARY KEY,
                actual_kwh   REAL,
                forecast_kwh REAL,
                correction   REAL
            )
        """)
        # Create default admin if no users exist
        row = con.execute("SELECT COUNT(*) FROM users").fetchone()
        if row[0] == 0:
            con.execute(
                "INSERT INTO users (username, password_hash, must_change_password, preferences) VALUES (?,?,?,?)",
                ("admin",
                 generate_password_hash("amber"),
                 1,
                 json.dumps(DEFAULT_PREFERENCES)),
            )
            log.info("Created default admin user (password: amber) — please change immediately")


# ── User helpers ──────────────────────────────────────────────────────────────

def get_user_by_id(user_id: int) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None


def verify_password(username: str, password: str) -> dict | None:
    """Return user dict if credentials valid, else None."""
    user = get_user_by_username(username)
    if user and check_password_hash(user["password_hash"], password):
        return user
    return None


def set_password(user_id: int, new_password: str):
    with _conn() as con:
        con.execute(
            "UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
            (generate_password_hash(new_password), user_id),
        )


# ── Preferences helpers ───────────────────────────────────────────────────────

def get_preferences(user_id: int) -> dict:
    with _conn() as con:
        row = con.execute("SELECT preferences FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return dict(DEFAULT_PREFERENCES)
        stored = json.loads(row["preferences"] or "{}")
        # Merge with defaults so new keys are always present
        return {**DEFAULT_PREFERENCES, **stored}


def set_preferences(user_id: int, prefs: dict):
    with _conn() as con:
        con.execute(
            "UPDATE users SET preferences=? WHERE id=?",
            (json.dumps(prefs), user_id),
        )


def get_default_preferences() -> dict:
    """For scheduler/alerts where there's no request context — use non-admin user's prefs."""
    with _conn() as con:
        row = con.execute(
            "SELECT id FROM users WHERE username != 'admin' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            row = con.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if not row:
            return dict(DEFAULT_PREFERENCES)
        return get_preferences(row["id"])


# ── Solar actuals helpers ─────────────────────────────────────────────────────

def upsert_solar_actual(date: str, actual_kwh: float | None = None,
                        forecast_kwh: float | None = None):
    """Insert or update a row in solar_actuals. Recalculates correction when both are set."""
    with _conn() as con:
        con.execute("""
            INSERT INTO solar_actuals (date, actual_kwh, forecast_kwh, correction)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                actual_kwh   = COALESCE(excluded.actual_kwh,   actual_kwh),
                forecast_kwh = COALESCE(excluded.forecast_kwh, forecast_kwh),
                correction   = CASE
                    WHEN COALESCE(excluded.actual_kwh,   actual_kwh)   IS NOT NULL
                     AND COALESCE(excluded.forecast_kwh, forecast_kwh) > 0
                    THEN COALESCE(excluded.actual_kwh, actual_kwh)
                       / COALESCE(excluded.forecast_kwh, forecast_kwh)
                    ELSE NULL
                END
        """, (date, actual_kwh, forecast_kwh,
              (actual_kwh / forecast_kwh) if (actual_kwh is not None and forecast_kwh and forecast_kwh > 0) else None))


def get_recent_solar_actuals(days: int = 30) -> list[dict]:
    """Return up to `days` most recent solar_actuals rows, newest first."""
    with _conn() as con:
        rows = con.execute("""
            SELECT date, actual_kwh, forecast_kwh, correction
            FROM solar_actuals
            ORDER BY date DESC
            LIMIT ?
        """, (days,)).fetchall()
        return [dict(r) for r in rows]


def get_solar_correction_factor(min_days: int = 3) -> float:
    """Return median(actual/forecast) for recent complete days; 1.0 if insufficient data."""
    rows = get_recent_solar_actuals(30)
    ratios = [r["correction"] for r in rows if r["correction"] is not None]
    if len(ratios) < min_days:
        return 1.0
    return statistics.median(ratios)

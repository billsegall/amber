import os
import json
import logging
from datetime import date, timedelta
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from amber_client import get_client, get_site_id
from optimizer import analyse, HardwareConfig
from notifications import send_notification, is_configured, get_method
from fronius_client import get_power_flow_safe
from foxess_client import get_client as get_foxess_client, get_device_sn
from alerts import _load_state, _save_state
import db

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# ── Flask-Login ───────────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"


class User(UserMixin):
    def __init__(self, user_row: dict):
        self.id                  = user_row["id"]
        self.username            = user_row["username"]
        self.must_change_password = bool(user_row["must_change_password"])

    def get_id(self):
        return str(self.id)


@login_manager.user_loader
def load_user(user_id):
    row = db.get_user_by_id(int(user_id))
    return User(row) if row else None


# ── Constants ─────────────────────────────────────────────────────────────────
DESCRIPTOR_COLORS = {
    "extremelyLow": "#00c853",
    "veryLow":      "#69f0ae",
    "low":          "#b9f6ca",
    "neutral":      "#90a4ae",
    "high":         "#ff6d00",
    "spike":        "#d50000",
}

DESCRIPTOR_LABELS = {
    "extremelyLow": "Extremely Low",
    "veryLow":      "Very Low",
    "low":          "Low",
    "neutral":      "Neutral",
    "high":         "High",
    "spike":        "SPIKE",
}

DEFAULT_BATTERY_SOC = 50.0
DEFAULT_EV_SOC      = 50.0
DEFAULT_EV_TARGET   = 85.0


# ── Helpers ───────────────────────────────────────────────────────────────────
def _aggregate_usage(usage: list[dict]) -> list[dict]:
    from collections import defaultdict
    days: dict[str, dict] = defaultdict(lambda: {"consume_kwh": 0.0, "feedin_kwh": 0.0, "cost": 0.0})
    for u in usage:
        d = u.get("date", "")
        if not d:
            continue
        ch  = u.get("channelType", "")
        kwh = u.get("kwh", 0) or 0
        if ch == "general":
            days[d]["consume_kwh"] += kwh
            days[d]["cost"] += u.get("cost", 0) or 0
        elif ch == "feedIn":
            days[d]["feedin_kwh"] += abs(kwh)
    return [{"date": d, **v} for d, v in sorted(days.items())]


def _split_intervals(intervals: list[dict]):
    past, current, forecast = [], None, []
    for iv in intervals:
        t = iv.get("type", "")
        if t == "ActualInterval":
            past.append(iv)
        elif t == "CurrentInterval":
            current = iv
        elif t == "ForecastInterval":
            forecast.append(iv)
    return past, current, forecast


def _hw_from_prefs(prefs: dict) -> HardwareConfig:
    return HardwareConfig(
        battery_capacity_kwh     = float(prefs.get("battery_capacity_kwh", 42.0)),
        battery_min_soc_pct      = float(prefs.get("battery_min_soc_pct", 10.0)),
        battery_max_charge_kw    = float(prefs.get("battery_max_charge_kw", 10.0)),
        battery_max_discharge_kw = float(prefs.get("battery_max_discharge_kw", 10.0)),
        ev_capacity_kwh          = float(prefs.get("ev_capacity_kwh", 100.0)),
        ev_charge_kw             = float(prefs.get("ev_charge_kw", 7.0)),
    )


# ── Auth routes ───────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user_row = db.verify_password(username, password)
        if user_row:
            user = User(user_row)
            login_user(user)
            if user.must_change_password:
                flash("Please change your password before continuing.", "warning")
                return redirect(url_for("preferences"))
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET", "POST"])
@login_required
def dashboard():
    try:
        prefs = db.get_preferences(current_user.id)
        state = _load_state()

        if request.method == "POST":
            battery_soc = float(request.form.get("battery_soc", DEFAULT_BATTERY_SOC))
            ev_soc      = float(request.form.get("ev_soc",      DEFAULT_EV_SOC))
            ev_target   = float(request.form.get("ev_target",   DEFAULT_EV_TARGET))
            state["battery_soc"] = battery_soc
            state["ev_soc"]      = ev_soc
            state["ev_target"]   = ev_target
            _save_state(state)
        else:
            ev_soc    = float(state.get("ev_soc",    DEFAULT_EV_SOC))
            ev_target = float(prefs.get("ev_target_soc", DEFAULT_EV_TARGET))
            # Battery SOC: prefer live FOX ESS reading
            live_soc = None
            try:
                fc = get_foxess_client()
                if fc:
                    sn = get_device_sn()
                    if sn:
                        live_soc = fc.get_battery_soc(sn)
            except Exception:
                pass
            if live_soc is not None:
                battery_soc = live_soc
                state["battery_soc"] = battery_soc
                _save_state(state)
            else:
                battery_soc = float(state.get("battery_soc", DEFAULT_BATTERY_SOC))

        client  = get_client()
        site_id = get_site_id(client)
        general    = client.get_current_prices(site_id, next_intervals=96, previous_intervals=24, channel_type="general")
        feedin_iv  = client.get_current_prices(site_id, next_intervals=96, previous_intervals=24, channel_type="feedIn")
        renewables = client.get_renewables(state=prefs.get("location_state", "QLD"),
                                           next_intervals=96, previous_intervals=24)

        past, current, forecast         = _split_intervals(general)
        _, feedin_current, feedin_forecast = _split_intervals(feedin_iv)

        hw = _hw_from_prefs(prefs)
        analysis = analyse(
            current_interval=current,
            forecast=forecast,
            feedin_current=feedin_current,
            battery_soc_pct=battery_soc,
            battery_target_pct=100.0,
            ev_soc_pct=ev_soc,
            ev_target_pct=ev_target,
            hw=hw,
        )

        last_poll = state.get("last_poll", "")
        solar = get_power_flow_safe()
        foxess = None
        try:
            fc = get_foxess_client()
            if fc:
                sn = get_device_sn()
                if sn:
                    foxess = fc.get_realtime(sn)
        except Exception:
            pass

        try:
            end   = date.today()
            start = end - timedelta(days=6)
            usage = client.get_usage(site_id, start, end)
        except Exception:
            usage = []

        usage_daily = _aggregate_usage(usage)

        return render_template(
            "dashboard.html",
            current=current,
            past=past,
            forecast=forecast,
            feedin_current=feedin_current,
            feedin_forecast=feedin_forecast,
            renewables=renewables,
            analysis=analysis,
            battery_soc=battery_soc,
            ev_soc=ev_soc,
            ev_target=ev_target,
            last_poll=last_poll,
            solar=solar,
            foxess=foxess,
            usage_daily=usage_daily,
            signal_configured=is_configured(),
            descriptor_colors=DESCRIPTOR_COLORS,
            descriptor_labels=DESCRIPTOR_LABELS,
        )
    except Exception as e:
        import traceback
        return render_template("error.html", error=traceback.format_exc()), 500


# ── Alerts page ───────────────────────────────────────────────────────────────
@app.route("/alerts", methods=["GET", "POST"])
@login_required
def alerts_page():
    prefs = db.get_preferences(current_user.id)
    state = _load_state()
    msg   = None

    if request.method == "POST" and request.form.get("action") == "test":
        ok  = send_notification("🔔 Amber test notification — everything is working!", title="Amber Test")
        msg = "Test notification sent." if ok else "Notification not configured — set ntfy topic in Preferences."

    notify_method = "ntfy" if prefs.get("ntfy_topic") else get_method()

    alert_config = {
        "signal_configured":  is_configured(),
        "notify_method":      notify_method,
        "ntfy_topic":         prefs.get("ntfy_topic", ""),
        "spike":              prefs.get("alert_spike", True),
        "cheap":              prefs.get("alert_cheap", True),
        "cheap_desc":         prefs.get("alert_cheap_descriptor", "extremelyLow"),
        "renewables":         prefs.get("alert_renewables", True),
        "renewables_pct":     prefs.get("alert_renewables_pct", 80),
        "battery_charge_stop": prefs.get("alert_battery_charge_stop", True),
        "daily_summary":      prefs.get("alert_daily_summary", True),
        "daily_hour":         prefs.get("daily_summary_hour", 7),
        "poll_interval":      prefs.get("poll_interval_seconds", 300),
        "last_poll":          state.get("last_poll", "never"),
        "spike_status":       state.get("spike_status", "none"),
        "was_cheap":          state.get("was_cheap", False),
        "was_green":          state.get("was_green", False),
        "was_charging":       state.get("was_charging", False),
    }

    return render_template("alerts.html", config=alert_config, msg=msg)


# ── Preferences page ──────────────────────────────────────────────────────────
@app.route("/preferences", methods=["GET", "POST"])
@login_required
def preferences():
    prefs = db.get_preferences(current_user.id)
    errors, success = [], []

    if request.method == "POST":
        action = request.form.get("action")

        if action == "save_prefs":
            def _float(key, default):
                try: return float(request.form.get(key, default))
                except ValueError: return default
            def _int(key, default):
                try: return int(request.form.get(key, default))
                except ValueError: return default
            def _bool(key):
                return request.form.get(key) == "1"

            prefs.update({
                "location_state":           request.form.get("location_state", "QLD"),
                "battery_capacity_kwh":     _float("battery_capacity_kwh", 42.0),
                "battery_min_soc_pct":      _float("battery_min_soc_pct", 10.0),
                "battery_max_charge_kw":    _float("battery_max_charge_kw", 10.0),
                "battery_max_discharge_kw": _float("battery_max_discharge_kw", 10.0),
                "ev_capacity_kwh":          _float("ev_capacity_kwh", 100.0),
                "ev_charge_kw":             _float("ev_charge_kw", 7.0),
                "ev_target_soc":            _float("ev_target_soc", 85.0),
                "ntfy_topic":               request.form.get("ntfy_topic", "").strip(),
                "alert_spike":              _bool("alert_spike"),
                "alert_cheap":              _bool("alert_cheap"),
                "alert_cheap_descriptor":   request.form.get("alert_cheap_descriptor", "extremelyLow"),
                "alert_renewables":         _bool("alert_renewables"),
                "alert_renewables_pct":     _float("alert_renewables_pct", 80.0),
                "alert_battery_charge_stop": _bool("alert_battery_charge_stop"),
                "alert_daily_summary":      _bool("alert_daily_summary"),
                "daily_summary_hour":       _int("daily_summary_hour", 7),
                "poll_interval_seconds":    _int("poll_interval_seconds", 300),
            })
            db.set_preferences(current_user.id, prefs)
            success.append("Preferences saved.")

        elif action == "change_password":
            current_pw  = request.form.get("current_password", "")
            new_pw      = request.form.get("new_password", "")
            confirm_pw  = request.form.get("confirm_password", "")
            user_row    = db.verify_password(current_user.username, current_pw)
            if not user_row:
                errors.append("Current password is incorrect.")
            elif len(new_pw) < 8:
                errors.append("New password must be at least 8 characters.")
            elif new_pw != confirm_pw:
                errors.append("Passwords do not match.")
            else:
                db.set_password(current_user.id, new_pw)
                success.append("Password changed successfully.")

    return render_template("preferences.html", prefs=prefs, errors=errors, success=success,
                           descriptors=list(DESCRIPTOR_LABELS.keys()),
                           descriptor_labels=DESCRIPTOR_LABELS)


# ── JSON API ──────────────────────────────────────────────────────────────────
@app.route("/api/prices/current")
@login_required
def api_current_prices():
    client = get_client()
    site_id = get_site_id(client)
    return jsonify(client.get_current_prices(site_id, next_intervals=96, previous_intervals=24))


@app.route("/api/renewables")
@login_required
def api_renewables():
    client = get_client()
    return jsonify(client.get_renewables(state="QLD", next_intervals=96, previous_intervals=24))


@app.route("/api/usage")
@login_required
def api_usage():
    client = get_client()
    site_id = get_site_id(client)
    end = date.today()
    start = end - timedelta(days=6)
    return jsonify(client.get_usage(site_id, start, end))


@app.route("/api/prices/history")
@login_required
def api_price_history():
    client = get_client()
    site_id = get_site_id(client)
    end = date.today()
    start = end - timedelta(days=6)
    return jsonify(client.get_historical_prices(site_id, start, end))


@app.route("/api/sites")
@login_required
def api_sites():
    client = get_client()
    return jsonify(client.get_sites())


if __name__ == "__main__":
    db.init_db()
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        import scheduler
        scheduler.start(app)
    app.run(host="0.0.0.0", port=8888, debug=True)

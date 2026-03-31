import os
import json
import logging
import threading
import time
from datetime import date, timedelta
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from amber_client import get_client, get_site_id
from optimizer import analyse, HardwareConfig
from notifications import send_notification, is_configured, get_method
from fronius_client import get_power_flow_safe
from foxess_client import get_client as get_foxess_client, get_device_sn
from solar_forecast_client import get_forecast as _solar_get_forecast
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

# ── Stale-while-revalidate cache ──────────────────────────────────────────────
_cache: dict     = {}
_refreshing: set = set()
_CACHE_TTL       = 60  # seconds


def _ensure_fresh(key: str, fn, *args, **kwargs):
    """Return (value, is_stale).

    If no cached value exists, fetch synchronously (first load).
    If cached value is stale, return it immediately and fire a background
    thread to refresh it — the next page load will get fresh data.
    """
    entry = _cache.get(key)
    now   = time.time()

    if entry is None:
        try:
            val = fn(*args, **kwargs)
        except Exception:
            val = None
        _cache[key] = {"ts": now, "val": val}
        return val, False

    stale = (now - entry["ts"]) >= _CACHE_TTL
    if stale and key not in _refreshing:
        _refreshing.add(key)
        def _bg(k=key, f=fn, a=args, kw=kwargs):
            try:
                val = f(*a, **kw)
                _cache[k] = {"ts": time.time(), "val": val}
            except Exception:
                pass
            finally:
                _refreshing.discard(k)
        threading.Thread(target=_bg, daemon=True).start()

    return entry["val"], stale


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
            login_user(user, remember=True)
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
            ev_soc      = float(state.get("ev_soc",    DEFAULT_EV_SOC))
            ev_target   = float(prefs.get("ev_target_soc", DEFAULT_EV_TARGET))
            battery_soc = float(state.get("battery_soc", DEFAULT_BATTERY_SOC))

        client  = get_client()
        site_id = get_site_id(client)
        loc = prefs.get("location_state", "QLD")

        general,    s1 = _ensure_fresh("general",    client.get_current_prices, site_id, next_intervals=96, previous_intervals=24, channel_type="general")
        feedin_iv,  s2 = _ensure_fresh("feedin",     client.get_current_prices, site_id, next_intervals=96, previous_intervals=24, channel_type="feedIn")
        renewables, s3 = _ensure_fresh(f"ren_{loc}", client.get_renewables, state=loc, next_intervals=96, previous_intervals=24)

        past, current, forecast            = _split_intervals(general   or [])
        _, feedin_current, feedin_forecast = _split_intervals(feedin_iv or [])

        # FOX ESS — single realtime call covers both SOC and power flow
        foxess, s4 = None, False
        try:
            fc = get_foxess_client()
            if fc:
                sn = get_device_sn()
                if sn:
                    foxess, s4 = _ensure_fresh("foxess", fc.get_realtime, sn)
        except Exception:
            pass

        # Derive battery SOC from realtime data when available
        if request.method == "GET":
            if foxess and foxess.get("SoC") is not None:
                battery_soc = float(foxess["SoC"])
                state["battery_soc"] = battery_soc
                _save_state(state)

        solar, s5 = _ensure_fresh("solar", get_power_flow_safe)
        data_stale = any([s1, s2, s3, s4, s5])

        # Solar generation forecast (cached 1 hour)
        solar_forecast = None
        _sf_entry = _cache.get("solar_forecast")
        if _sf_entry is None or (time.time() - _sf_entry["ts"]) >= 3600:
            try:
                solar_forecast = _solar_get_forecast(
                    lat=float(prefs.get("solar_lat", -27.47)),
                    lon=float(prefs.get("solar_lon",  153.02)),
                    tilt=int(prefs.get("solar_tilt",  20)),
                    azimuth=int(prefs.get("solar_azimuth", 0)),
                    kwp=float(prefs.get("solar_kwp",  5.6)),
                )
                _cache["solar_forecast"] = {"ts": time.time(), "val": solar_forecast}
            except Exception:
                solar_forecast = _sf_entry["val"] if _sf_entry else None
        else:
            solar_forecast = _sf_entry["val"]
        if solar_forecast:
            correction = db.get_solar_correction_factor()
            if correction != 1.0:
                solar_forecast = dict(solar_forecast)
                solar_forecast["today_kwh"]    = round(solar_forecast["today_kwh"]    * correction, 2)
                solar_forecast["tomorrow_kwh"] = round(solar_forecast["tomorrow_kwh"] * correction, 2)
            # Store today's forecast for later correction tracking
            from datetime import datetime as _dt2, timezone as _tz2, timedelta as _td2
            _today = _dt2.now(_tz2(_td2(hours=10))).strftime("%Y-%m-%d")
            db.upsert_solar_actual(_today, forecast_kwh=solar_forecast["today_kwh"])

        # Solar offline detection — daytime in AEST (QLD, UTC+10) with near-zero generation
        from datetime import datetime as _dt, timezone, timedelta as _td
        _aest = timezone(_td(hours=10))
        _hour = _dt.now(_aest).hour
        _is_daytime = 6 <= _hour < 20
        _sol_kw = 0.0
        if foxess:
            _bc = foxess.get("batChargePower", 0) or 0
            _bd = foxess.get("batDischargePower", 0) or 0
            _gi = foxess.get("gridConsumptionPower", 0) or 0
            _go = foxess.get("feedinPower", 0) or 0
            _ld = foxess.get("loadsPower", 0) or 0
            _sol_kw = max(_ld + _go + _bc - _gi - _bd, 0)
        elif solar and solar.get("p_pv_w") is not None:
            _sol_kw = (solar["p_pv_w"] or 0) / 1000
        solar_offline = _is_daytime and _sol_kw < 0.1

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

        try:
            end   = date.today()
            start = end - timedelta(days=6)
            usage, _ = _ensure_fresh("usage", client.get_usage, site_id, start, end)
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
            solar_offline=solar_offline,
            solar_forecast=solar_forecast,
            foxess=foxess,
            usage_daily=usage_daily,
            signal_configured=is_configured(),
            descriptor_colors=DESCRIPTOR_COLORS,
            descriptor_labels=DESCRIPTOR_LABELS,
            data_stale=data_stale,
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

    if request.method == "POST":
        action = request.form.get("action")

        def _float(key, default):
            try: return float(request.form.get(key, default))
            except ValueError: return default
        def _int(key, default):
            try: return int(request.form.get(key, default))
            except ValueError: return default
        def _bool(key):
            return request.form.get(key) == "1"

        if action == "test":
            ok  = send_notification("🔔 Amber test notification — everything is working!", title="Amber Test")
            msg = "Test notification sent." if ok else "Notification not configured — set ntfy topic first."

        elif action == "save_notify":
            prefs["ntfy_topic"] = request.form.get("ntfy_topic", "").strip()
            db.set_preferences(current_user.id, prefs)
            msg = "Saved."

        elif action == "save_alerts":
            prefs.update({
                "alert_spike":                    _bool("alert_spike"),
                "alert_cheap":                    _bool("alert_cheap"),
                "alert_cheap_descriptor":         request.form.get("alert_cheap_descriptor", "extremelyLow"),
                "alert_renewables":               _bool("alert_renewables"),
                "alert_renewables_pct":           _float("alert_renewables_pct", 80.0),
                "alert_battery_charge_stop":      _bool("alert_battery_charge_stop"),
                "alert_negative_price":           _bool("alert_negative_price"),
                "alert_feedin_spike":             _bool("alert_feedin_spike"),
                "alert_feedin_spike_threshold":   _float("alert_feedin_spike_threshold", 30.0),
                "alert_spike_incoming":           _bool("alert_spike_incoming"),
                "alert_spike_incoming_minutes":   _int("alert_spike_incoming_minutes", 30),
                "alert_soc_low":                  _bool("alert_soc_low"),
                "alert_soc_low_pct":              _float("alert_soc_low_pct", 20.0),
                "alert_battery_full":             _bool("alert_battery_full"),
                "alert_battery_full_pct":         _float("alert_battery_full_pct", 95.0),
                "alert_daily_summary":            _bool("alert_daily_summary"),
                "daily_summary_hour":             _int("daily_summary_hour", 7),
                "poll_interval_seconds":          _int("poll_interval_seconds", 300),
            })
            db.set_preferences(current_user.id, prefs)
            msg = "Alert settings saved."

    notify_method = "ntfy" if prefs.get("ntfy_topic") else get_method()

    alert_config = {
        "signal_configured":          is_configured(),
        "notify_method":              notify_method,
        "ntfy_topic":                 prefs.get("ntfy_topic", ""),
        "spike":                      prefs.get("alert_spike", True),
        "cheap":                      prefs.get("alert_cheap", True),
        "cheap_desc":                 prefs.get("alert_cheap_descriptor", "extremelyLow"),
        "renewables":                 prefs.get("alert_renewables", True),
        "renewables_pct":             prefs.get("alert_renewables_pct", 80),
        "battery_charge_stop":        prefs.get("alert_battery_charge_stop", True),
        "negative_price":             prefs.get("alert_negative_price", True),
        "feedin_spike":               prefs.get("alert_feedin_spike", True),
        "feedin_spike_threshold":     prefs.get("alert_feedin_spike_threshold", 30.0),
        "spike_incoming":             prefs.get("alert_spike_incoming", True),
        "spike_incoming_minutes":     prefs.get("alert_spike_incoming_minutes", 30),
        "soc_low":                    prefs.get("alert_soc_low", True),
        "soc_low_pct":                prefs.get("alert_soc_low_pct", 20.0),
        "battery_full":               prefs.get("alert_battery_full", True),
        "battery_full_pct":           prefs.get("alert_battery_full_pct", 95.0),
        "daily_summary":              prefs.get("alert_daily_summary", True),
        "daily_hour":                 prefs.get("daily_summary_hour", 7),
        "poll_interval":              prefs.get("poll_interval_seconds", 300),
        "last_poll":                  state.get("last_poll", "never"),
        "spike_status":               state.get("spike_status", "none"),
        "was_cheap":                  state.get("was_cheap", False),
        "was_green":                  state.get("was_green", False),
        "was_charging":               state.get("was_charging", False),
        "was_negative":               state.get("was_negative", False),
        "was_feedin_spike":           state.get("was_feedin_spike", False),
        "was_soc_low":                state.get("was_soc_low", False),
        "was_battery_full":           state.get("was_battery_full", False),
    }

    return render_template("alerts.html", config=alert_config, msg=msg,
                           descriptors=DESCRIPTOR_LABELS)


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
                "solar_lat":               _float("solar_lat", -27.47),
                "solar_lon":               _float("solar_lon",  153.02),
                "solar_tilt":              _int("solar_tilt",   20),
                "solar_azimuth":           _int("solar_azimuth", 0),
                "solar_kwp":               _float("solar_kwp",  5.6),
            })
            # Bust cached forecast so next load uses updated params
            _cache.pop("solar_forecast", None)
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

    return render_template("preferences.html", prefs=prefs, errors=errors, success=success)


# ── Battery control ──────────────────────────────────────────────────────────
WORK_MODES = ["SelfUse", "Feedin", "Backup"]


@app.route("/battery", methods=["GET", "POST"])
@login_required
def battery_control():
    fc = get_foxess_client()
    sn = get_device_sn() if fc else None

    errors, success = [], []
    force_charge = None
    settings = None
    realtime = None

    if fc and sn:
        if request.method == "POST":
            action = request.form.get("action")

            if action == "set_work_mode":
                mode = request.form.get("work_mode")
                if mode not in WORK_MODES:
                    errors.append(f"Unknown work mode: {mode}")
                else:
                    ok, err = fc.set_setting(sn, "workMode", mode)
                    if ok:
                        success.append(f"Work mode set to {mode}")
                        _cache.pop("bat_settings", None)
                    else:
                        errors.append(f"Failed to set work mode: {err}")

            elif action == "set_min_soc":
                try:
                    min_soc = int(request.form.get("min_soc_on_grid", 10))
                    if not 0 <= min_soc <= 100:
                        raise ValueError
                except ValueError:
                    errors.append("Min SOC must be 0–100")
                else:
                    ok, err = fc.set_setting(sn, "minSocOnGrid", str(min_soc))
                    if ok:
                        success.append(f"Min SOC on grid set to {min_soc}%")
                        _cache.pop("bat_settings", None)
                    else:
                        errors.append(f"Failed to set min SOC: {err}")

            elif action == "set_force_charge":
                try:
                    def _hm(field):
                        val = request.form.get(field, "00:00")
                        h, m = val.split(":")
                        return {"hour": int(h), "minute": int(m)}

                    data = {
                        "enable1":    "enable1" in request.form,
                        "startTime1": _hm("start1"),
                        "endTime1":   _hm("end1"),
                        "enable2":    "enable2" in request.form,
                        "startTime2": _hm("start2"),
                        "endTime2":   _hm("end2"),
                    }
                except Exception:
                    errors.append("Invalid time format")
                else:
                    ok, err = fc.set_force_charge(sn, data)
                    if ok:
                        success.append("Force charge schedule updated")
                        _cache.pop("bat_force_charge", None)
                    else:
                        errors.append(f"Failed to update force charge schedule: {err}")

            elif action == "charge_now":
                from datetime import datetime, timedelta
                try:
                    minutes = int(request.form.get("duration_minutes", 60))
                    if minutes <= 0:
                        raise ValueError
                except ValueError:
                    errors.append("Invalid duration")
                else:
                    now  = datetime.now()
                    end  = now + timedelta(minutes=minutes)
                    data = {
                        "enable1":    True,
                        "startTime1": {"hour": now.hour, "minute": now.minute},
                        "endTime1":   {"hour": end.hour, "minute": end.minute},
                        "enable2":    False,
                        "startTime2": {"hour": 0, "minute": 0},
                        "endTime2":   {"hour": 0, "minute": 0},
                    }
                    ok, err = fc.set_force_charge(sn, data)
                    if ok:
                        h, m = divmod(minutes, 60)
                        label = f"{h}h {m}m" if h and m else (f"{h}h" if h else f"{m}m")
                        success.append(f"Charging from grid for {label} (until {end.strftime('%H:%M')})")
                        _cache.pop("bat_force_charge", None)
                    else:
                        errors.append(f"Failed to start charging: {err}")

            elif action == "stop_charging":
                data = {
                    "enable1": False, "startTime1": {"hour": 0, "minute": 0},
                    "endTime1": {"hour": 0, "minute": 0},
                    "enable2": False, "startTime2": {"hour": 0, "minute": 0},
                    "endTime2": {"hour": 0, "minute": 0},
                }
                ok, err = fc.set_force_charge(sn, data)
                if ok:
                    success.append("Force charge stopped")
                    _cache.pop("bat_force_charge", None)
                else:
                    errors.append(f"Failed to stop charging: {err}")

            elif action == "clear_force_charge":
                data = {
                    "enable1": False, "startTime1": {"hour": 0, "minute": 0},
                    "endTime1": {"hour": 0, "minute": 0},
                    "enable2": False, "startTime2": {"hour": 0, "minute": 0},
                    "endTime2": {"hour": 0, "minute": 0},
                }
                ok, err = fc.set_force_charge(sn, data)
                if ok:
                    success.append("Force charge cleared")
                    _cache.pop("bat_force_charge", None)
                else:
                    errors.append(f"Failed to clear force charge: {err}")

        # Serve from cache; force_charge and settings are slow FOX ESS cloud calls
        realtime,     _ = _ensure_fresh("foxess",          fc.get_realtime, sn)
        force_charge, _ = _ensure_fresh("bat_force_charge", fc.get_force_charge, sn)
        settings,     _ = _ensure_fresh("bat_settings",    fc.get_settings, sn, ["workMode", "minSocOnGrid"])

    return render_template(
        "battery.html",
        realtime=realtime,
        force_charge=force_charge,
        settings=settings or {},
        work_modes=WORK_MODES,
        sn=sn,
        errors=errors,
        success=success,
        foxess_available=bool(fc and sn),
    )


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

import os
import json
import logging
from datetime import date, timedelta
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from dotenv import load_dotenv
from amber_client import get_client, get_site_id
from optimizer import analyse, HardwareConfig
from notifications import send_notification, is_configured, get_method
from fronius_client import get_power_flow_safe
from foxess_client import get_client as get_foxess_client, get_device_sn
from alerts import _load_state, _save_state

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

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


def _aggregate_usage(usage: list[dict]) -> list[dict]:
    """Aggregate 5-min usage intervals into daily totals by channel type."""
    from collections import defaultdict
    days: dict[str, dict] = defaultdict(lambda: {"consume_kwh": 0.0, "feedin_kwh": 0.0, "cost": 0.0})
    for u in usage:
        d = u.get("date", "")
        if not d:
            continue
        ch = u.get("channelType", "")
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


DEFAULT_HW = HardwareConfig()
DEFAULT_BATTERY_SOC = 50.0
DEFAULT_EV_SOC      = 50.0
DEFAULT_EV_TARGET   = 85.0


@app.route("/", methods=["GET", "POST"])
def dashboard():
    try:
        # SOC inputs — persist in alert_state so scheduler can use them
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
            ev_target = float(state.get("ev_target", DEFAULT_EV_TARGET))
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
        feedin     = client.get_current_prices(site_id, next_intervals=96, previous_intervals=24, channel_type="feedIn")
        renewables = client.get_renewables(state="QLD", next_intervals=96, previous_intervals=24)

        past, current, forecast = _split_intervals(general)
        _, feedin_current, feedin_forecast = _split_intervals(feedin)

        analysis = analyse(
            current_interval=current,
            forecast=forecast,
            feedin_current=feedin_current,
            battery_soc_pct=battery_soc,
            battery_target_pct=100.0,
            ev_soc_pct=ev_soc,
            ev_target_pct=ev_target,
            hw=DEFAULT_HW,
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

        # 7-day usage history for consumption chart
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


@app.route("/alerts", methods=["GET", "POST"])
def alerts_page():
    state = _load_state()
    msg = None

    if request.method == "POST":
        action = request.form.get("action")
        if action == "test":
            ok = send_notification("🔔 Amber test notification — everything is working!", title="Amber Test")
            msg = ("Test notification sent." if ok
                   else "Notification not configured — set NTFY_TOPIC in .env and restart.")
        elif action == "save_thresholds":
            # Write threshold overrides into alert state for display; real values go in .env
            pass  # thresholds are .env-based for now

    alert_config = {
        "signal_configured": is_configured(),
        "notify_method": get_method(),
        "ntfy_topic":    os.environ.get("NTFY_TOPIC", ""),
        "spike":        os.environ.get("ALERT_SPIKE",        "true").lower() != "false",
        "cheap":        os.environ.get("ALERT_CHEAP",        "true").lower() != "false",
        "cheap_desc":   os.environ.get("ALERT_CHEAP_DESCRIPTOR", "extremelyLow"),
        "renewables":   os.environ.get("ALERT_RENEWABLES",   "true").lower() != "false",
        "renewables_pct": float(os.environ.get("ALERT_RENEWABLES_PCT", 80)),
        "daily_summary": os.environ.get("ALERT_DAILY_SUMMARY", "true").lower() != "false",
        "daily_hour":   int(os.environ.get("DAILY_SUMMARY_HOUR", 7)),
        "poll_interval": int(os.environ.get("POLL_INTERVAL_SECONDS", 300)),
        "last_poll":    state.get("last_poll", "never"),
        "spike_status": state.get("spike_status", "none"),
        "was_cheap":    state.get("was_cheap", False),
        "was_green":    state.get("was_green", False),
    }

    return render_template("alerts.html", config=alert_config, msg=msg)


# ── JSON API ─────────────────────────────────────────────────────────────────

@app.route("/api/prices/current")
def api_current_prices():
    client = get_client()
    site_id = get_site_id(client)
    return jsonify(client.get_current_prices(site_id, next_intervals=96, previous_intervals=24))


@app.route("/api/renewables")
def api_renewables():
    client = get_client()
    return jsonify(client.get_renewables(state="QLD", next_intervals=96, previous_intervals=24))


@app.route("/api/usage")
def api_usage():
    client = get_client()
    site_id = get_site_id(client)
    end = date.today()
    start = end - timedelta(days=6)
    return jsonify(client.get_usage(site_id, start, end))


@app.route("/api/prices/history")
def api_price_history():
    client = get_client()
    site_id = get_site_id(client)
    end = date.today()
    start = end - timedelta(days=6)
    return jsonify(client.get_historical_prices(site_id, start, end))


@app.route("/api/sites")
def api_sites():
    client = get_client()
    return jsonify(client.get_sites())


if __name__ == "__main__":
    # Only start scheduler in the main process (not the reloader child)
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        import scheduler
        scheduler.start(app)
    app.run(host="0.0.0.0", port=8888, debug=True)

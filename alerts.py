"""
Alert logic: checks current conditions against user preferences and fires
notifications on state transitions. Uses a JSON file for dedup state.
"""
import json
import logging
import os
from datetime import datetime

from notifications import send_notification

log = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), "alert_state.json")

DESCRIPTOR_RANK = {
    "extremelyLow": 0,
    "veryLow":      1,
    "low":          2,
    "neutral":      3,
    "high":         4,
    "spike":        5,
}


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def check_and_alert(current: dict, feedin_current: dict | None,
                    renewables_current: dict | None,
                    foxess_realtime: dict | None,
                    prefs: dict) -> list[str]:
    """
    Check current conditions against prefs thresholds.
    Send notifications for new state transitions.
    Returns list of messages sent.
    """
    if not current:
        return []

    state = _load_state()
    sent  = []

    spike_status    = current.get("spikeStatus", "none")
    descriptor      = current.get("descriptor", "neutral")
    price           = current.get("perKwh", 0)
    renewables_pct  = renewables_current.get("renewables", 0) if renewables_current else 0

    # ── Spike alert ────────────────────────────────────────────────────────────
    if prefs.get("alert_spike", True):
        prev_spike = state.get("spike_status", "none")
        if spike_status in ("spike", "potential") and prev_spike not in ("spike", "potential"):
            label = "PRICE SPIKE" if spike_status == "spike" else "Potential spike"
            msg = (f"⚡ Amber Alert: {label}\n"
                   f"Current price: {price:.1f}¢/kWh\n"
                   f"Stop charging — consider exporting if battery allows.")
            if send_notification(msg, title="Amber — Spike", priority="urgent"):
                sent.append(msg)
        elif spike_status == "none" and prev_spike in ("spike", "potential"):
            msg = f"✅ Amber: Spike cleared\nPrice back to {price:.1f}¢/kWh ({descriptor})"
            if send_notification(msg, title="Amber — Spike cleared", priority="high"):
                sent.append(msg)
    state["spike_status"] = spike_status

    # ── Cheap window alert ─────────────────────────────────────────────────────
    if prefs.get("alert_cheap", True):
        threshold   = prefs.get("alert_cheap_descriptor", "extremelyLow")
        is_cheap    = DESCRIPTOR_RANK.get(descriptor, 3) <= DESCRIPTOR_RANK.get(threshold, 0)
        was_cheap   = state.get("was_cheap", False)
        if is_cheap and not was_cheap:
            msg = (f"💚 Amber: Great time to charge!\n"
                   f"Price: {price:.1f}¢/kWh ({descriptor})\n"
                   f"Renewables: {renewables_pct:.0f}%")
            if send_notification(msg, title="Amber — Cheap window", priority="default"):
                sent.append(msg)
        state["was_cheap"] = is_cheap

    # ── High renewables alert ──────────────────────────────────────────────────
    if prefs.get("alert_renewables", True) and renewables_current:
        threshold = float(prefs.get("alert_renewables_pct", 80))
        is_green  = renewables_pct >= threshold
        was_green = state.get("was_green", False)
        if is_green and not was_green:
            msg = (f"🌱 Amber: High renewables — {renewables_pct:.0f}% green\n"
                   f"Price: {price:.1f}¢/kWh\n"
                   f"Good time to charge from the grid.")
            if send_notification(msg, title="Amber — Green grid", priority="default"):
                sent.append(msg)
        state["was_green"] = is_green

    # ── Battery charging stopped alert ────────────────────────────────────────
    if prefs.get("alert_battery_charge_stop", True) and foxess_realtime:
        bat_charge_kw = foxess_realtime.get("batChargePower", 0) or 0
        is_charging   = bat_charge_kw > 0.2   # >200W = actively charging
        was_charging  = state.get("was_charging", False)
        bat_soc       = foxess_realtime.get("SoC", 0) or 0

        if was_charging and not is_charging and bat_soc < 98:
            # Was charging, now stopped, and not because it's full
            msg = (f"🔋 Amber: Battery charging stopped\n"
                   f"SOC: {bat_soc:.0f}%\n"
                   f"Current price: {price:.1f}¢/kWh ({descriptor})")
            if send_notification(msg, title="Amber — Charging stopped", priority="high"):
                sent.append(msg)
        state["was_charging"] = is_charging

    state["last_poll"] = datetime.now().isoformat()
    _save_state(state)
    return sent


def send_daily_summary(current: dict, stats_24h: dict, analysis: dict) -> bool:
    price = current.get("perKwh", 0) if current else 0
    s     = stats_24h
    ba    = analysis.get("battery", {})
    ea    = analysis.get("ev", {})

    lines = [
        "📊 Amber Daily Summary",
        f"Right now: {price:.1f}¢/kWh",
        f"Today's range: {s.get('min', 0):.1f}¢ – {s.get('max', 0):.1f}¢ (avg {s.get('avg', 0):.1f}¢)",
    ]
    if ba.get("best_window"):
        bw = ba["best_window"]
        lines.append(f"Battery: best window at {bw['start_time'][11:16]} @ {bw['avg_price']:.1f}¢/kWh")
    if ea.get("best_window"):
        ew = ea["best_window"]
        lines.append(f"EV: best window at {ew['start_time'][11:16]} @ {ew['avg_price']:.1f}¢/kWh")
    total_saving = ba.get("saving_dollars", 0) + ea.get("saving_dollars", 0)
    if total_saving > 0:
        lines.append(f"Potential saving today: ${total_saving:.2f}")

    return send_notification("\n".join(lines), title="Amber Daily Summary")

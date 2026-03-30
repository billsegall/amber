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
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)  # atomic on POSIX — never leaves a partial file


def check_and_alert(current: dict, feedin_current: dict | None,
                    renewables_current: dict | None,
                    foxess_realtime: dict | None,
                    prefs: dict,
                    forecast: list[dict] | None = None) -> list[str]:
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
    if prefs.get("alert_battery_charge_stop", True):
        if foxess_realtime:
            bat_charge_kw = foxess_realtime.get("batChargePower", 0) or 0
            is_charging   = bat_charge_kw > 0.2   # >200W = actively charging
            # None = never seen (fresh state file); False = known not charging
            was_charging  = state.get("was_charging")
            bat_soc       = foxess_realtime.get("SoC", 0) or 0
            log.info("Battery charge check: was_charging=%s is_charging=%s (%.2fkW) SoC=%.0f%%",
                     was_charging, is_charging, bat_charge_kw, bat_soc)

            if was_charging and not is_charging and bat_soc < 98:
                msg = (f"🔋 Amber: Battery charging stopped\n"
                       f"SOC: {bat_soc:.0f}%\n"
                       f"Current price: {price:.1f}¢/kWh ({descriptor})")
                log.info("Firing battery-charge-stop alert")
                if send_notification(msg, title="Amber — Charging stopped", priority="high"):
                    sent.append(msg)
            state["was_charging"] = is_charging
        else:
            log.warning("Battery charge stop alert skipped — no FOX ESS data this poll")

    # ── Negative price ────────────────────────────────────────────────────────
    if prefs.get("alert_negative_price", True):
        is_negative  = price < 0
        was_negative = state.get("was_negative", False)
        if is_negative and not was_negative:
            msg = (f"🤑 Amber: Negative price! {price:.1f}¢/kWh\n"
                   f"You're being paid to consume — charge everything now.")
            if send_notification(msg, title="Amber — Negative price", priority="urgent"):
                sent.append(msg)
        elif not is_negative and was_negative:
            msg = f"Amber: Price back to {price:.1f}¢/kWh ({descriptor})"
            if send_notification(msg, title="Amber — Price recovered", priority="default"):
                sent.append(msg)
        state["was_negative"] = is_negative

    # ── Feed-in spike ─────────────────────────────────────────────────────────
    if prefs.get("alert_feedin_spike", True) and feedin_current:
        threshold        = float(prefs.get("alert_feedin_spike_threshold", 30.0))
        feedin_price     = feedin_current.get("perKwh", 0)
        is_feedin_spike  = feedin_price >= threshold
        was_feedin_spike = state.get("was_feedin_spike", False)
        if is_feedin_spike and not was_feedin_spike:
            msg = (f"💰 Amber: High feed-in rate — {feedin_price:.1f}¢/kWh\n"
                   f"Great time to export from battery if possible.")
            if send_notification(msg, title="Amber — Feed-in spike", priority="high"):
                sent.append(msg)
        elif not is_feedin_spike and was_feedin_spike:
            msg = f"Amber: Feed-in rate back to {feedin_price:.1f}¢/kWh"
            if send_notification(msg, title="Amber — Feed-in normalised", priority="default"):
                sent.append(msg)
        state["was_feedin_spike"] = is_feedin_spike

    # ── Spike incoming (forecast) ─────────────────────────────────────────────
    if prefs.get("alert_spike_incoming", True) and forecast:
        lookahead_min = int(prefs.get("alert_spike_incoming_minutes", 30))
        from datetime import timezone, timedelta as td
        now_utc = datetime.now(timezone.utc)
        cutoff  = now_utc + td(minutes=lookahead_min)
        upcoming = []
        for iv in forecast:
            raw = iv.get("startTime") or iv.get("nemTime", "")
            if not raw:
                continue
            try:
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                iv_ts = datetime.fromisoformat(raw)
                if iv_ts.tzinfo is None:
                    continue
                if now_utc <= iv_ts <= cutoff and iv.get("spikeStatus") in ("spike", "potential"):
                    upcoming.append(iv)
            except Exception:
                continue
        was_incoming = state.get("was_spike_incoming", False)
        is_incoming  = bool(upcoming)
        if is_incoming and not was_incoming:
            first = upcoming[0]
            label = "spike" if first.get("spikeStatus") == "spike" else "potential spike"
            msg = (f"⚡ Amber: {label.capitalize()} forecast in ~{lookahead_min} min\n"
                   f"Expected price: {first.get('perKwh', 0):.1f}¢/kWh — charge now if possible.")
            if send_notification(msg, title="Amber — Spike incoming", priority="high"):
                sent.append(msg)
        state["was_spike_incoming"] = is_incoming

    # ── Battery SOC low ───────────────────────────────────────────────────────
    if prefs.get("alert_soc_low", True) and foxess_realtime:
        threshold = float(prefs.get("alert_soc_low_pct", 20))
        bat_soc   = foxess_realtime.get("SoC", 0) or 0
        is_low    = bat_soc <= threshold
        was_low   = state.get("was_soc_low", False)
        if is_low and not was_low:
            msg = (f"🪫 Amber: Battery low — {bat_soc:.0f}%\n"
                   f"Current price: {price:.1f}¢/kWh ({descriptor})")
            if send_notification(msg, title="Amber — Battery low", priority="high"):
                sent.append(msg)
        elif was_low and bat_soc >= threshold + 5:
            state["was_soc_low"] = False
        else:
            state["was_soc_low"] = is_low

    # ── Battery full ──────────────────────────────────────────────────────────
    if prefs.get("alert_battery_full", True) and foxess_realtime:
        target   = float(prefs.get("alert_battery_full_pct", 95))
        bat_soc  = foxess_realtime.get("SoC", 0) or 0
        is_full  = bat_soc >= target
        was_full = state.get("was_battery_full", False)
        if is_full and not was_full:
            msg = (f"🔋 Amber: Battery full — {bat_soc:.0f}%\n"
                   f"Current price: {price:.1f}¢/kWh ({descriptor})")
            if send_notification(msg, title="Amber — Battery full", priority="default"):
                sent.append(msg)
        state["was_battery_full"] = is_full

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

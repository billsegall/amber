"""
Alert logic: checks current conditions against thresholds and fires
Signal notifications when state changes. Uses a JSON file for persistence
so we don't spam repeated alerts for the same condition.
"""
import json
import logging
import os
from datetime import datetime, date

from notifications import send_signal

log = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), "alert_state.json")

# ── Default thresholds (all overridable via .env) ─────────────────────────────
DEFAULT_SPIKE_NOTIFY        = True
DEFAULT_CHEAP_NOTIFY        = True
DEFAULT_CHEAP_THRESHOLD     = "extremelyLow"   # descriptor level
DEFAULT_RENEWABLES_NOTIFY   = True
DEFAULT_RENEWABLES_THRESHOLD = 80.0            # %
DEFAULT_DAILY_SUMMARY_HOUR  = 7               # 7am NEM time
DEFAULT_DAILY_SUMMARY       = True


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no"):
        return False
    return default


def check_and_alert(current: dict, feedin_current: dict | None, renewables_current: dict | None) -> list[str]:
    """
    Check current conditions against thresholds. Send alerts for new events.
    Returns list of alert messages that were sent (for logging/display).
    """
    if not current:
        return []

    state = _load_state()
    sent = []
    now_str = datetime.now().isoformat()

    spike_status  = current.get("spikeStatus", "none")
    descriptor    = current.get("descriptor", "neutral")
    price         = current.get("perKwh", 0)
    renewables_pct = renewables_current.get("renewables", 0) if renewables_current else 0

    # ── Spike alert ────────────────────────────────────────────────────────────
    if _env_bool("ALERT_SPIKE", DEFAULT_SPIKE_NOTIFY):
        prev_spike = state.get("spike_status", "none")
        if spike_status in ("spike", "potential") and prev_spike not in ("spike", "potential"):
            label = "PRICE SPIKE" if spike_status == "spike" else "Potential spike"
            msg = f"⚡ Amber Alert: {label}\nCurrent price: {price:.1f}¢/kWh\nStop charging — consider exporting if battery allows."
            if send_signal(msg):
                sent.append(msg)
        elif spike_status == "none" and prev_spike in ("spike", "potential"):
            msg = f"✅ Amber: Spike has cleared\nPrice back to {price:.1f}¢/kWh ({descriptor})"
            if send_signal(msg):
                sent.append(msg)
        state["spike_status"] = spike_status

    # ── Cheap window alert ─────────────────────────────────────────────────────
    if _env_bool("ALERT_CHEAP", DEFAULT_CHEAP_NOTIFY):
        cheap_threshold = os.environ.get("ALERT_CHEAP_DESCRIPTOR", DEFAULT_CHEAP_THRESHOLD)
        descriptor_rank = {"extremelyLow": 0, "veryLow": 1, "low": 2, "neutral": 3, "high": 4, "spike": 5}
        is_cheap = descriptor_rank.get(descriptor, 3) <= descriptor_rank.get(cheap_threshold, 0)
        was_cheap = state.get("was_cheap", False)

        if is_cheap and not was_cheap:
            msg = f"💚 Amber: Great time to charge!\nPrice: {price:.1f}¢/kWh ({descriptor})\nRenewables: {renewables_pct:.0f}%"
            if send_signal(msg):
                sent.append(msg)
        state["was_cheap"] = is_cheap

    # ── High renewables alert ──────────────────────────────────────────────────
    if _env_bool("ALERT_RENEWABLES", DEFAULT_RENEWABLES_NOTIFY) and renewables_current:
        threshold = float(os.environ.get("ALERT_RENEWABLES_PCT", DEFAULT_RENEWABLES_THRESHOLD))
        was_green = state.get("was_green", False)
        is_green  = renewables_pct >= threshold

        if is_green and not was_green:
            msg = f"🌱 Amber: High renewables — {renewables_pct:.0f}% green\nPrice: {price:.1f}¢/kWh\nGood time to charge from the grid."
            if send_signal(msg):
                sent.append(msg)
        state["was_green"] = is_green

    _save_state(state)
    return sent


def send_daily_summary(current: dict, stats_24h: dict, analysis: dict) -> bool:
    """Send a daily summary message. Called by scheduler at configured hour."""
    price     = current.get("perKwh", 0) if current else 0
    s         = stats_24h
    ba        = analysis.get("battery", {})
    ea        = analysis.get("ev", {})

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
    if ba.get("saving_dollars", 0) > 0:
        lines.append(f"Potential saving today: ${ba['saving_dollars'] + ea.get('saving_dollars', 0):.2f}")

    return send_signal("\n".join(lines))

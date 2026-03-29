"""
Background scheduler — polls Amber API every 5 minutes and fires alerts.
Uses APScheduler running inside the Flask process.
"""
import logging
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from amber_client import get_client, get_site_id
from alerts import check_and_alert, send_daily_summary, _load_state, _save_state
from optimizer import analyse, HardwareConfig

log = logging.getLogger(__name__)

_scheduler = None


def _poll():
    """Called every 5 minutes. Fetches current data and checks alert conditions."""
    try:
        client     = get_client()
        site_id    = get_site_id(client)
        general    = client.get_current_prices(site_id, next_intervals=96, previous_intervals=0, channel_type="general")
        feedin     = client.get_current_prices(site_id, next_intervals=96, previous_intervals=0, channel_type="feedIn")
        renewables = client.get_renewables(state="QLD", next_intervals=0, previous_intervals=0)

        # Find current intervals
        current         = next((iv for iv in general    if iv.get("type") == "CurrentInterval"), None)
        feedin_current  = next((iv for iv in feedin     if iv.get("type") == "CurrentInterval"), None)
        renew_current   = next((iv for iv in renewables if iv.get("type") == "CurrentRenewable"), None)

        sent = check_and_alert(current, feedin_current, renew_current)
        if sent:
            log.info("Alerts sent: %d", len(sent))

        # Store last poll time for the dashboard
        state = _load_state()
        state["last_poll"] = datetime.now().isoformat()
        _save_state(state)

    except Exception as e:
        log.error("Scheduler poll failed: %s", e)


def _daily_summary():
    """Called once per day at the configured hour."""
    try:
        client     = get_client()
        site_id    = get_site_id(client)
        general    = client.get_current_prices(site_id, next_intervals=96, previous_intervals=0, channel_type="general")
        feedin     = client.get_current_prices(site_id, next_intervals=96, previous_intervals=0, channel_type="feedIn")

        current        = next((iv for iv in general if iv.get("type") == "CurrentInterval"), None)
        feedin_current = next((iv for iv in feedin  if iv.get("type") == "CurrentInterval"), None)
        forecast       = [iv for iv in general if iv.get("type") == "ForecastInterval"]

        state = _load_state()
        battery_soc = state.get("battery_soc", 50.0)
        ev_soc      = state.get("ev_soc", 50.0)
        ev_target   = state.get("ev_target", 85.0)

        analysis = analyse(
            current_interval=current,
            forecast=forecast,
            feedin_current=feedin_current,
            battery_soc_pct=battery_soc,
            battery_target_pct=100.0,
            ev_soc_pct=ev_soc,
            ev_target_pct=ev_target,
            hw=HardwareConfig(),
        )
        send_daily_summary(current, analysis.get("stats_24h", {}), analysis)

    except Exception as e:
        log.error("Daily summary failed: %s", e)


def start(app):
    """Start the background scheduler. Call once at app startup."""
    global _scheduler
    if _scheduler is not None:
        return  # already running (Flask debug reloader calls this twice)

    poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", 300))
    summary_hour  = int(os.environ.get("DAILY_SUMMARY_HOUR", 7))

    _scheduler = BackgroundScheduler(timezone="Australia/Brisbane")
    _scheduler.add_job(_poll, IntervalTrigger(seconds=poll_interval), id="poll", max_instances=1)
    _scheduler.add_job(_daily_summary, CronTrigger(hour=summary_hour, minute=0), id="daily_summary")
    _scheduler.start()

    log.info("Scheduler started: poll every %ds, daily summary at %02d:00", poll_interval, summary_hour)

    # Run an immediate poll so alerts fire straight away on startup
    _poll()

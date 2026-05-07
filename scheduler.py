"""
Background scheduler — polls Amber + FOX ESS every N minutes and fires alerts.
"""
import logging
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from amber_client import get_client, get_site_id
from foxess_client import get_client as get_foxess_client, get_device_sn
from fronius_client import get_power_flow_safe
from alerts import check_and_alert, send_daily_summary, _load_state, _save_state
from optimizer import analyse, plan_battery_schedule, HardwareConfig
from solar_forecast_client import get_forecast
import db

log = logging.getLogger(__name__)

_scheduler     = None
_last_schedule = None  # groups list we last wrote; None = we haven't written yet


def _active_work_mode(schedule: dict | None) -> str:
    """Return the workMode of the currently-active schedule group, or 'SelfUse'."""
    if not schedule:
        return "unknown"
    now = datetime.now()
    now_mins = now.hour * 60 + now.minute
    for g in schedule.get("groups", []):
        if not g.get("enable"):
            continue
        start = g["startHour"] * 60 + g["startMinute"]
        end   = g["endHour"]   * 60 + g["endMinute"]
        if start <= now_mins < end:
            return g.get("workMode", "SelfUse")
    return "SelfUse"


def _maybe_update_schedule(fc, sn: str, current_schedule: dict | None,
                           foxess_realtime: dict, forecast: list, prefs: dict):
    global _last_schedule

    if not os.environ.get("FOXESS_AUTO_SCHEDULE", "").strip().lower() in ("1", "true", "yes"):
        return

    if not current_schedule:
        return

    current_groups = current_schedule.get("groups", [])

    # If the current schedule differs from what we last wrote, VPP (or user) changed it.
    # Don't overwrite for this poll cycle — let the external control finish.
    if _last_schedule is not None and current_groups != _last_schedule:
        log.info("FOX ESS schedule changed externally — skipping write this cycle")
        _last_schedule = current_groups
        return

    battery_soc = float(foxess_realtime.get("SoC") or 50)
    state = _load_state()
    hw = HardwareConfig(
        battery_capacity_kwh     = float(prefs.get("battery_capacity_kwh", 42.0)),
        battery_min_soc_pct      = float(prefs.get("battery_min_soc_pct", 10.0)),
        battery_max_charge_kw    = float(prefs.get("battery_max_charge_kw", 10.0)),
        battery_max_discharge_kw = float(prefs.get("battery_max_discharge_kw", 10.0)),
        ev_capacity_kwh          = float(prefs.get("ev_capacity_kwh", 100.0)),
        ev_charge_kw             = float(prefs.get("ev_charge_kw", 7.0)),
    )

    new_groups = plan_battery_schedule(forecast, battery_soc, hw)
    if new_groups == _last_schedule:
        log.info("FOX ESS schedule unchanged — skipping write")
        return

    ok, err = fc.set_schedule(sn, new_groups)
    if ok:
        log.info("FOX ESS schedule updated: %s", new_groups)
        _last_schedule = new_groups
    else:
        log.warning("FOX ESS schedule write failed: %s", err)


def _poll():
    """Called every N minutes. Fetches current data and checks alert conditions."""
    try:
        prefs = db.get_default_preferences()

        client  = get_client()
        site_id = get_site_id(client)
        general    = client.get_current_prices(site_id, next_intervals=96, previous_intervals=0,
                                               channel_type="general")
        feedin     = client.get_current_prices(site_id, next_intervals=96, previous_intervals=0,
                                               channel_type="feedIn")
        renewables = client.get_renewables(state=prefs.get("location_state", "QLD"),
                                           next_intervals=0, previous_intervals=0)

        current        = next((iv for iv in general    if iv.get("type") == "CurrentInterval"), None)
        feedin_current = next((iv for iv in feedin     if iv.get("type") == "CurrentInterval"), None)
        renew_current  = next((iv for iv in renewables if iv.get("type") == "CurrentRenewable"), None)
        forecast       = [iv for iv in general if iv.get("type") == "ForecastInterval"]

        # Fetch FOX ESS realtime for charging-stop detection
        foxess_realtime = None
        try:
            fc = get_foxess_client()
            if fc:
                sn = get_device_sn()
                if sn:
                    foxess_realtime = fc.get_realtime(sn)
                    if foxess_realtime:
                        schedule = fc.get_schedule(sn)
                        work_mode = _active_work_mode(schedule)
                        foxess_realtime["workMode"] = work_mode
                        log.info("FOX ESS: SoC=%.0f%% charge=%.2fkW discharge=%.2fkW workMode=%s",
                                 foxess_realtime.get("SoC", 0),
                                 foxess_realtime.get("batChargePower", 0) or 0,
                                 foxess_realtime.get("batDischargePower", 0) or 0,
                                 work_mode)
                        _maybe_update_schedule(fc, sn, schedule, foxess_realtime, forecast, prefs)
                    else:
                        log.warning("FOX ESS returned no data")
        except Exception as e:
            log.warning("FOX ESS unavailable in scheduler: %s", e)

        sent = check_and_alert(current, feedin_current, renew_current, foxess_realtime, prefs, forecast)
        if sent:
            log.info("Alerts sent: %d", len(sent))

    except Exception as e:
        log.error("Scheduler poll failed: %s", e)


def _daily_summary():
    """Called once per day at the configured hour."""
    try:
        prefs   = db.get_default_preferences()
        if not prefs.get("alert_daily_summary", True):
            log.info("Daily summary skipped — disabled in prefs")
            return

        state = _load_state()
        today = datetime.now().strftime("%Y-%m-%d")
        if state.get("last_daily_summary_date") == today:
            log.info("Daily summary skipped — already sent today (%s)", today)
            return
        client  = get_client()
        site_id = get_site_id(client)
        general = client.get_current_prices(site_id, next_intervals=96, previous_intervals=0,
                                            channel_type="general")
        feedin  = client.get_current_prices(site_id, next_intervals=96, previous_intervals=0,
                                            channel_type="feedIn")

        current        = next((iv for iv in general if iv.get("type") == "CurrentInterval"), None)
        feedin_current = next((iv for iv in feedin  if iv.get("type") == "CurrentInterval"), None)
        forecast       = [iv for iv in general if iv.get("type") == "ForecastInterval"]

        battery_soc = state.get("battery_soc", 50.0)
        ev_soc      = state.get("ev_soc",      50.0)
        ev_target   = prefs.get("ev_target_soc", 85.0)

        hw = HardwareConfig(
            battery_capacity_kwh     = prefs.get("battery_capacity_kwh", 42.0),
            battery_min_soc_pct      = prefs.get("battery_min_soc_pct", 10.0),
            battery_max_charge_kw    = prefs.get("battery_max_charge_kw", 10.0),
            battery_max_discharge_kw = prefs.get("battery_max_discharge_kw", 10.0),
            ev_capacity_kwh          = prefs.get("ev_capacity_kwh", 100.0),
            ev_charge_kw             = prefs.get("ev_charge_kw", 7.0),
        )
        analysis = analyse(
            current_interval=current, forecast=forecast, feedin_current=feedin_current,
            battery_soc_pct=battery_soc, battery_target_pct=100.0,
            ev_soc_pct=ev_soc, ev_target_pct=ev_target, hw=hw,
        )
        if send_daily_summary(current, analysis.get("stats_24h", {}), analysis):
            state["last_daily_summary_date"] = today
            _save_state(state)

    except Exception as e:
        log.error("Daily summary failed: %s", e)


def _record_solar_actual():
    """Called once daily at 21:30 AEST. Records today's actual generation and forecast."""
    try:
        prefs = db.get_default_preferences()
        today = datetime.now().strftime("%Y-%m-%d")

        # Actual generation from Fronius
        solar = get_power_flow_safe()
        actual_kwh = None
        if solar and solar.get("e_day_wh") is not None:
            actual_kwh = round((solar["e_day_wh"] or 0) / 1000.0, 3)
            log.info("Solar actual for %s: %.2f kWh", today, actual_kwh)
        else:
            log.warning("Fronius unavailable — skipping solar actual for %s", today)

        # Forecast from forecast.solar (to pair with actual)
        forecast_kwh = None
        try:
            fc = get_forecast(
                lat=float(prefs.get("solar_lat", -27.47)),
                lon=float(prefs.get("solar_lon",  153.02)),
                tilt=int(prefs.get("solar_tilt",  20)),
                azimuth=int(prefs.get("solar_azimuth", 0)),
                kwp=float(prefs.get("solar_kwp",  5.6)),
            )
            if fc:
                forecast_kwh = fc["today_kwh"]
        except Exception as e:
            log.warning("forecast.solar unavailable in _record_solar_actual: %s", e)

        if actual_kwh is not None or forecast_kwh is not None:
            db.upsert_solar_actual(today, actual_kwh=actual_kwh, forecast_kwh=forecast_kwh)

    except Exception as e:
        log.error("_record_solar_actual failed: %s", e)


def start(app):
    """Start the background scheduler. Safe to call from main process only."""
    global _scheduler
    if _scheduler is not None:
        return

    prefs         = db.get_default_preferences()
    poll_interval = int(prefs.get("poll_interval_seconds", 300))
    summary_hour  = int(prefs.get("daily_summary_hour", 7))

    _scheduler = BackgroundScheduler(timezone="Australia/Brisbane")
    _scheduler.add_job(_poll, IntervalTrigger(seconds=poll_interval), id="poll", max_instances=1)
    _scheduler.add_job(_daily_summary, CronTrigger(hour=summary_hour, minute=0), id="daily_summary")
    _scheduler.add_job(_record_solar_actual, CronTrigger(hour=21, minute=30), id="solar_actual")
    _scheduler.start()

    log.info("Scheduler started: poll every %ds, daily summary at %02d:00", poll_interval, summary_hour)
    _poll()  # immediate first poll

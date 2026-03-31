"""
forecast.solar public API client.
Free tier: 2-day horizon, hourly resolution, no API key required.
Endpoint: GET https://api.forecast.solar/estimate/{lat}/{lon}/{dec}/{az}/{kwp}
"""
import logging
from datetime import datetime, timezone, timedelta

import requests

log = logging.getLogger(__name__)

_BASE = "https://api.forecast.solar/estimate"
_TIMEOUT = 10


def get_forecast(lat: float, lon: float, tilt: int, azimuth: int, kwp: float) -> dict | None:
    """
    Fetch solar generation forecast from forecast.solar.

    Returns dict with:
      "today_kwh"    — forecast kWh for today
      "tomorrow_kwh" — forecast kWh for tomorrow
      "hourly"       — list of (datetime_local_str, kw) tuples for the next 48h
    Returns None on any error.
    """
    url = f"{_BASE}/{lat}/{lon}/{tilt}/{azimuth}/{kwp}"
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("forecast.solar fetch failed: %s", e)
        return None

    result = data.get("result", {})
    wh_day = result.get("watt_hours_day", {})
    watts  = result.get("watts", {})

    # Dates are YYYY-MM-DD in local (site) time returned by the API
    aest = timezone(timedelta(hours=10))
    today_str    = datetime.now(aest).strftime("%Y-%m-%d")
    tomorrow_str = (datetime.now(aest) + timedelta(days=1)).strftime("%Y-%m-%d")

    today_kwh    = (wh_day.get(today_str,    0) or 0) / 1000.0
    tomorrow_kwh = (wh_day.get(tomorrow_str, 0) or 0) / 1000.0

    # Hourly power: dict of "YYYY-MM-DD HH:MM:SS" -> watts
    hourly = []
    for ts_str, w in sorted(watts.items()):
        hourly.append((ts_str, round((w or 0) / 1000.0, 3)))

    log.info("forecast.solar: today=%.1fkWh tomorrow=%.1fkWh", today_kwh, tomorrow_kwh)
    return {
        "today_kwh":    round(today_kwh,    2),
        "tomorrow_kwh": round(tomorrow_kwh, 2),
        "hourly":       hourly,
    }

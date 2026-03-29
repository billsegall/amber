import os
import requests
from datetime import date, timedelta

BASE_URL = "https://api.amber.com.au/v1"
RENEWABLES_URL = "https://api.amber.com.au/v1"


class AmberClient:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def get_sites(self) -> list[dict]:
        r = self.session.get(f"{BASE_URL}/sites")
        r.raise_for_status()
        return r.json()

    def get_current_prices(self, site_id: str, next_intervals: int = 96, previous_intervals: int = 12,
                           channel_type: str | None = None) -> list[dict]:
        """Returns current interval + forecast. Default: 96 x 30-min = 48h ahead.
        Pass channel_type='general' or 'feedIn' to filter to one channel."""
        params = {"next": next_intervals, "previous": previous_intervals, "resolution": 30}
        r = self.session.get(f"{BASE_URL}/sites/{site_id}/prices/current", params=params)
        r.raise_for_status()
        intervals = r.json()
        if channel_type:
            intervals = [iv for iv in intervals if iv.get("channelType") == channel_type]
        return intervals

    def get_historical_prices(self, site_id: str, start_date: date, end_date: date) -> list[dict]:
        params = {"startDate": start_date.isoformat(), "endDate": end_date.isoformat(), "resolution": 30}
        r = self.session.get(f"{BASE_URL}/sites/{site_id}/prices", params=params)
        r.raise_for_status()
        return r.json()

    def get_usage(self, site_id: str, start_date: date, end_date: date) -> list[dict]:
        params = {"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}
        r = self.session.get(f"{BASE_URL}/sites/{site_id}/usage", params=params)
        r.raise_for_status()
        return r.json()

    def get_renewables(self, state: str = "QLD", next_intervals: int = 96, previous_intervals: int = 12) -> list[dict]:
        """Public endpoint — no auth required."""
        params = {"next": next_intervals, "previous": previous_intervals, "resolution": 30}
        r = requests.get(f"{RENEWABLES_URL}/state/{state}/renewables/current", params=params)
        r.raise_for_status()
        return r.json()


def get_client() -> AmberClient:
    token = os.environ.get("AMBER_API_TOKEN")
    if not token:
        raise RuntimeError("AMBER_API_TOKEN not set in environment")
    return AmberClient(token)


def get_site_id(client: AmberClient) -> str:
    """Return AMBER_SITE_ID from env, or auto-discover from first site."""
    site_id = os.environ.get("AMBER_SITE_ID", "").strip()
    if site_id:
        return site_id
    sites = client.get_sites()
    if not sites:
        raise RuntimeError("No sites found for this Amber account")
    return sites[0]["id"]

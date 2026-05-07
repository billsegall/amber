"""
Zeekr EV dashboard client.
Fetches state-of-charge via the zeekr.segall.net API (cookie-based session).
"""
import logging
import os

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://zeekr.segall.net"

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def _login(session: requests.Session) -> bool:
    email    = os.environ.get("ZEEKR_USERNAME", "").strip()
    password = os.environ.get("ZEEKR_PASSWORD", "").strip()
    if not (email and password):
        log.warning("ZEEKR_USERNAME or ZEEKR_PASSWORD not set")
        return False
    try:
        r = session.post(
            f"{BASE_URL}/api/login",
            json={"email": email, "password": password},
            timeout=10,
        )
        if r.ok and r.json().get("ok"):
            return True
        log.warning("Zeekr login failed: %s", r.text)
        return False
    except Exception as e:
        log.warning("Zeekr login error: %s", e)
        return False


def get_charge_level() -> float | None:
    """Return EV state-of-charge as a float (0–100), or None on failure."""
    session = _get_session()
    for attempt in range(2):
        try:
            r = session.get(f"{BASE_URL}/api/chargeLevel", timeout=10)
            if r.status_code == 401:
                if attempt == 0 and _login(session):
                    continue
                return None
            r.raise_for_status()
            return float(r.json()["chargeLevel"])
        except Exception as e:
            log.warning("Zeekr chargeLevel error: %s", e)
            return None
    return None

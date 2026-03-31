"""
FOX ESS Cloud API client.
Auth: private token + MD5 signature per request.
Rate limit: 1440 calls/day per inverter.
"""
import hashlib
import logging
import os
import time
import requests

log = logging.getLogger(__name__)

BASE_URL = "https://www.foxesscloud.com"


def _signature(token: str, path: str, timestamp: int) -> str:
    # Raw string — \r\n is literal backslash-r-backslash-n, not CR+LF
    raw = fr"{path}\r\n{token}\r\n{timestamp}"
    return hashlib.md5(raw.encode("UTF-8")).hexdigest()


def _headers(token: str, path: str) -> dict:
    ts = int(time.time() * 1000)
    return {
        "Token":        token,
        "Timestamp":    str(ts),
        "Signature":    _signature(token, path, ts),
        "Lang":         "en",
        "Content-Type": "application/json",
    }


class FoxESSClient:
    def __init__(self, token: str):
        self.token = token

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        try:
            r = requests.get(
                f"{BASE_URL}{path}",
                params=params,
                headers=_headers(self.token, path),
                timeout=10,
            )
            r.raise_for_status()
            body = r.json()
            if body.get("errno", 0) != 0:
                log.warning("FOX ESS error %s: %s", body.get("errno"), body.get("msg"))
                return None
            return body.get("result")
        except Exception as e:
            log.error("FOX ESS GET %s failed: %s", path, e)
            return None

    def _post(self, path: str, payload: dict) -> dict | None:
        try:
            r = requests.post(
                f"{BASE_URL}{path}",
                json=payload,
                headers=_headers(self.token, path),
                timeout=10,
            )
            r.raise_for_status()
            body = r.json()
            if body.get("errno", 0) != 0:
                log.warning("FOX ESS error %s: %s", body.get("errno"), body.get("msg"))
                return None
            return body.get("result")
        except Exception as e:
            log.error("FOX ESS POST %s failed: %s", path, e)
            return None

    def _post_ok(self, path: str, payload: dict) -> bool:
        """POST and return True if errno == 0, regardless of result content."""
        try:
            r = requests.post(
                f"{BASE_URL}{path}",
                json=payload,
                headers=_headers(self.token, path),
                timeout=10,
            )
            r.raise_for_status()
            body = r.json()
            errno = body.get("errno", -1)
            if errno != 0:
                log.warning("FOX ESS error %s: %s", errno, body.get("msg"))
                return False
            return True
        except Exception as e:
            log.error("FOX ESS POST %s failed: %s", path, e)
            return False

    def get_devices(self) -> list[dict]:
        """List all inverters on the account."""
        result = self._post("/op/v0/device/list", {"pageSize": 10, "currentPage": 1})
        if result is None:
            return []
        return result.get("data", [])

    def get_battery_soc(self, sn: str) -> float | None:
        """Return current battery SOC % or None."""
        result = self._post("/op/v0/device/real/query", {
            "sn": sn,
            "variables": ["SoC"],
        })
        if not result:
            return None
        datas = result[0].get("datas", []) if result else []
        for item in datas:
            if item.get("variable") == "SoC":
                return item.get("value")
        return None

    def get_realtime(self, sn: str) -> dict | None:
        """Return a dict of key realtime variables."""
        variables = ["SoC", "batChargePower", "batDischargePower",
                     "generationPower", "loadsPower", "gridConsumptionPower",
                     "feedinPower", "pvPower"]
        result = self._post("/op/v0/device/real/query", {
            "sn": sn,
            "variables": variables,
        })
        if not result:
            return None
        # result is a list of device objects each with a "datas" list
        datas = result[0].get("datas", []) if result else []
        return {item["variable"]: item.get("value") for item in datas}


    # ── Force charge time windows ─────────────────────────────────────────────

    def get_force_charge(self, sn: str) -> dict | None:
        """Return force charge time config (two periods)."""
        return self._post("/op/v0/device/battery/forceChargeTime/get", {"sn": sn})

    def set_force_charge(self, sn: str, data: dict) -> bool:
        """
        Set force charge time windows.
        data keys: enable1, startTime1/endTime1 ({"hour":H,"minute":M}),
                   enable2, startTime2/endTime2.
        Returns True on success.
        """
        return self._post_ok("/op/v0/device/battery/forceChargeTime/set", {"sn": sn, **data})

    # ── Work mode & SOC settings ──────────────────────────────────────────────

    def get_settings(self, sn: str, keys: list[str]) -> dict | None:
        """Query one or more device settings by key. Returns {key: value, ...}."""
        result = self._post("/op/v0/device/setting/query", {"sn": sn, "keys": keys})
        if not result:
            return None
        return {item["key"]: item.get("value") for item in result}

    def set_setting(self, sn: str, key: str, value: str) -> bool:
        """Set a single device setting. Returns True on success."""
        return self._post_ok("/op/v0/device/setting/set", {"sn": sn, "key": key, "value": value})


def get_client() -> FoxESSClient | None:
    token = os.environ.get("FOXESS_API_KEY", "").strip()
    if not token:
        return None
    return FoxESSClient(token)


def get_device_sn() -> str | None:
    """Return SN from env or auto-discover from first device."""
    sn = os.environ.get("FOXESS_DEVICE_SN", "").strip()
    if sn:
        return sn
    client = get_client()
    if not client:
        return None
    devices = client.get_devices()
    if not devices:
        log.warning("No FOX ESS devices found")
        return None
    sn = devices[0].get("deviceSN")
    log.info("Auto-discovered FOX ESS device SN: %s", sn)
    return sn

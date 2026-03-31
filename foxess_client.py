"""
FOX ESS Cloud API client.
Supports two auth modes:
  1. API key + MD5 signature (data access only)
  2. OAuth bearer token (data + device control)
OAuth tokens are stored in foxess_tokens.json (gitignored).
"""
import hashlib
import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

BASE_URL    = "https://www.foxesscloud.com"
TOKENS_FILE = os.path.join(os.path.dirname(__file__), "foxess_tokens.json")


# ── OAuth token storage ───────────────────────────────────────────────────────

def _load_tokens() -> dict:
    try:
        with open(TOKENS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_tokens(tokens: dict):
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f)


def _refresh_access_token() -> str | None:
    """Use the stored refresh_token to get a new access_token. Returns new token or None."""
    client_id     = os.environ.get("FOXESS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("FOXESS_CLIENT_SECRET", "").strip()
    tokens        = _load_tokens()
    refresh_token = tokens.get("refresh_token", "")
    if not (client_id and client_secret and refresh_token):
        return None
    try:
        r = requests.post(
            f"{BASE_URL}/oauth2/refresh",
            data={
                "grant_type":    "refresh_token",
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if "access_token" in data:
            tokens["access_token"]  = data["access_token"]
            tokens["expires_at"]    = time.time() + data.get("expires_in", 86400) - 60
            if "refresh_token" in data:
                tokens["refresh_token"] = data["refresh_token"]
            _save_tokens(tokens)
            log.info("FOX ESS OAuth token refreshed")
            return tokens["access_token"]
    except Exception as e:
        log.error("FOX ESS token refresh failed: %s", e)
    return None


def _get_bearer_token() -> str | None:
    """Return a valid access_token, refreshing if needed. None if OAuth not configured."""
    tokens = _load_tokens()
    if not tokens.get("access_token"):
        return None
    # Refresh if expiring within 60s
    if time.time() >= tokens.get("expires_at", 0):
        return _refresh_access_token()
    return tokens["access_token"]


def exchange_code_for_tokens(code: str) -> bool:
    """Exchange an authorization code for access+refresh tokens. Returns True on success."""
    client_id     = os.environ.get("FOXESS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("FOXESS_CLIENT_SECRET", "").strip()
    redirect_uri  = os.environ.get("FOXESS_REDIRECT_URI", "http://localhost").strip()
    if not (client_id and client_secret):
        log.error("FOXESS_CLIENT_ID / FOXESS_CLIENT_SECRET not set")
        return False
    try:
        r = requests.post(
            f"{BASE_URL}/oauth2/token",
            data={
                "grant_type":    "authorization_code",
                "client_id":     client_id,
                "client_secret": client_secret,
                "redirect_uri":  redirect_uri,
                "code":          code,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        log.info("FOX ESS token exchange response: %s", data)
        if "access_token" not in data:
            log.error("No access_token in response: %s", data)
            return False
        _save_tokens({
            "access_token":  data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_at":    time.time() + data.get("expires_in", 86400) - 60,
        })
        log.info("FOX ESS OAuth tokens saved")
        return True
    except Exception as e:
        log.error("FOX ESS token exchange failed: %s", e)
        return False


def get_auth_url() -> str | None:
    """Return the FOX ESS authorization URL, or None if OAuth not configured."""
    client_id    = os.environ.get("FOXESS_CLIENT_ID", "").strip()
    redirect_uri = os.environ.get("FOXESS_REDIRECT_URI", "http://localhost").strip()
    if not client_id:
        return None
    return (
        f"{BASE_URL}/h5/auth/foxessindex"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=data_access,device_control"
    )


def has_oauth_tokens() -> bool:
    return bool(_load_tokens().get("access_token"))


# ── MD5 signature auth (read-only API key) ────────────────────────────────────

def _signature(token: str, path: str, timestamp: int) -> str:
    raw = fr"{path}\r\n{token}\r\n{timestamp}"
    return hashlib.md5(raw.encode("UTF-8")).hexdigest()


def _apikey_headers(token: str, path: str) -> dict:
    ts = int(time.time() * 1000)
    return {
        "Token":        token,
        "Timestamp":    str(ts),
        "Signature":    _signature(token, path, ts),
        "Lang":         "en",
        "Content-Type": "application/json",
    }


def _oauth_headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_bearer_token()}",
        "Lang":          "en",
        "Content-Type":  "application/json",
    }


# ── Client ────────────────────────────────────────────────────────────────────

class FoxESSClient:
    def __init__(self, token: str):
        self.token = token  # API key (MD5 auth) — used as fallback for read ops

    def _headers_for(self, prefer_oauth: bool = False) -> dict:
        """Return OAuth headers if available (and preferred), else API key headers."""
        bearer = _get_bearer_token()
        if prefer_oauth and bearer:
            return {"Authorization": f"Bearer {bearer}", "Lang": "en", "Content-Type": "application/json"}
        return _apikey_headers(self.token, "")  # path unused in this wrapper

    def _post(self, path: str, payload: dict, oauth: bool = False) -> dict | None:
        bearer = _get_bearer_token()
        if oauth and bearer:
            hdrs = {"Authorization": f"Bearer {bearer}", "Lang": "en", "Content-Type": "application/json"}
        else:
            hdrs = _apikey_headers(self.token, path)
        try:
            r = requests.post(f"{BASE_URL}{path}", json=payload, headers=hdrs, timeout=10)
            r.raise_for_status()
            body = r.json()
            if body.get("errno", 0) != 0:
                log.warning("FOX ESS error %s: %s", body.get("errno"), body.get("msg"))
                return None
            return body.get("result")
        except Exception as e:
            log.error("FOX ESS POST %s failed: %s", path, e)
            return None

    def _post_ok(self, path: str, payload: dict, oauth: bool = True) -> tuple[bool, str]:
        """POST and return (True, "") if errno == 0, else (False, error_msg)."""
        bearer = _get_bearer_token()
        if oauth and bearer:
            hdrs = {"Authorization": f"Bearer {bearer}", "Lang": "en", "Content-Type": "application/json"}
        else:
            hdrs = _apikey_headers(self.token, path)
        try:
            log.info("FOX ESS POST %s payload: %s", path, payload)
            r = requests.post(f"{BASE_URL}{path}", json=payload, headers=hdrs, timeout=10)
            r.raise_for_status()
            body = r.json()
            log.info("FOX ESS POST %s response: %s", path, body)
            errno = body.get("errno", -1)
            msg   = body.get("msg", "")
            if errno != 0:
                log.warning("FOX ESS error %s: %s", errno, msg)
                return False, f"API error {errno}: {msg}"
            return True, ""
        except Exception as e:
            log.error("FOX ESS POST %s failed: %s", path, e)
            return False, str(e)

    def get_devices(self) -> list[dict]:
        result = self._post("/op/v0/device/list", {"pageSize": 10, "currentPage": 1})
        if result is None:
            return []
        return result.get("data", [])

    def get_realtime(self, sn: str) -> dict | None:
        variables = ["SoC", "batChargePower", "batDischargePower",
                     "generationPower", "loadsPower", "gridConsumptionPower",
                     "feedinPower", "pvPower"]
        result = self._post("/op/v0/device/real/query", {"sn": sn, "variables": variables})
        if not result:
            return None
        datas = result[0].get("datas", []) if result else []
        return {item["variable"]: item.get("value") for item in datas}

    # ── Force charge time windows ─────────────────────────────────────────────

    def get_force_charge(self, sn: str) -> dict | None:
        result = self._post("/op/v0/device/battery/forceChargeTime/get", {"sn": sn}, oauth=True)
        log.info("FOX ESS get_force_charge raw result: %s", result)
        return result

    def set_force_charge(self, sn: str, data: dict) -> tuple[bool, str]:
        return self._post_ok("/op/v0/device/battery/forceChargeTime/set", {"sn": sn, **data}, oauth=True)

    # ── Work mode & SOC settings ──────────────────────────────────────────────

    def get_settings(self, sn: str, keys: list[str]) -> dict | None:
        result = self._post("/op/v0/device/setting/query", {"sn": sn, "keys": keys}, oauth=True)
        if not result:
            return None
        return {item["key"]: item.get("value") for item in result}

    def set_setting(self, sn: str, key: str, value: str) -> tuple[bool, str]:
        return self._post_ok("/op/v0/device/setting/set", {"sn": sn, "key": key, "value": value}, oauth=True)


def get_client() -> FoxESSClient | None:
    token = os.environ.get("FOXESS_API_KEY", "").strip()
    if not token:
        return None
    return FoxESSClient(token)


def get_device_sn() -> str | None:
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

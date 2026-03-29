"""
Fronius Primo local JSON API client.
No authentication required — inverter must be reachable on local network.
Inverter sleeps at night (no generation); all methods return None gracefully.
"""
import logging
import os
import requests

log = logging.getLogger(__name__)

TIMEOUT = 5  # seconds — fast fail if inverter unreachable


class FroniusClient:
    def __init__(self, host: str):
        self.base = f"http://{host}/solar_api/v1"

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        try:
            r = requests.get(f"{self.base}/{path}", params=params, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            # Fronius wraps everything in Body.Data
            body = data.get("Body", {})
            if "Data" in body:
                return body["Data"]
            return body
        except requests.exceptions.ConnectionError:
            log.debug("Fronius unreachable (inverter may be asleep)")
            return None
        except Exception as e:
            log.warning("Fronius API error: %s", e)
            return None

    def get_power_flow(self) -> dict | None:
        """
        Fastest endpoint. Returns current power flows:
          P_Grid   — grid import (+) / export (-)  in W
          P_Load   — current household consumption  in W
          P_PV     — solar generation               in W
          E_Day    — energy generated today         in Wh
          E_Year   — energy generated this year     in Wh
        Returns None if inverter is asleep/unreachable.
        """
        data = self._get("GetPowerFlowRealtimeData.fcgi")
        if not data:
            return None
        site = data.get("Site", {})
        if not site:
            return None
        return {
            "p_grid_w":    site.get("P_Grid"),      # + = importing, - = exporting
            "p_load_w":    site.get("P_Load"),       # always negative (consumption)
            "p_pv_w":      site.get("P_PV"),         # solar generation in W
            "e_day_wh":    site.get("E_Day"),        # Wh generated today
            "e_year_wh":   site.get("E_Year"),
            "rel_autonomy": site.get("rel_Autonomy"),  # % self-sufficient
            "rel_selfconsumption": site.get("rel_SelfConsumption"),
        }

    def get_inverter_data(self) -> dict | None:
        """Returns inverter status and AC output."""
        data = self._get("GetInverterRealtimeData.cgi",
                         params={"Scope": "Device", "DeviceId": "1",
                                 "DataCollection": "CommonInverterData"})
        if not data:
            return None
        def _val(key):
            entry = data.get(key, {})
            return entry.get("Value") if isinstance(entry, dict) else None

        return {
            "pac_w":      _val("PAC"),       # AC power output W
            "sac_va":     _val("SAC"),       # apparent power VA
            "iac_a":      _val("IAC"),       # AC current A
            "uac_v":      _val("UAC"),       # AC voltage V
            "fac_hz":     _val("FAC"),       # AC frequency Hz
            "e_day_wh":   _val("DAY_ENERGY"),
            "e_year_wh":  _val("YEAR_ENERGY"),
            "e_total_wh": _val("TOTAL_ENERGY"),
        }


def get_client() -> FroniusClient | None:
    host = os.environ.get("FRONIUS_IP", "").strip()
    if not host:
        return None
    return FroniusClient(host)


def get_power_flow_safe() -> dict | None:
    """Convenience: returns power flow or None without raising."""
    client = get_client()
    if client is None:
        return None
    return client.get_power_flow()

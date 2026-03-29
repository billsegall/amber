"""
Arbitrage and charging opportunity analysis.

All prices are in c/kWh. Energy in kWh. Power rates in kW.
Intervals are 30 minutes, so 1 interval = 0.5h.
"""
from dataclasses import dataclass, field


INTERVAL_HOURS = 0.5  # 30-minute intervals


@dataclass
class HardwareConfig:
    battery_capacity_kwh: float = 42.0
    battery_min_soc_pct: float = 10.0
    battery_max_charge_kw: float = 10.0
    battery_max_discharge_kw: float = 10.0
    ev_capacity_kwh: float = 100.0
    ev_charge_kw: float = 7.0

    @property
    def battery_usable_kwh(self) -> float:
        return self.battery_capacity_kwh * (1 - self.battery_min_soc_pct / 100)

    def battery_needed_kwh(self, current_soc_pct: float, target_soc_pct: float = 100.0) -> float:
        needed = (target_soc_pct - current_soc_pct) / 100 * self.battery_capacity_kwh
        return max(0.0, needed)

    def ev_needed_kwh(self, current_soc_pct: float, target_soc_pct: float = 85.0) -> float:
        needed = (target_soc_pct - current_soc_pct) / 100 * self.ev_capacity_kwh
        return max(0.0, needed)


@dataclass
class ChargingWindow:
    start_time: str
    end_time: str
    intervals: list[dict]
    total_kwh: float
    avg_price: float        # c/kWh
    total_cost: float       # cents
    descriptor: str         # worst descriptor in window


@dataclass
class OpportunityReport:
    current_price: float            # c/kWh retail
    current_descriptor: str
    price_now_cost: float           # cents to charge needed kWh now
    best_window: ChargingWindow | None
    saving_cents: float             # saving vs charging now
    saving_pct: float
    feedin_opportunity: bool        # is it worth exporting now?
    feedin_price: float             # c/kWh feed-in right now
    stats_24h: dict                 # min/max/avg over next 24h


DESCRIPTOR_RANK = {
    "extremelyLow": 0,
    "veryLow": 1,
    "low": 2,
    "neutral": 3,
    "high": 4,
    "spike": 5,
}


def _worst_descriptor(intervals: list[dict]) -> str:
    return max((iv.get("descriptor", "neutral") for iv in intervals),
               key=lambda d: DESCRIPTOR_RANK.get(d, 3))


def _interval_kwh(charge_kw: float) -> float:
    return charge_kw * INTERVAL_HOURS


def find_cheapest_window(forecast: list[dict], needed_kwh: float, charge_kw: float) -> ChargingWindow | None:
    """
    Find the cheapest contiguous block of forecast intervals that can deliver
    needed_kwh at charge_kw. Uses a sliding window over the forecast.
    """
    if not forecast or needed_kwh <= 0:
        return None

    kwh_per_interval = _interval_kwh(charge_kw)
    intervals_needed = max(1, int(needed_kwh / kwh_per_interval + 0.999))  # ceil

    if intervals_needed > len(forecast):
        intervals_needed = len(forecast)

    best_cost = None
    best_start = 0

    for i in range(len(forecast) - intervals_needed + 1):
        window = forecast[i:i + intervals_needed]
        cost = sum(iv.get("perKwh", 0) * kwh_per_interval for iv in window)
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_start = i

    window_ivs = forecast[best_start:best_start + intervals_needed]
    delivered_kwh = kwh_per_interval * len(window_ivs)
    avg_price = best_cost / delivered_kwh if delivered_kwh else 0

    return ChargingWindow(
        start_time=window_ivs[0].get("nemTime", ""),
        end_time=window_ivs[-1].get("nemTime", ""),
        intervals=window_ivs,
        total_kwh=delivered_kwh,
        avg_price=avg_price,
        total_cost=best_cost,
        descriptor=_worst_descriptor(window_ivs),
    )


def analyse(
    current_interval: dict,
    forecast: list[dict],
    feedin_current: dict | None,
    battery_soc_pct: float,
    battery_target_pct: float,
    ev_soc_pct: float,
    ev_target_pct: float,
    hw: HardwareConfig | None = None,
) -> dict:
    """
    Returns a dict with opportunity analysis for both battery and EV.
    """
    if hw is None:
        hw = HardwareConfig()

    results = {}

    current_price = current_interval.get("perKwh", 0) if current_interval else 0
    feedin_price = feedin_current.get("perKwh", 0) if feedin_current else 0

    # 24h price stats
    next_48 = forecast[:96]  # up to 48 intervals = 24h at 30-min
    prices_24h = [iv.get("perKwh", 0) for iv in next_48 if iv.get("perKwh") is not None]
    stats_24h = {
        "min": min(prices_24h) if prices_24h else 0,
        "max": max(prices_24h) if prices_24h else 0,
        "avg": sum(prices_24h) / len(prices_24h) if prices_24h else 0,
        "current": current_price,
        "current_percentile": (
            sum(1 for p in prices_24h if p <= current_price) / len(prices_24h) * 100
            if prices_24h else 50
        ),
    }

    # ── Battery analysis ───────────────────────────────────────────────────────
    batt_needed = hw.battery_needed_kwh(battery_soc_pct, battery_target_pct)
    batt_cost_now = current_price * batt_needed / 100  # dollars
    batt_window = find_cheapest_window(forecast, batt_needed, hw.battery_max_charge_kw)
    batt_saving = 0.0
    batt_saving_pct = 0.0
    if batt_window and batt_cost_now > 0:
        batt_saving = batt_cost_now - batt_window.total_cost / 100
        batt_saving_pct = batt_saving / batt_cost_now * 100

    results["battery"] = {
        "soc_pct": battery_soc_pct,
        "target_pct": battery_target_pct,
        "needed_kwh": round(batt_needed, 1),
        "cost_now_dollars": round(batt_cost_now, 2),
        "best_window": _window_to_dict(batt_window),
        "saving_dollars": round(batt_saving, 2),
        "saving_pct": round(batt_saving_pct, 1),
    }

    # ── EV analysis ────────────────────────────────────────────────────────────
    ev_needed = hw.ev_needed_kwh(ev_soc_pct, ev_target_pct)
    ev_cost_now = current_price * ev_needed / 100
    ev_window = find_cheapest_window(forecast, ev_needed, hw.ev_charge_kw)
    ev_saving = 0.0
    ev_saving_pct = 0.0
    if ev_window and ev_cost_now > 0:
        ev_saving = ev_cost_now - ev_window.total_cost / 100
        ev_saving_pct = ev_saving / ev_cost_now * 100

    results["ev"] = {
        "soc_pct": ev_soc_pct,
        "target_pct": ev_target_pct,
        "needed_kwh": round(ev_needed, 1),
        "cost_now_dollars": round(ev_cost_now, 2),
        "best_window": _window_to_dict(ev_window),
        "saving_dollars": round(ev_saving, 2),
        "saving_pct": round(ev_saving_pct, 1),
    }

    # ── Export / feed-in opportunity ───────────────────────────────────────────
    # Worth exporting if feed-in rate exceeds the avg forecast price (i.e. it's
    # cheaper to buy back later than sell now at a low rate)
    feedin_opportunity = feedin_price > stats_24h["avg"] and feedin_price > 5.0

    results["feedin"] = {
        "price": round(feedin_price, 2),
        "opportunity": feedin_opportunity,
        "vs_avg_24h": round(feedin_price - stats_24h["avg"], 2),
    }

    results["stats_24h"] = {k: round(v, 2) for k, v in stats_24h.items()}
    results["current_price"] = round(current_price, 2)
    results["current_descriptor"] = current_interval.get("descriptor", "") if current_interval else ""

    return results


def _window_to_dict(w: ChargingWindow | None) -> dict | None:
    if w is None:
        return None
    return {
        "start_time": w.start_time,
        "end_time": w.end_time,
        "total_kwh": round(w.total_kwh, 1),
        "avg_price": round(w.avg_price, 2),
        "total_cost_dollars": round(w.total_cost / 100, 2),
        "descriptor": w.descriptor,
        "num_intervals": len(w.intervals),
    }

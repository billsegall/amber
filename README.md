# Amber

A personal energy monitoring and optimization app for [Amber Electric](https://www.amber.com.au/) wholesale electricity customers.

Amber exposes real-time wholesale electricity prices, forecasts, and usage data. This app uses that to help minimize electricity costs by identifying arbitrage opportunities between cheap and expensive periods — particularly for charging a home battery and electric vehicle.

## Setup

### Prerequisites
- Python 3.10+
- An Amber Electric account with API access

### Installation

```bash
git clone git@github.com:billsegall/amber.git
cd amber
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
python app.py
```

### Configuration (`.env`)

| Variable | Description |
|---|---|
| `AMBER_API_TOKEN` | Your Amber API token (from the Amber app) |
| `AMBER_SITE_ID` | Your site ID (auto-discovered on first run if blank) |
| `FLASK_SECRET_KEY` | Random secret for Flask sessions |
| `FLASK_ENV` | `development` or `production` |
| `FOXESS_API_KEY` | FOX ESS cloud API private token (foxesscloud.com → profile → API Management) |
| `FOXESS_DEVICE_SN` | FOX ESS inverter serial number (auto-discovered if blank) |
| `FRONIUS_IP` | Local IP of Fronius Primo inverter |
| `NTFY_TOPIC` | ntfy.sh topic for push notifications (install ntfy app, subscribe to topic) |
| `SIGNAL_PHONE` | Phone number for Signal alerts via CallMeBot (alternative to ntfy) |
| `SIGNAL_CALLMEBOT_APIKEY` | CallMeBot API key for Signal |
| `ALERT_SPIKE` | Enable spike alerts (default: true) |
| `ALERT_CHEAP` | Enable cheap window alerts (default: true) |
| `ALERT_CHEAP_DESCRIPTOR` | Descriptor threshold for cheap alert (default: extremelyLow) |
| `ALERT_RENEWABLES` | Enable high renewables alerts (default: true) |
| `ALERT_RENEWABLES_PCT` | Renewables % threshold (default: 80) |
| `ALERT_DAILY_SUMMARY` | Enable 7am daily summary (default: true) |
| `DAILY_SUMMARY_HOUR` | Hour for daily summary in NEM time (default: 7) |
| `POLL_INTERVAL_SECONDS` | Background polling interval (default: 300) |

## Default credentials
On first run a default `admin` / `amber` account is created — log in and change the password immediately via the Preferences page.

## Current Features

### Authentication
- Login required for all pages
- Session managed via Flask-Login
- Password change on the Preferences page
- New users prompted to change password on first login

### Dashboard (`/`)
- Live current price — large colour-coded display with descriptor (extremely low → spike)
- Spike and potential-spike banner alerts at top of page
- Live battery SOC and charge/discharge rate (FOX ESS cloud API, auto-refreshed)
- Live solar generation, daily kWh, and grid export/import (Fronius local API — shows "asleep" at night)
- Live feed-in rate (separate Amber channel)
- Renewable energy % for the current interval
- 24h price range (min/avg/max, current percentile)
- **Power Flow panel** — real-time solar / battery / grid / house load in kW
- **7-day usage chart** — daily consumed vs exported kWh (Plotly)

### Arbitrage Analysis
- SOC input form for EV (battery SOC auto-populated from FOX ESS)
- **Battery analysis** — kWh needed to full, cost to charge now vs cheapest upcoming window, $ saving
- **EV analysis** — same for EV (7kW charge rate, configurable target %)
- **Price stats** — 24h min/avg/max, current price percentile
- **Feed-in opportunity** — whether current export rate beats average upcoming buy price

### Price & Renewables Charts
- 48-hour price forecast chart — actual + forecast bars, colour-coded by descriptor
- 48-hour renewable energy % forecast chart

### Alerts (`/alerts`)
- Background polling every 5 minutes via APScheduler
- **Spike alert** — instant notification when spike starts or clears
- **Cheap window alert** — notification when price enters extremelyLow (configurable)
- **High renewables alert** — notification when grid hits 80%+ green (configurable)
- **Battery charging stopped** — notification when battery stops charging before full
- **Daily summary** — 7am: price range, best charging windows, potential savings
- Push notifications via **ntfy.sh** (primary) or Signal/CallMeBot (fallback)
- Alerts page shows live state (spike/cheap/green/charging) and test button
- All alert toggles and thresholds configurable directly on the Alerts page

### Preferences (`/preferences`)
- Hardware config: battery capacity/SOC/charge rate, EV capacity/rate/target SOC
- Location/state (used for renewables data)
- Notification: ntfy.sh topic
- Password change

### JSON API
All data available as JSON for future mobile clients:
- `GET /api/prices/current` — current + 48h forecast prices
- `GET /api/renewables` — renewables forecast
- `GET /api/usage` — 7-day usage by channel
- `GET /api/prices/history` — 7-day historical prices
- `GET /api/sites` — site information

## Proposed Feature Development

### Stage 1 — Data & Dashboard ✓ complete
### Stage 2 — Analysis & Opportunity Detection ✓ complete
### Stage 3 — Notifications & Alerts ✓ complete
### Stage 4 — Control Integration ✓ (monitoring complete; direct control TBD)
- FOX ESS battery: live SOC and power flow ✓
- Fronius Primo solar: live generation and grid flow ✓
- EV charger: OCPP, not yet online (expected ~2 weeks)
- Direct battery charge/discharge commands: pending

### Stage 5 — Automated Optimization
- Automated EV charging scheduler: charge during cheapest forecast window
- Battery charge/discharge optimization: charge cheap, export/hold when expensive
- Configurable constraints: minimum reserve, max charge rate, departure time
- Backtesting: replay historical prices to evaluate strategy savings

### Stage 6 — Mobile Apps
- Android app (React Native or native Kotlin)
- iOS app (React Native or native Swift)
- Reuses the Flask app's REST API backend

### Stage 7 — User Accounts & Configuration ✓ (single user complete)
- User login and authentication ✓
- Per-user configurable settings ✓ (hardware, alerts, notifications)
- Multi-user support: structure in place, admin UI pending

---

## Hardware Context
- **Home battery**: FOX ESS 42kWh (usable: ~37.8kWh, minimum 10% reserve), max charge/discharge 10kW
- **Solar**: Fronius Primo, nominally 5.6kW, max ~25kWh/day generation
- **Electric vehicle**: 100kWh battery, typical charge target 85%, 7kW OCPP charger (not yet online)
- **Location**: QLD, Australia

## License
Private / personal use.

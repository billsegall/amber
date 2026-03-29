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
# Edit .env with your API token and site ID
flask run
```

### Configuration (`.env`)

| Variable | Description |
|---|---|
| `AMBER_API_TOKEN` | Your Amber API token (from the Amber app) |
| `AMBER_SITE_ID` | Your site ID (auto-discovered on first run if blank) |
| `FLASK_SECRET_KEY` | Random secret for Flask sessions |
| `FLASK_ENV` | `development` or `production` |
| `FOXESS_API_KEY` | FOX ESS cloud API key (generate at foxesscloud.com) |
| `FOXESS_DEVICE_SN` | FOX ESS inverter serial number |
| `FRONIUS_IP` | Local IP of Fronius Primo inverter |
| `SIGNAL_PHONE` | Your phone number for Signal alerts (e.g. +61...) |
| `SIGNAL_CALLMEBOT_APIKEY` | CallMeBot API key for Signal |

## Current Features

### Stage 1 — Data & Dashboard ✓
- Live current price display with colour-coded descriptor (extremely low → spike)
- Spike and potential-spike banner alerts
- 48-hour price forecast chart (actual + forecast bars, colour-coded by descriptor)
- 48-hour renewable energy percentage forecast chart
- Cheapest upcoming window highlighted
- JSON API endpoints for all data (ready for future mobile clients)

## Proposed Feature Development

### Stage 1 — Data & Dashboard ✓ complete
- Battery / EV state of charge display (manual entry or live from hardware)
- Historical price viewer (7-day lookback)

### Stage 2 — Analysis & Opportunity Detection
- Identify cheapest windows in the forecast for battery charging
- Identify periods where exporting (feed-in) would be profitable
- Calculate cost of charging EV to target now vs. waiting for cheaper window
- Track daily cost vs. a flat-rate baseline to measure savings
- Arbitrage score: visualize how much money could be saved by optimal scheduling

### Stage 3 — Notifications & Alerts
- Alert when prices spike above a threshold
- Alert when prices drop to "extremelyLow" (good charging window)
- Alert when renewable % is very high (green charging opportunity)
- Daily summary: actual cost vs. baseline, savings achieved
- Signal messenger notifications via CallMeBot (free, personal use)

### Stage 4 — Control Integration
- FOX ESS battery monitoring via FoxCloud API (SOC, charge schedules)
- Fronius Primo solar monitoring via local network API (generation, power flow)
- Integrate with EV charger (make/model TBD)
- Manual charge/discharge commands via the app UI
- Set battery charge/export schedule from the app

### Stage 5 — Automated Optimization
- Automated overnight EV charging scheduler: charges during cheapest forecast window while ensuring car is ready by a user-defined departure time
- Battery charge/discharge optimization: charge during cheap windows, export or hold during expensive windows
- Configurable constraints: minimum battery reserve, maximum charge rate, departure time
- Backtesting: replay historical prices to evaluate what a strategy would have saved

### Stage 6 — Mobile Apps
- Android app (React Native or native Kotlin)
- iOS app (React Native or native Swift)
- Reuses the Flask app's REST API backend

---

## Hardware Context
- **Home battery**: 42kWh (usable: ~37.8kWh, minimum 10% reserve)
- **Electric vehicle**: 100kWh battery, typical charge target 85%

## License
Private / personal use.

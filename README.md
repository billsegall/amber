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

## Current Features

> **Stage 0 — Not yet implemented.** See proposed features below.

## Proposed Feature Development

### Stage 1 — Data & Dashboard
- Connect to Amber API and display current price with descriptor (low/neutral/high/spike)
- Show price forecast chart for the next several hours
- Display renewable energy percentage (current and forecast)
- Show home battery state of charge (manual entry initially)
- Show EV state of charge and charge target (manual entry initially)
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
- Push notifications (email and/or SMS initially; mobile push in Stage 6)

### Stage 4 — Control Integration
- Integrate with home battery system (TBD: Powerwall / Alpha ESS / Sonnen / other)
- Integrate with EV charger (TBD: OCPP / smart charger API)
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

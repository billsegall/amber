# Amber App — Claude Knowledge Base

## Project Overview
Flask/Python web app for monitoring and optimizing electricity usage with Amber (wholesale variable pricing). Focuses on arbitrage opportunities using a home battery and EV charging.

## User's Setup
- **Home battery**: 42kWh capacity, minimum 10% (37.8kWh usable)
- **Electric vehicle**: 100kWh battery, normally charged to 85% target
- **API token env var**: `AMBER_API_TOKEN` (key name: AmberApp)
- **API base URL**: `https://api.amber.com.au/v1`

## Amber API Summary
- Auth: Bearer token in `Authorization` header
- Endpoints:
  - `GET /sites` — list sites, get `siteId`
  - `GET /sites/{siteId}/prices/current` — real-time + forecast prices (up to 2048 intervals, 5 or 30-min resolution)
  - `GET /sites/{siteId}/prices` — historical prices (max 7-day span)
  - `GET /sites/{siteId}/usage` — usage by channel (max 7-day span)
  - `GET /state/{state}/renewables/current` — renewable % forecast (public, no auth)
- Price descriptors: `extremelyLow`, `veryLow`, `low`, `neutral`, `high`, `spike`
- Spike status: `none`, `potential`, `spike`
- Channel types: `general`, `controlledLoad`, `feedIn`
- Prices include `spotPerKwh`, `perKwh` (retail), renewables %, timestamps in NEM + UTC

## Architecture
- **Backend**: Python/Flask
- **Config**: `.env` file (never committed), `.env.example` as template
- **Future**: Android/iOS app option (keep API layer clean and separable)

## Development Stages (see README.md for detail)
1. Data layer & dashboard
2. Analysis & opportunity detection
3. Notifications & alerts
4. Control integration (battery + EV)
5. Automated optimization
6. Mobile apps

## Key Design Decisions
- Keep all Amber API calls in a dedicated `amber_client.py` module
- Store secrets only in `.env` (gitignored)
- `plan.md` is gitignored (working notes)
- Design the REST API layer to be reusable by future mobile clients

## Files
- `app.py` — Flask application entry point
- `amber_client.py` — Amber API wrapper
- `optimizer.py` — Arbitrage/scheduling logic
- `requirements.txt` — Python dependencies
- `.env` — Secrets (gitignored)
- `.env.example` — Template for secrets
- `templates/` — Jinja2 HTML templates
- `static/` — CSS, JS, charts

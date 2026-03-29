import os
import json
from datetime import date, timedelta
from flask import Flask, render_template, jsonify
from dotenv import load_dotenv
from amber_client import get_client, get_site_id

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

DESCRIPTOR_COLORS = {
    "extremelyLow": "#00c853",
    "veryLow":      "#69f0ae",
    "low":          "#b9f6ca",
    "neutral":      "#90a4ae",
    "high":         "#ff6d00",
    "spike":        "#d50000",
}

DESCRIPTOR_LABELS = {
    "extremelyLow": "Extremely Low",
    "veryLow":      "Very Low",
    "low":          "Low",
    "neutral":      "Neutral",
    "high":         "High",
    "spike":        "SPIKE",
}


def _split_intervals(intervals: list[dict]):
    """Split price intervals into past, current, and forecast."""
    past, current, forecast = [], None, []
    for iv in intervals:
        t = iv.get("type", "")
        if t == "ActualInterval":
            past.append(iv)
        elif t == "CurrentInterval":
            current = iv
        elif t == "ForecastInterval":
            forecast.append(iv)
    return past, current, forecast


@app.route("/")
def dashboard():
    try:
        client = get_client()
        site_id = get_site_id(client)
        general   = client.get_current_prices(site_id, next_intervals=96, previous_intervals=24, channel_type="general")
        feedin    = client.get_current_prices(site_id, next_intervals=96, previous_intervals=24, channel_type="feedIn")
        renewables = client.get_renewables(state="QLD", next_intervals=96, previous_intervals=24)
        past, current, forecast = _split_intervals(general)
        _, feedin_current, feedin_forecast = _split_intervals(feedin)
        return render_template(
            "dashboard.html",
            current=current,
            past=past,
            forecast=forecast,
            feedin_current=feedin_current,
            feedin_forecast=feedin_forecast,
            renewables=renewables,
            descriptor_colors=DESCRIPTOR_COLORS,
            descriptor_labels=DESCRIPTOR_LABELS,
        )
    except Exception as e:
        return render_template("error.html", error=str(e)), 500


# ── JSON API (reusable by future mobile clients) ─────────────────────────────

@app.route("/api/prices/current")
def api_current_prices():
    client = get_client()
    site_id = get_site_id(client)
    return jsonify(client.get_current_prices(site_id, next_intervals=96, previous_intervals=24))


@app.route("/api/renewables")
def api_renewables():
    client = get_client()
    return jsonify(client.get_renewables(state="QLD", next_intervals=96, previous_intervals=24))


@app.route("/api/usage")
def api_usage():
    client = get_client()
    site_id = get_site_id(client)
    end = date.today()
    start = end - timedelta(days=6)
    return jsonify(client.get_usage(site_id, start, end))


@app.route("/api/prices/history")
def api_price_history():
    client = get_client()
    site_id = get_site_id(client)
    end = date.today()
    start = end - timedelta(days=6)
    return jsonify(client.get_historical_prices(site_id, start, end))


@app.route("/api/sites")
def api_sites():
    client = get_client()
    return jsonify(client.get_sites())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)

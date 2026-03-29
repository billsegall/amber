"""
Signal notifications via CallMeBot.

Setup:
  1. Add +34 644 52 74 88 to your Signal contacts
  2. Send them: "I allow callmebot to send me messages"
  3. They reply with your apikey
  4. Set SIGNAL_PHONE and SIGNAL_CALLMEBOT_APIKEY in .env
"""
import os
import logging
import urllib.parse
import requests

log = logging.getLogger(__name__)

CALLMEBOT_URL = "https://signal.callmebot.com/signal/send.php"


def send_signal(message: str) -> bool:
    """Send a Signal message via CallMeBot. Returns True on success."""
    phone  = os.environ.get("SIGNAL_PHONE", "").strip()
    apikey = os.environ.get("SIGNAL_CALLMEBOT_APIKEY", "").strip()

    if not phone or not apikey:
        log.warning("Signal not configured (SIGNAL_PHONE / SIGNAL_CALLMEBOT_APIKEY not set)")
        return False

    try:
        r = requests.get(CALLMEBOT_URL, params={
            "phone":  phone,
            "apikey": apikey,
            "text":   message,
        }, timeout=10)
        r.raise_for_status()
        log.info("Signal sent: %s", message[:60])
        return True
    except Exception as e:
        log.error("Signal send failed: %s", e)
        return False


def is_configured() -> bool:
    return bool(os.environ.get("SIGNAL_PHONE") and os.environ.get("SIGNAL_CALLMEBOT_APIKEY"))

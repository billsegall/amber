"""
Push notifications via ntfy.sh (primary) with Signal/CallMeBot as optional fallback.

ntfy setup:
  1. Install the ntfy app on your phone (Android/iOS)
  2. Subscribe to your topic (e.g. amber-bill8)
  3. Set NTFY_TOPIC=amber-bill8 in .env

Signal setup (optional, for future use):
  1. Add +34 644 52 74 88 to Signal contacts
  2. Send "I allow callmebot to send me messages"
  3. Set SIGNAL_PHONE and SIGNAL_CALLMEBOT_APIKEY in .env
"""
import os
import logging
import requests

log = logging.getLogger(__name__)

NTFY_URL = "https://ntfy.sh"


def send_notification(message: str, title: str = "Amber", priority: str = "default") -> bool:
    """Send via ntfy (primary). Returns True on success."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic:
        return _send_ntfy(topic, title, message, priority)

    # Fallback to Signal if ntfy not configured
    return _send_signal(message)


def _send_ntfy(topic: str, title: str, message: str, priority: str = "default") -> bool:
    try:
        r = requests.post(
            f"{NTFY_URL}/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title":    title,
                "Priority": priority,
                "Tags":     "zap",
            },
            timeout=10,
        )
        r.raise_for_status()
        log.info("ntfy sent: %s — %s", title, message[:60])
        return True
    except Exception as e:
        log.error("ntfy send failed: %s", e)
        return False


def _send_signal(message: str) -> bool:
    phone  = os.environ.get("SIGNAL_PHONE", "").strip()
    apikey = os.environ.get("SIGNAL_CALLMEBOT_APIKEY", "").strip()
    if not phone or not apikey:
        log.warning("No notification method configured (set NTFY_TOPIC or SIGNAL_PHONE+SIGNAL_CALLMEBOT_APIKEY)")
        return False
    try:
        r = requests.get(
            "https://signal.callmebot.com/signal/send.php",
            params={"phone": phone, "apikey": apikey, "text": message},
            timeout=10,
        )
        r.raise_for_status()
        log.info("Signal sent: %s", message[:60])
        return True
    except Exception as e:
        log.error("Signal send failed: %s", e)
        return False


def is_configured() -> bool:
    return bool(os.environ.get("NTFY_TOPIC") or
                (os.environ.get("SIGNAL_PHONE") and os.environ.get("SIGNAL_CALLMEBOT_APIKEY")))


def get_method() -> str:
    if os.environ.get("NTFY_TOPIC"):
        return "ntfy"
    if os.environ.get("SIGNAL_PHONE") and os.environ.get("SIGNAL_CALLMEBOT_APIKEY"):
        return "signal"
    return "none"

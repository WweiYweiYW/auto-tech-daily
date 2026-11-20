"""Fail closed before starting the production reminder service; never print secrets."""
import os
import copy
from datetime import date
import json
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daily
from web_digest import valid_entry_url

STATE_PATH = Path("/var/lib/auto-tech-daily/state.json")
SITE_PATH = Path("/var/lib/auto-tech-daily/site")


def mode_bits(path):
    return stat.S_IMODE(path.stat().st_mode)


def valid_state_schema(state):
    """Validate the fields consumed by collect/render, after an in-memory migration."""
    try:
        if not isinstance(state, dict) or type(state.get("version", 1)) is not int:
            return False
        state = daily.migrate_state(copy.deepcopy(state))
        if type(state.get("initialized")) is not bool or not isinstance(state.get("items"), dict):
            return False
        dates = state["last_sent_dates"]
        if not isinstance(dates, dict) or not set(daily.CHANNELS) <= dates.keys():
            return False
        for channel in daily.CHANNELS:
            if dates[channel] is not None:
                date.fromisoformat(dates[channel])
        statuses = {"pending", "sent", "skipped", "discarded"}
        for key, item in state["items"].items():
            if not isinstance(item, dict) or item.get("id") != key:
                return False
            if not all(isinstance(item.get(field), str) for field in
                       ("id", "title", "summary", "source", "link", "category", "seen_at")):
                return False
            if daily.parse_date(item["seen_at"]) is None or item.get("status") not in statuses:
                return False
            if item.get("published") is not None and daily.parse_date(item["published"]) is None:
                return False
            if not isinstance(item.get("keys"), list) or not all(isinstance(k, str) for k in item["keys"]):
                return False
            delivery = item.get("delivery")
            if not isinstance(delivery, dict) or any(delivery.get(c) not in statuses for c in daily.CHANNELS):
                return False
        return True
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def valid_filesystem():
    try:
        if STATE_PATH.is_symlink() or not STATE_PATH.is_file():
            return False
        # The service user must read/write state and replace it in its parent.
        if mode_bits(STATE_PATH) != 0o600 or not os.access(STATE_PATH, os.R_OK | os.W_OK):
            return False
        if not os.access(STATE_PATH.parent, os.W_OK | os.X_OK):
            return False
        if not valid_state_schema(json.loads(STATE_PATH.read_text(encoding="utf-8"))):
            return False
        if SITE_PATH.is_symlink() or not SITE_PATH.is_dir():
            return False
        if mode_bits(SITE_PATH) & 0o005 != 0o005 or not os.access(SITE_PATH, os.W_OK | os.X_OK):
            return False
        # nginx runs as another user; all directory components must be traversable.
        for parent in SITE_PATH.parents:
            if parent.is_symlink() or not parent.is_dir() or not mode_bits(parent) & 0o001:
                return False
        index = SITE_PATH / "index.html"
        return (not index.is_symlink() and index.is_file()
                and bool(mode_bits(index) & 0o004) and os.access(index, os.R_OK))
    except (OSError, ValueError, TypeError):
        return False


def valid_environment(env):
    channel = env.get("WEB_REMINDER_CHANNEL") or "email"
    credentials = (bool(env.get("SMTP_USERNAME") and env.get("SMTP_PASSWORD")) if channel == "email"
                   else bool(env.get("FEISHU_WEBHOOK")) if channel == "feishu" else False)
    return (env.get("DAILY_STATE_PATH") == str(STATE_PATH)
            and env.get("WEB_OUTPUT_DIR") == str(SITE_PATH)
            and valid_entry_url(env.get("WEB_BASE_URL")) and credentials
            and valid_filesystem())


if __name__ == "__main__":
    if not valid_environment(os.environ):
        print("Production precheck failed: check root entry URL, selected credentials, existing valid state (0600), and nginx-readable site/index and parent permissions. No bootstrap performed.")
        sys.exit(1)

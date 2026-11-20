"""Article-only export and atomic static publishing; standard library only."""
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def public_url(value):
    """Allow an HTTP(S) article URL, including ordinary query parameters."""
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096:
        return False
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in ("http", "https") and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and parsed.port != 0)
    except ValueError:
        return False


def valid_entry_url(value):
    """The supplied nginx template serves only the root index, without parameters."""
    if not public_url(value):
        return False
    parsed = urlsplit(value)
    return parsed.path in ("", "/", "/index.html") and "?" not in value and "#" not in value


def iso_date(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
    except (ValueError, TypeError, AttributeError):
        return None


def export_payload(state, now):
    articles = []
    for item in state["items"].values():
        if item.get("status") == "discarded" or not public_url(item.get("link")):
            continue
        seen = iso_date(item.get("seen_at"))
        published = iso_date(item.get("published"))
        if seen is None:
            continue
        stamp = (published or seen).astimezone(SHANGHAI)
        article: dict[str, object] = {key: str(item.get(key, "")) for key in
                                    ("id", "title", "summary", "source", "link", "category")}
        article.update(published=published.isoformat() if published else None,
                       seen_at=seen.isoformat(), date=stamp.date().isoformat())
        articles.append((stamp, article))
    articles.sort(key=lambda pair: (pair[0], pair[1]["seen_at"], pair[1]["id"]), reverse=True)
    return {"updated_at": now.astimezone(SHANGHAI).isoformat(),
            "timezone": "Asia/Shanghai", "articles": [article for _, article in articles]}


def publish(state, now, output_dir, *, payload=None):
    # Import only when publishing: legacy mode and dry runs need no web renderer.
    from web_ui import render_page

    markup = render_page(payload if payload is not None else export_payload(state, now))
    if not isinstance(markup, str) or not markup.strip():
        raise ValueError("empty web page")
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=directory, prefix=".index-", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(markup)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)  # nginx may read only the static output.
        temporary.replace(directory / "index.html")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

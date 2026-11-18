"""Free RSS automotive digest. Python 3.12, standard library only."""
import argparse
import copy
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc
MAX_FEED_BYTES = 5 * 1024 * 1024


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("br", "p", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        else:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    parser = TextParser()
    parser.feed(value or "")
    return " ".join("".join(parser.parts).split())


def valid_url(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def parse_date(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def parse_feed(payload, source):
    root = ET.fromstring(payload)
    if local_name(root.tag) not in ("rss", "RDF", "feed"):
        raise ValueError("not an RSS or Atom feed")
    result = []
    for node in root.iter():
        if local_name(node.tag) not in ("item", "entry"):
            continue
        fields = {}
        link = ""
        for child in node:
            name = local_name(child.tag)
            value = "".join(child.itertext())
            fields.setdefault(name, value)
            if name == "link" and child.get("href") and child.get("rel", "alternate") == "alternate":
                link = child.get("href")
        link = (link or fields.get("link", "")).strip()
        if not valid_url(link):
            continue
        title = plain(fields.get("title", ""))
        summary = plain(fields.get("description") or fields.get("summary") or fields.get("encoded") or fields.get("content", ""))
        if not title:
            continue
        identity = fields.get("guid") or fields.get("id") or link
        keys = [hashlib.sha256(v.encode()).hexdigest() for v in (source + "\n" + identity, link)]
        date = parse_date(fields.get("pubDate") or fields.get("published") or fields.get("updated"))
        result.append({"id": keys[0], "keys": keys, "title": title, "summary": summary[:4000], "link": link, "source": source, "published": date.isoformat() if date else None})
    return result


def contains(text, words):
    for word in words:
        if re.search(r"[a-zA-Z]", word):
            if re.search(r"(?<![a-z0-9])" + re.escape(word.casefold()) + r"(?![a-z0-9])", text.casefold()):
                return True
        elif word in text:
            return True
    return False


def classify(item, feed, config):
    text = item["title"] + " " + item["summary"]
    if contains(text, config["exclude_keywords"]):
        return None
    if feed.get("require_auto_context", True) and not contains(text, config["auto_context"]):
        return None
    if contains(text, config["chassis_keywords"]):
        return "底盘技术"
    if contains(text, config["technology_keywords"]):
        return "汽车新技术"
    return None


def fetch_feed(url):
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "AutoTechDaily/1.0 (RSS reader)", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
            with urlopen(request, timeout=20) as response:
                payload = response.read(MAX_FEED_BYTES + 1)
            if len(payload) > MAX_FEED_BYTES:
                raise ValueError("feed too large")
            return payload
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def new_state():
    return {"version": 1, "initialized": False, "last_sent_date": None, "items": {}}


def load_state(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else new_state()


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def collect(state, config, now, fetch=fetch_feed):
    cutoff = now - timedelta(days=config["retention_days"])
    state["items"] = {k: v for k, v in state["items"].items() if parse_date(v["seen_at"]) >= cutoff}
    known = {key for v in state["items"].values() for key in v["keys"]}
    added = successes = 0
    errors = []
    first = not state["initialized"]
    for feed in config["feeds"]:
        try:
            entries = parse_feed(fetch(feed["url"]), feed["name"])
            successes += 1
        except Exception as exc:
            # Exception messages can contain URLs or credentials; only report the type.
            errors.append(f"{feed['name']}: {type(exc).__name__}")
            continue
        for item in entries:
            category = classify(item, feed, config)
            if not category or known.intersection(item["keys"]):
                continue
            published = parse_date(item["published"])
            # Missing dates are skipped on bootstrap; subsequent discoveries use seen_at.
            if first and (published is None or published < now - timedelta(hours=config["first_run_hours"])):
                continue
            if published and (published < cutoff or published > now + timedelta(hours=24)):
                continue
            item.update(category=category, status="pending", seen_at=now.isoformat())
            state["items"][item["id"]] = item
            known.update(item["keys"])
            added += 1
    if successes:
        state["initialized"] = True
    pending = sorted((v for v in state["items"].values() if v["status"] == "pending"), key=lambda v: v["seen_at"])
    # Keep dedup tombstones when bounding the pending queue.
    for item in pending[:-config["max_pending"]]:
        item["status"] = "discarded"
        item["summary"] = ""
    return added, successes, errors


def due(state, now):
    local = now.astimezone(SHANGHAI)
    return (local.hour, local.minute) >= (8, 37) and state.get("last_sent_date") != local.date().isoformat()


def render(items, day):
    text = [f"汽车技术日报 {day}", "摘要为 RSS 原文摘录，未使用 AI。"]
    markup = [f"<h1>汽车技术日报 {html.escape(day)}</h1><p>摘要为 RSS 原文摘录，未使用 AI。</p>"]
    for category in ("底盘技术", "汽车新技术"):
        group = [item for item in items if item["category"] == category and valid_url(item["link"])]
        if not group:
            continue
        text.append("\n" + category)
        markup.append(f"<h2>{category}</h2>")
        for item in group:
            excerpt = item["summary"][:600]
            text.append(f"{item['title']}（{item['source']}）\n{excerpt}\n{item['link']}")
            markup.append(f'<h3><a href="{html.escape(item["link"], quote=True)}">{html.escape(item["title"])}</a></h3><p>{html.escape(item["source"])}</p><p>{html.escape(excerpt)}</p>')
    return "\n".join(text), "\n".join(markup)


def send_mail(items, day, env):
    message = EmailMessage()
    message["Subject"] = f"汽车技术日报 {day}"
    message["From"] = env["SMTP_USERNAME"]
    message["To"] = env.get("MAIL_TO") or env["SMTP_USERNAME"]
    text, markup = render(items, day)
    message.set_content(text)
    message.add_alternative(markup, subtype="html")
    with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30, context=ssl.create_default_context()) as smtp:
        smtp.login(env["SMTP_USERNAME"], env["SMTP_PASSWORD"])
        refused = smtp.send_message(message)
        if refused:
            raise RuntimeError("recipient refused")


def run(config, state_path, now=None, dry_run=False, force_send=False, env=None, fetch=fetch_feed, sender=send_mail):
    now = now or datetime.now(UTC)
    env = os.environ if env is None else env
    state = copy.deepcopy(load_state(state_path))
    added, successes, errors = collect(state, config, now, fetch)
    pending = sorted((v for v in state["items"].values() if v["status"] == "pending"), key=lambda v: (v["category"], v["seen_at"], v["id"]))
    day = now.astimezone(SHANGHAI).date().isoformat()
    messages = [f"采集新增 {added}；成功源 {successes}/{len(config['feeds'])}；待发 {len(pending)}"]
    messages.extend(f"源失败：{error}" for error in errors)
    code = 0 if successes else 1
    if not successes:
        messages.append("全部 RSS 源失败，本次不发送，请检查网络或源地址。")
    if dry_run:
        messages.append("预览：不发送、不修改状态。")
        messages.append(render(pending, day)[0])
    else:
        try:
            if not env.get("SMTP_USERNAME") or not env.get("SMTP_PASSWORD"):
                messages.append("邮件凭据未配置：仅采集，未发送。")
            elif not pending:
                messages.append("无内容，未发送。")
            elif successes and (force_send or due(state, now)):
                sender(pending, day, env)
                for item in pending:
                    item.update(status="sent", sent_at=now.isoformat())
                state["last_sent_date"] = day
                messages.append(f"已发 {len(pending)} 篇。")
            else:
                messages.append("已采集；未到发送时间或今日已发。")
        except Exception as exc:
            messages.append(f"邮件发送失败（{type(exc).__name__}）；保留 pending。")
            code = 1
        finally:
            save_state(state_path, state)
    return code, messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只读状态并输出文章预览，绝不发信")
    parser.add_argument("--force-send", action="store_true", help="有凭据时强制发送 pending，绕过时间和当日限制")
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    try:
        code, messages = run(config, ROOT / "data/state.json", dry_run=args.dry_run, force_send=args.force_send)
        output = "\n".join(messages)
        print(output)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            # Only operational summary is written; never include article-controlled markup.
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write("\n".join(messages[:1] + [m for m in messages[1:] if m.startswith(("预览：", "邮件凭据", "已发 ", "无内容", "已采集", "邮件发送失败", "全部 RSS"))]) + "\n")
        return code
    except Exception as exc:
        print(f"运行失败（{type(exc).__name__}），请检查配置和状态文件。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

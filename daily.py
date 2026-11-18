"""Free RSS automotive digest. Python 3.12, standard library only."""
import argparse
import base64
import copy
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
import hashlib
import hmac
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
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc
MAX_FEED_BYTES = 5 * 1024 * 1024
CHANNELS = ("email", "feishu")
FEISHU_BATCH = 12
FEISHU_MAX_BYTES = 20_000


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
    return {"version": 2, "initialized": False, "last_sent_dates": dict.fromkeys(CHANNELS), "items": {}}


def migrate_state(state):
    """Migrate in memory only; legacy sent articles never replay to new channels."""
    if state.get("version", 1) == 1:
        state["last_sent_dates"] = {"email": state.pop("last_sent_date", None), "feishu": None}
        for item in state["items"].values():
            status = item["status"]
            if isinstance(item.get("sent_at"), str):
                item["sent_at"] = {"email": item["sent_at"]}
            item["delivery"] = {
                "email": status,
                "feishu": "sent" if status == "sent" else "skipped",
            }
        state["version"] = 2
    if state["version"] != 2:
        raise ValueError("unsupported state version")
    return state


def update_status(item):
    statuses = item["delivery"].values()
    item["status"] = "pending" if "pending" in statuses else "sent" if "sent" in statuses else "skipped"


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


def collect(state, config, now, fetch=fetch_feed, enabled=None):
    enabled = enabled or dict.fromkeys(CHANNELS, False)
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
            item.update(category=category, seen_at=now.isoformat(), delivery={
                channel: "pending" if enabled[channel] else "skipped" for channel in CHANNELS
            })
            update_status(item)
            state["items"][item["id"]] = item
            known.update(item["keys"])
            added += 1
    if successes:
        state["initialized"] = True
    pending = sorted((v for v in state["items"].values() if v["status"] == "pending"), key=lambda v: v["seen_at"])
    # Keep dedup tombstones when bounding the pending queue.
    for item in pending[:-config["max_pending"]]:
        item["status"] = "discarded"
        item["delivery"] = {c: "discarded" if s == "pending" else s for c, s in item["delivery"].items()}
        item["summary"] = ""
    return added, successes, errors


def due(state, now, channel="email"):
    local = now.astimezone(SHANGHAI)
    return (local.hour, local.minute) >= (12, 30) and state["last_sent_dates"].get(channel) != local.date().isoformat()


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


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect rejected")


def valid_feishu_url(value):
    # Exact host, no ports/userinfo/query/fragment, and no normalization surprises.
    return bool(re.fullmatch(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9_-]+", value))


def feishu_signature(timestamp, secret):
    key = f"{timestamp}\n{secret}".encode("utf-8")
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode("ascii")


def feishu_payload(items, day, env, timestamp):
    text = [f"汽车技术日报 {day}", "摘要为 RSS 原文摘录，未使用 AI。"]
    for item in items:
        link = item["link"]
        if not valid_url(link) or len(link.encode("utf-8")) > 600:
            link = "链接无效或超长，完整记录请查看仓库 data/state.json。"
        text.append(f"\n【{item['category']}】{item['title'][:60]}\n{item['source'][:20]}\n{item['summary'][:80]}\n{link}")
    text.append("\n每批最多 12 篇；标题/摘录可能截短，其余待下次日报或手动 send。")
    payload = {"msg_type": "text", "content": {"text": "\n".join(text)}}
    if env.get("FEISHU_SIGN_SECRET"):
        payload.update(timestamp=str(timestamp), sign=feishu_signature(timestamp, env["FEISHU_SIGN_SECRET"]))
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def feishu_batch(items, day, env):
    batch = []
    for item in items[:FEISHU_BATCH]:
        candidate = batch + [item]
        if len(feishu_payload(candidate, day, env, int(time.time()))) >= FEISHU_MAX_BYTES:
            break
        batch = candidate
    if items and not batch:
        raise ValueError("article cannot fit bounded payload")
    return batch


def send_feishu(items, day, env):
    url = env["FEISHU_WEBHOOK"]
    if not valid_feishu_url(url):
        raise ValueError("invalid Feishu webhook")
    payload = feishu_payload(items, day, env, int(time.time()))
    if len(items) > FEISHU_BATCH or len(payload) >= FEISHU_MAX_BYTES:
        raise ValueError("Feishu payload too large")
    request = Request(url, data=payload, headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "AutoTechDaily/1.0"}, method="POST")
    # Never retry an ambiguous delivery automatically; pending is retained for a later run.
    with build_opener(NoRedirect()).open(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("Feishu HTTP failure")
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("Feishu response too large")
    result = json.loads(raw)
    if not isinstance(result, dict) or type(result.get("code")) is not int or result["code"] != 0:
        raise RuntimeError("Feishu business failure")


def run(config, state_path, now=None, dry_run=False, force_send=False, env=None, fetch=fetch_feed, sender=send_mail, feishu_sender=send_feishu):
    now = now or datetime.now(UTC)
    env = os.environ if env is None else env
    state = migrate_state(copy.deepcopy(load_state(state_path)))
    enabled = {"email": bool(env.get("SMTP_USERNAME") and env.get("SMTP_PASSWORD")), "feishu": bool(env.get("FEISHU_WEBHOOK"))}
    added, successes, errors = collect(state, config, now, fetch, enabled)
    pending = sorted((v for v in state["items"].values() if v["status"] == "pending"), key=lambda v: (v["category"], v["seen_at"], v["id"]))
    day = now.astimezone(SHANGHAI).date().isoformat()
    messages = [f"采集新增 {added}；成功源 {successes}/{len(config['feeds'])}；待发 {len(pending)}"]
    messages.extend(f"源失败：{error}" for error in errors)
    code = 0 if successes else 1
    if not successes:
        messages.append("全部 RSS 源失败，本次不发送，请检查网络或源地址。")
    if dry_run:
        messages.append("预览：不发送、不修改状态。")
        preview = sorted((v for v in state["items"].values() if v["status"] == "pending" or v["seen_at"] == now.isoformat()), key=lambda v: v["id"])
        messages.append(render(preview, day)[0])
    else:
        try:
            for channel, send, label in (("email", sender, "邮件"), ("feishu", feishu_sender, "飞书")):
                if not enabled[channel]:
                    partial = (channel == "email" and any(env.get(k) for k in ("SMTP_USERNAME", "SMTP_PASSWORD", "MAIL_TO"))) or (channel == "feishu" and env.get("FEISHU_SIGN_SECRET"))
                    messages.append(f"{label}凭据{'配置不完整（缺少必需项）' if partial else '未配置'}：本渠道未发送。")
                    continue
                queue = [item for item in pending if item["delivery"][channel] == "pending"]
                if not queue:
                    messages.append(f"{label}无内容，未发送。")
                    continue
                if not successes or not (force_send or due(state, now, channel)):
                    messages.append(f"{label}已采集；未到发送时间或今日已发。")
                    continue
                try:
                    batch = feishu_batch(queue, day, env) if channel == "feishu" else queue
                    send(batch, day, env)
                    for item in batch:
                        item["delivery"][channel] = "sent"
                        item.setdefault("sent_at", {})[channel] = now.isoformat()
                        update_status(item)
                    state["last_sent_dates"][channel] = day
                    messages.append(f"{label}已发 {len(batch)} 篇；本渠道剩余待发 {len(queue) - len(batch)} 篇。")
                except Exception as exc:
                    messages.append(f"{label}发送失败（{type(exc).__name__}）；保留本渠道 pending。")
                    code = 1
        finally:
            save_state(state_path, state)
    return code, messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只读状态并输出文章预览，不向任何渠道投递")
    parser.add_argument("--force-send", action="store_true", help="各已配置渠道强制发送其 pending，绕过时间和当日限制")
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    try:
        code, messages = run(config, ROOT / "data/state.json", dry_run=args.dry_run, force_send=args.force_send)
        output = "\n".join(messages)
        print(output)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            # Only operational summary is written; never include article-controlled markup.
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write("\n".join(messages[:1] + [m for m in messages[1:] if m.startswith(("预览：", "邮件", "飞书", "全部 RSS"))]) + "\n")
        return code
    except Exception as exc:
        print(f"运行失败（{type(exc).__name__}），请检查配置和状态文件。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

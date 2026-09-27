"""メール本文（HTML）の組み立てと Gmail SMTP での送信。"""

from __future__ import annotations

import html
import os
import smtplib
from collections import defaultdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

STYLE = """
body{font-family:'Hiragino Sans','Yu Gothic','Meiryo',sans-serif;color:#222;line-height:1.6;max-width:720px;margin:auto}
h2{font-size:17px;border-bottom:2px solid #1a73e8;padding-bottom:2px;margin-top:28px}
.item{margin:12px 0 16px}
.item a{font-size:15px;font-weight:bold;color:#1a0dab;text-decoration:none}
.orig{font-size:12px;color:#777}
.sum{font-size:14px;margin-top:2px}
.meta{font-size:12px;color:#888}
.hl{background:#f1f6fe;border-left:4px solid #1a73e8;padding:8px 14px}
.kw{display:inline-block;background:#fce8e6;color:#c5221f;border-radius:4px;padding:0 6px;font-size:12px}
"""

e = html.escape


def _local(iso: str | None) -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone().strftime("%m/%d %H:%M")


def _item_html(it: dict, extra: str = "") -> str:
    orig = ""
    if it["title_ja"] != it["title"]:
        orig = f'<div class="orig">{e(it["title"])}</div>'
    summary = f'<div class="sum">{e(it["summary_ja"])}</div>' if it.get("summary_ja") else ""
    meta = " · ".join(x for x in (e(it["feed_name"]), _local(it.get("published"))) if x)
    return (
        f'<div class="item">{extra}<a href="{e(it["link"], quote=True)}">{e(it["title_ja"])}</a>'
        f'{orig}{summary}<div class="meta">{meta}</div></div>'
    )


def render_digest(items: list[dict], highlights: list[str]) -> str:
    groups: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        groups[it["category"] or it["feed_name"]].append(it)
    parts = [f"<html><head><style>{STYLE}</style></head><body>"]
    parts.append(f"<p>{len(items)} 件の新着記事（{len(groups)} ソース）</p>")
    if highlights:
        parts.append('<div class="hl"><b>注目</b><ul>' + "".join(f"<li>{e(h)}</li>" for h in highlights) + "</ul></div>")
    for name, its in groups.items():
        parts.append(f"<h2>{e(name)}（{len(its)}）</h2>")
        parts += [_item_html(it) for it in its]
    parts.append("</body></html>")
    return "".join(parts)


def render_alert(hits: list[tuple[dict, str, str]]) -> str:
    """hits は (記事, キーワード名, 一致語) のリスト。"""
    parts = [f"<html><head><style>{STYLE}</style></head><body>"]
    for it, kw, word in hits:
        tag = f'<span class="kw">{e(kw)}</span> ' + (f'<span class="meta">「{e(word)}」に一致</span><br>' if word != kw else "<br>")
        parts.append(_item_html(it, tag))
    parts.append("</body></html>")
    return "".join(parts)


def send(subject: str, html_body: str, to: str) -> None:
    user = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise RuntimeError(".env に GMAIL_ADDRESS と GMAIL_APP_PASSWORD を設定してください")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr(("ニュースダイジェスト", user))
    msg["To"] = to or os.environ.get("MAIL_TO") or user
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, password.replace(" ", ""))
        s.send_message(msg)

"""フィードの取得と、サイト URL からのフィード自動検出。"""

from __future__ import annotations

import calendar
import hashlib
import html
import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

import feedparser
import requests

from .config import Feed

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (compatible; newsbot/0.1; personal RSS reader)"
TIMEOUT = 20


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _published(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc).isoformat()
    return None


def fetch_feed(feed: Feed, max_items: int = 50) -> list[dict]:
    resp = requests.get(feed.url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"フィードとして読めません: {parsed.bozo_exception}")
    items = []
    for e in parsed.entries[:max_items]:
        link = e.get("link") or ""
        title = _clean(e.get("title", ""))
        if not link or not title:
            continue
        key = e.get("id") or link
        items.append({
            "id": hashlib.sha1(f"{feed.url}\n{key}".encode()).hexdigest(),
            "feed_name": feed.name,
            "feed_url": feed.url,
            "category": feed.category,
            "title": title,
            "link": link,
            "summary": _clean(e.get("summary", ""))[:1500],
            "published": _published(e),
        })
    return items


# ---------------------------------------------------------------- 自動検出

class _LinkFinder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.feeds: list[tuple[str, str]] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and "alternate" in (a.get("rel") or "").lower():
            if (a.get("type") or "").lower() in ("application/rss+xml", "application/atom+xml", "application/rdf+xml"):
                self.feeds.append((a.get("href", ""), a.get("title") or ""))
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()


def discover(url: str) -> list[tuple[str, str]]:
    """URL がフィードならそれを、HTML なら <link rel=alternate> のフィードを返す。

    戻り値は (フィード URL, タイトル) のリスト。
    """
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if parsed.entries and parsed.version:
        return [(url, _clean(parsed.feed.get("title", "")))]

    finder = _LinkFinder()
    finder.feed(resp.text)
    found = [(urljoin(resp.url, href), t or finder.title) for href, t in finder.feeds if href]
    if found:
        return found

    # よくある置き場所を試す
    for path in ("/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml"):
        cand = urljoin(resp.url, path)
        try:
            r = requests.get(cand, headers={"User-Agent": UA}, timeout=TIMEOUT)
            p = feedparser.parse(r.content)
            if r.ok and p.entries:
                return [(cand, _clean(p.feed.get("title", "")) or finder.title)]
        except requests.RequestException:
            continue
    return []

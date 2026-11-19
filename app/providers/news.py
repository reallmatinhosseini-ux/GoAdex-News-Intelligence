"""Market news from public RSS feeds (headline, link, time, short summary only — each item links
to the publisher). Official sources are listed first."""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .base import NEWS, Provider, ProviderError

FEEDS = [
    # name, url, kind
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "official"),
    ("European Central Bank", "https://www.ecb.europa.eu/rss/press.html", "official"),
    ("BEA", "https://apps.bea.gov/rss/rss.xml", "official"),
    ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html", "market"),
    ("CNBC Markets", "https://www.cnbc.com/id/10000664/device/rss/rss.html", "market"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse", "market"),
    ("FXStreet", "https://www.fxstreet.com/rss/news", "market"),
    ("investingLive", "https://investinglive.com/feed/", "market"),
    ("Investing.com", "https://www.investing.com/rss/news_1.rss", "market"),
]


def _when(s: str | None):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _clean(s: str | None, n: int = 320) -> str:
    t = re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(s or "")))
    return " ".join(t.split())[:n]


class NewsFeeds(Provider):
    name = "Market news (RSS)"
    category = NEWS
    data_delay = "Polled every 3 minutes"
    docs = "https://www.federalreserve.gov/feeds/feeds.htm"

    def fetch(self, name: str, url: str) -> list[dict]:
        txt = self.get(url, as_json=False, timeout=15)
        root = ET.fromstring(txt.lstrip("﻿").encode("utf-8"))
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        out = []
        for it in items:
            g = lambda tag: (it.findtext(tag) or it.findtext("{http://www.w3.org/2005/Atom}" + tag) or "").strip()
            link = g("link")
            if not link:
                le = it.find("{http://www.w3.org/2005/Atom}link")
                link = le.get("href") if le is not None else ""
            title = _clean(g("title"), 300)
            if not title or not link:
                continue
            out.append({"title": title, "url": link.strip(), "source": name,
                        "published": _when(g("pubDate") or g("published") or g("updated") or
                                           it.findtext("{http://purl.org/dc/elements/1.1/}date")),
                        "summary": _clean(g("description") or g("summary"))})
        return out

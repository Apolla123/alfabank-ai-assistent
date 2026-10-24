import json
import feedparser
import re
import requests
from datetime import datetime
from html import unescape


SOURCES = {
    "Habr": "https://habr.com/ru/rss/news/all/?fl=ru",
    "VC.ru": "https://vc.ru/rss",
    "Tproger": "https://tproger.ru/feed"
}


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


def CleanHtml(raw_html: str) -> str:
    if not raw_html:
        return ""

    CleanText = re.sub(r'<[^>]+>', '', raw_html)
    CleanText = unescape(CleanText)

    return ' '.join(CleanText.split())


def ExtractTagsFromHtml(raw_html: str) -> list:
    if not raw_html:
        return []

    pattern = r'<a\s+[^>]*href=["\'][^"\']*["\'][^>]*>\s*([^<\s]+)'
    matches = re.findall(pattern, raw_html)

    return list(set(matches))


def GetVcTagsFromPage(url: str) -> list:
    if not url:
        return []

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        return ExtractTagsFromHtml(resp.text)

    except requests.exceptions.RequestException:
        return []


def ParseAndSave(output_file: str = "news.json"):
    all_news = []
    for source_name, rss_url in SOURCES.items():
        feed = feedparser.parse(rss_url)

        for entry in feed.entries:
            title = entry.get("title","").strip()
            link = entry.get("link", "").strip()
            raw_description = entry.get("summary") or entry.get("description", "")
            description = CleanHtml(raw_description)

            tags = []

            if "tags" in entry:
                tags = [tag.term for tag in entry.tags if hasattr(tag, "term")]

            if not tags:
                raw_description = (entry.get("summary") or entry.get("description") or "")
                tags = ExtractTagsFromHtml(raw_description)

            if source_name == "VC.ru" and not tags:
                tags = GetVcTagsFromPage(link)

            category_str = ", ".join(sorted(set(tags)))
            date = ""

            if hasattr(entry, "published_parsed") and entry.published_parsed:
                date = datetime(*entry.published_parsed[:6]).strftime("%d.%m.%Y %H:%M")

            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                date = datetime(*entry.updated_parsed[:6]).strftime("%d.%m.%Y %H:%M")

            news_item = {
                "source": source_name,
                "title": title,
                "description": description,
                "category": category_str,
                "date": date,
                "link": link
            }

            all_news.append(news_item)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_news, f, ensure_ascii=False, indent=4)
        f.flush()

import json
import logging
import re
import socket
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import unescape
from urllib.parse import urlparse, parse_qsl, urlencode
import feedparser


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("news")
# таймаут для всех сетевых запросов, включая feedparser
socket.setdefaulttimeout(15)

SOURCES = {"Habr": "https://habr.com/ru/rss/news/all/?fl=ru",
           "VC.ru": "https://vc.ru/rss",
           "NEWSru": "https://rss.newsru.com/finance"} 
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
           "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}

# надёжность источника 0-1
SOURCE_WEIGHTS = {"Habr": 0.9, "VC.ru": 0.7, "NEWSru": 0.7}
# порог похожести заголовков при объединении
THRESHOLD = 0.5
NO_CATEGORY = "Без категории"

# Профиль компании: основы слов в нижнем регистре.
PROFILE = {"industry": ["it", "софт", "программ", "разработ", "облач", "нейросет", "финтех", "платформ", "стартап", "цифров", "кибербезопасн", "данны", "saas", "api"],
           "region": ["росси", "рф", "москв", "санкт-петербург", "спб", "снг"],
           "foreign": ["сша", "китай", "европ", "евросоюз", "индия", "япони", "германи"],
           "financial": ["выручк", "прибыл", "убыт", "капитализац", "инвестиц", "раунд", "сделк", "поглощен", "ipo", "налог", "штраф", "санкци", "бюджет", "рост", "падени", "курс", "рубл", "тариф"],
           "banking": ["банк", "кредит", "займ", "ипотек", "ставк", "цб", "центробанк", "ключев", "платеж", "платёж", "эквайринг", "карт", "перевод", "сбп", "лизинг", "страхов", "долг", "процент"]}
FIT_WEIGHTS = {"industry": 0.35, "region": 0.20, "financial": 0.20, "banking": 0.25}


def CleanHtml(raw) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", "", str(raw or ""))).split())


def ExtractTags(raw) -> set:
    return set(re.findall(r'<a\s+[^>]*href=["\'][^"\']*["\'][^>]*>\s*([^<\s]+)', str(raw or "")))


def Deduplicate(items: list) -> list:
    seen = set()
    result = []
    for it in items:
        key = NormUrl(it["link"])
        title_key = " ".join(Words(it["title"]))
        if key in seen or title_key in seen:
            continue
        seen.add(key)
        seen.add(title_key)
        result.append(it)
    return result


def VcTags(url: str) -> set:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS)) as r:
            return ExtractTags(r.read().decode("utf-8", errors="ignore"))
    except Exception:
        return set()


def NormUrl(url: str) -> str:
    p = urlparse(url)
    query = urlencode([(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_")])
    return f"{p.netloc.lower()}{p.path.rstrip('/')}?{query}"


def Words(text: str) -> list:
    return re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е"))


def GetDate(entry):
    for key in ("published_parsed", "updated_parsed"):
        try:
            if entry.get(key):
                return datetime(*entry[key][:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    # даты нет
    return None


def Fmt(dt) -> str:
    return dt.strftime("%d.%m.%Y %H:%M") if dt else ""


# загрузка
def LoadSource(name: str, url: str) -> list:
    try:
        entries = feedparser.parse(url, request_headers=HEADERS).entries
    except Exception as e:
        log.error("[%s] лента недоступна: %s", name, e)
        return []

    if not entries:
        log.warning("[%s] лента пуста или не загрузилась", name)

    items = []
    for e in entries:
        try:
            title, link = CleanHtml(e.get("title")), str(e.get("link") or "").strip()

            if not title or not link:
                continue

            raw = e.get("summary") or e.get("description") or ""
            tags = {str(t.get("term")) for t in e.get("tags") or [] if t.get("term")} or ExtractTags(raw)

            if name == "VC.ru" and not tags:
                tags = VcTags(link)

            items.append({"source": name, "title": title, "description": CleanHtml(raw) or title, "tags": tags, "dt": GetDate(e), "link": link})
        except Exception as ex:
            log.warning("[%s] пропущена битая запись: %s", name, ex)

    log.info("[%s] новостей: %d", name, len(items))

    return items


# объединение дубликатов и похожих
def Merge(items: list) -> list:
    clusters = []
    for it in items:
        stems = {w[:5] for w in Words(it["title"]) if len(w) > 3}
        text = " ".join(Words(it["title"]))
        for c in clusters:
            common = stems & c["stems"]
            if (len(common) >= 3 and len(common) / len(stems | c["stems"]) >= THRESHOLD) or SequenceMatcher(None, text, c["text"]).ratio() >= 0.8:
                c["items"].append(it)
                break
        else:
            clusters.append({"items": [it], "stems": stems, "text": text})
    return [c["items"] for c in clusters]


def BuildRecord(group: list) -> dict:
    main = max(group, key=lambda x: SOURCE_WEIGHTS.get(x["source"], 0.5))
    links = {NormUrl(g["link"]): {"source": g["source"], "title": g["title"], "link": g["link"], "date": Fmt(g["dt"])} for g in group}
    dt = min((g["dt"] for g in group if g["dt"]), default=None)
    tags = set().union(*(g["tags"] for g in group))

    return {"source": main["source"], "sources": sorted({g["source"] for g in group}),
            "title": main["title"], "description": max((g["description"] for g in group), key=len),
            "category": ", ".join(sorted(tags)) or NO_CATEGORY, "date": Fmt(dt), "link": main["link"],
            "links": list(links.values()),"dt": dt}


# оценки 0-100
def Hits(rec: dict, words: list) -> int:
    title = str(rec.get("title") or "").lower()
    body = f'{rec.get("description") or ""} {rec.get("category") or ""}'.lower()
    return min(100, 20 * sum(2 if w in title else 1 if w in body else 0 for w in words))


def Scores(rec: dict) -> dict:
    s = {k: Hits(rec, PROFILE[k]) for k in ("industry", "financial", "banking")}
    own = Hits(rec, PROFILE["region"])
    s["region"] = 60 + own // 2 if own else (10 if Hits(rec, PROFILE["foreign"]) else 30)

    age_h = max(0, (datetime.now(timezone.utc) - rec["dt"]).total_seconds() / 3600) if rec["dt"] else None
    # полураспад 2 суток
    freshness = round(100 * 0.5 ** (age_h / 48)) if age_h is not None else 30

    weight = max(SOURCE_WEIGHTS.get(x, 0.5) for x in rec["sources"])
    completeness = ((len(rec["description"]) >= 100) + bool(rec["date"]) + (rec["category"] != NO_CATEGORY)) / 3
    confirmation = min(1, (len(rec["sources"]) - 1) / 2)

    return {"profile_fit": round(sum(FIT_WEIGHTS[k] * s[k] for k in FIT_WEIGHTS)), "industry_match": s["industry"],
            "region_match": s["region"], "financial_impact": s["financial"], "banking_relation": s["banking"], "freshness": freshness,
            "source_quality": round(100 * (0.6 * weight + 0.2 * completeness + 0.2 * confirmation))}


# главная функция
def ParseAndSave(output_file: str = "news.json") -> list:
    items = Deduplicate([i for name, url in SOURCES.items() for i in LoadSource(name, url)])
    records = sorted((BuildRecord(g) for g in Merge(items)), key=lambda r: r["dt"].timestamp() if r["dt"] else 0, reverse=True)
    result = []
    for n, r in enumerate(records, 1):
        try:
            scores = Scores(r)
        except Exception as e:
            log.warning("Не удалось оценить '%s': %s", r["title"], e)
            scores = {}

        # Не сохраняем новости без связи с банковской сферой
        if scores["banking_relation"] == 0:
            continue

        result.append({"id": n, **{k: v for k, v in r.items() if k != "dt"}, "scores": scores})

    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=4)
        log.info("Сохранено: %d -> %s", len(result), output_file)
    except Exception as e:
        log.error("Не удалось сохранить %s: %s", output_file, e)


if __name__ == "__main__":
    ParseAndSave() 

import hashlib
import json
import logging
import re
import socket
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qsl, urlencode
import feedparser

log = logging.getLogger("news")

SOURCES = {"Habr": "https://habr.com/ru/rss/news/all/?fl=ru",
           "VC.ru": "https://vc.ru/rss",
           "NEWSru": "https://rss.newsru.com/finance"}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
           "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}

# надёжность источника 0-1
SOURCE_WEIGHTS = {"Habr": 0.9, "VC.ru": 0.7, "NEWSru": 0.7}
# порог пересечения основ слов в заголовках и порог общей похожести строк при объединении
THRESHOLD = 0.5
RATIO = 0.8
NO_CATEGORY = "Без категории"
TRACKING = {"fbclid", "yclid", "gclid", "from"}
EPOCH = datetime.min.replace(tzinfo=timezone.utc)

# Профиль компании: основы слов в нижнем регистре.
# Термины до 3 символов ищутся как целые слова, длиннее - как начало слова.
PROFILE = {"industry": ["золото", "добыча", "драгоценные металлы", "гидроэнергетика", "электроэнергия", "ГЭС", "тариф", "сталь", "металлургия", "чугун", "прокат", "экспорт"],
           "region": ["росси", "рф", "москв", "санкт-петербург", "спб", "снг"],
           "foreign": ["сша", "китай", "европ", "евросоюз", "индия", "япони", "германи"],
           "financial": ["выручк", "прибыл", "убыт", "капитализац", "инвестиц", "раунд", "сделк", "поглощен", "ipo", "налог", "штраф", "санкци", "бюджет", "рост", "падени", "курс", "рубл", "тариф"],
           "banking": ["банк", "кредит", "займ", "ипотек", "ставк", "цб", "центробанк", "ключев", "платеж", "эквайринг", "карт", "перевод", "сбп", "лизинг", "страхов", "долг", "процент"]}
FIT_WEIGHTS = {"industry": 0.35, "region": 0.20, "financial": 0.20, "banking": 0.25}

# Все ключи оценок, чтобы scores никогда не был пустым и обращение по ключу не падало с KeyError
SCORE_KEYS = ("profile_fit", "industry_match", "region_match", "financial_impact", "banking_relation", "freshness", "source_quality")
# news.json всегда лежит рядом с этим файлом, независимо от того, откуда запущен скрипт
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "news.json"


def CleanHtml(raw) -> str:
    # теги заменяются пробелом, чтобы соседние абзацы не слипались
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", str(raw or ""))).split())


def ExtractTags(raw) -> set:
    # текст ссылки целиком, а не только первое слово
    found = re.findall(r'<a\s+[^>]*href=["\'][^"\']*["\'][^>]*>([^<]+)</a>', str(raw or ""))
    return {" ".join(t.split()) for t in found if t.strip()}


def NormUrl(url: str) -> str:
    try:
        p = urlparse(url)
        query = urlencode([(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_") and k not in TRACKING])
        return f"{p.netloc.lower()}{p.path.rstrip('/')}" + (f"?{query}" if query else "")
    except Exception:
        # битая ссылка не должна ронять весь запуск
        return str(url).strip().lower()


def Deduplicate(items: list) -> list:
    # только точные повторы по ссылке; одинаковые заголовки из разных источников
    # объединяет Merge, чтобы сохранить подтверждение несколькими источниками
    seen = set()
    result = []
    for it in items:
        key = NormUrl(it["link"])
        if key not in seen:
            seen.add(key)
            result.append(it)
    return result


def VcTags(url: str) -> set:
    # теги берутся только из мета-тегов статьи, а не со всей страницы
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="ignore")
        found = re.findall(r'<meta[^>]+property=["\']article:tag["\'][^>]+content=["\']([^"\']+)', html)
        return {unescape(t).strip() for t in found if t.strip()}
    except Exception as e:
        log.warning("[VC.ru] теги не получены для %s: %s", url, e)
        return set()


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
    # в локальном часовом поясе машины (внутри dt хранится UTC)
    return dt.astimezone().strftime("%d.%m.%Y %H:%M") if dt else ""


# загрузка
def LoadSource(name: str, url: str) -> list:
    try:
        feed = feedparser.parse(url, request_headers=HEADERS)
    except Exception as e:
        log.error("[%s] лента недоступна: %s", name, e)
        return []

    # feedparser почти не бросает исключений: ошибки лежат в status и bozo
    status = feed.get("status")
    if (status and status >= 400) or (feed.get("bozo") and not feed.entries):
        log.error("[%s] ошибка ленты: status=%s, %s", name, status, feed.get("bozo_exception"))
        return []

    if not feed.entries:
        log.warning("[%s] лента пуста", name)

    items = []
    for e in feed.entries:
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
            if (len(common) >= 3 and len(common) / len(stems | c["stems"]) >= THRESHOLD) or SequenceMatcher(None, text, c["text"]).ratio() >= RATIO:
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
            "links": list(links.values()), "dt": dt}


# оценки 0-100
def Norm(s) -> str:
    return str(s or "").lower().replace("ё", "е")


def Has(text: str, term: str) -> bool:
    # слово должно начинаться с термина; короткие термины (it, api, цб) - только целым словом
    term = Norm(term)
    end = "" if len(term) > 3 else r"(?![a-zа-я0-9])"
    return re.search(rf"(?<![a-zа-я0-9]){re.escape(term)}{end}", text) is not None


def Hits(rec: dict, words: list) -> int:
    title = Norm(rec.get("title"))
    body = Norm(f'{rec.get("description") or ""} {rec.get("category") or ""}')
    return min(100, 20 * sum(2 if Has(title, w) else 1 if Has(body, w) else 0 for w in words))


def Scores(rec: dict, profile: Optional[dict] = None) -> dict:
    # profile - профиль конкретной компании (те же ключи, что в PROFILE); по умолчанию берётся PROFILE
    if profile is None:
        profile = PROFILE
    s = {k: Hits(rec, profile.get(k, [])) for k in ("industry", "financial", "banking")}
    own = Hits(rec, profile.get("region", []))
    s["region"] = min(100, 60 + own // 2) if own else (10 if Hits(rec, profile.get("foreign", [])) else 30)

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
def ParseAndSave(output_file=None, profile: Optional[dict] = None) -> list:
    # таймаут для всех сетевых запросов, включая feedparser
    socket.setdefaulttimeout(15)
    output_file = Path(output_file or DEFAULT_OUTPUT)

    with ThreadPoolExecutor(len(SOURCES)) as ex:
        loaded = list(ex.map(lambda kv: LoadSource(*kv), SOURCES.items()))
    # сортировка по дате делает объединение независимым от порядка загрузки
    items = sorted(Deduplicate([i for part in loaded for i in part]), key=lambda i: i["dt"] or EPOCH)

    records = []
    for g in Merge(items):
        try:
            records.append(BuildRecord(g))
        except Exception as e:
            log.warning("Группа новостей пропущена: %s", e)
    records.sort(key=lambda r: r["dt"].timestamp() if r["dt"] else 0, reverse=True)

    result = []
    for r in records:
        try:
            scores = Scores(r, profile)
        except Exception as e:
            log.warning("Не удалось оценить '%s': %s", r["title"], e)
            scores = {k: 0 for k in SCORE_KEYS}  # все ключи на месте, пустого scores не бывает
        # uid не зависит от порядка и остаётся одним и тем же между запусками
        uid = hashlib.md5(NormUrl(r["link"]).encode("utf-8")).hexdigest()[:10]

        # Не сохраняем новости без связи с банковской сферой
        if scores.get("banking_relation", 0) == 0:
            continue
               
        result.append({"id": len(result) + 1, "uid": uid, **{k: v for k, v in r.items() if k != "dt"}, "scores": scores})

    if not result:
        log.warning("Новостей нет, %s не перезаписан", output_file)
        return result

    try:
        # запись через временный файл: при сбое старый news.json остаётся целым
        tmp = output_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=4), encoding="utf-8")
        tmp.replace(output_file)
        log.info("Сохранено: %d -> %s", len(result), output_file)
    except Exception as e:
        log.error("Не удалось сохранить %s: %s", output_file, e)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ParseAndSave()

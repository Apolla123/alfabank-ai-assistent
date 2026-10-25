from __future__ import annotations
import difflib
import json
import math
import re
import sys
from parser import ParseAndSave
from collections import Counter
from pathlib import Path
from typing import Any


HERE = Path(__file__).parent
# фрагменты, уже подготовленные из PDF
SEED_PATH = HERE / "seed_chunks.json"
# результат build_chunks_from_pdfs (приоритетнее seed)
CHUNKS_PATH = HERE / "chunks.json"
NEWS_PATH = HERE / "news.json"


UNKNOWN = "неизвестно"
_RAW = {"polyus": dict(name="Полюс", industry="Золотодобыча", revenue=UNKNOWN, region=UNKNOWN, employees=UNKNOWN, products=UNKNOWN, aliases=["полюс", "polyus"]),
        "rusgidro": dict(name="РусГидро", industry="Гидроэнергетика", revenue=UNKNOWN, region="Дальневосточный федеральный округ (около 85% выработки электроэнергии в ДФО)", employees=UNKNOWN, products=UNKNOWN, aliases=["русгидро", "rushydro", "rusgidro"]),
        "severstal": dict(name="Северсталь", industry="Производство стали и плоского проката", revenue="2 332 млн USD (прогноз Альфа-Банка на 1К21)", region="Россия", employees=UNKNOWN, products=UNKNOWN, aliases=["северсталь", "severstal", "svst"]),
        "nlmk": dict(name="НЛМК", industry="Производство стали и плоского проката", revenue="2 877 млн USD (прогноз Альфа-Банка на 1К21)", region="Россия", employees=UNKNOWN, products=UNKNOWN, aliases=["нлмк", "nlmk"]),
        "mmk": dict(name="ММК", industry="Производство стали и плоского проката", revenue="2 237 млн USD (прогноз Альфа-Банка на 1К21)", region="Россия", employees=UNKNOWN, products=UNKNOWN, aliases=["ммк", "mmk"])}

def _profile_text(r: dict) -> str:
    parts = [f"Компания {r['name']}: отрасль - {r['industry'].lower()}."]
    if r["region"] != UNKNOWN:
        parts.append(f"Регион: {r['region']}.")
    if r["revenue"] != UNKNOWN:
        parts.append(f"Выручка: {r['revenue']}.")
    if r["employees"] != UNKNOWN:
        parts.append(f"Сотрудников: {r['employees']}.")
    if r["products"] != UNKNOWN:
        parts.append(f"Банковские продукты: {r['products']}.")
    return " ".join(parts)


COMPANIES: dict[str, dict[str, Any]] = {
    cid: {"company_id": cid, "industry": r["industry"], "revenue": r["revenue"], "region": r["region"], "employees": r["employees"], "products": r["products"], "profile_text": _profile_text(r), "_aliases": r["aliases"]}
    for cid, r in _RAW.items()}
_PUBLIC_FIELDS = ("company_id", "industry", "revenue", "region", "employees", "products", "profile_text")


def get_company_profile(company_id: str) -> dict[str, Any]:
    # Профиль компании по id, если id неизвестен - KeyError (чтобы не подсовывать ИИ выдуманные данные)."""
    key = str(company_id).strip().lower()
    if key not in COMPANIES:
        raise KeyError(f"Компания '{company_id}' не найдена. Доступные id: {', '.join(COMPANIES)}")
    c = COMPANIES[key]
    return {f: c[f] for f in _PUBLIC_FIELDS}


def _normalize_company_name(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е")).strip()


def find_company_by_name(query: str) -> str:
    query = _normalize_company_name(query)
    if not query:
        raise ValueError("Название компании пустое")

    query_tokens = set(_company_tokens(query))
    best_id = None
    best_score = 0.0

    for company_id, company in COMPANIES.items():
        variants = [_normalize_company_name(company["company_id"])]
        variants += [_normalize_company_name(a) for a in company["_aliases"]]
        score = 0.0

        for variant in variants:
            variant_tokens = set(_company_tokens(variant))
            if variant in query or query in variant:
                score = max(score, 1.0)

            token_overlap = len(query_tokens & variant_tokens) / max(len(variant_tokens), 1)
            score = max(score, token_overlap)
            ratio = difflib.SequenceMatcher(None, query, variant).ratio()
            score = max(score, ratio)

        if score > best_score:
            best_score = score
            best_id = company_id
    if best_id is None or best_score < 0.45:
        available = ", ".join(sorted(COMPANIES))
        raise ValueError(f"Не удалось определить компанию по запросу '{query}'. Доступные компании: {available}")

    return best_id


def _company_tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zа-я0-9]+", _normalize_company_name(text)) if len(t) > 1]


def get_profile(name_company: str) -> dict[str, Any]:
    company_id = find_company_by_name(name_company)
    return get_company_profile(company_id)


_BOILERPLATE = re.compile(r"центр макроэкономического анализа|все права защищены|генеральная лицензия|"
                          r"аналитика альфа-киб|альфа[\s-]*банк,? 20\d\d г|несанкционированное копирование|"
                          r"контакты\s*(и|/)\s*дисклеймер|^\s*\d{1,2}\s*$", re.I)
_DISCLAIMER = re.compile(r"дисклеймер|не является индивидуальной инвестиционной|"
                         r"предназначен для распространения", re.I)
_CAPTION = re.compile(r"^\s*(рис\.|табл\.|таблица|источник)", re.I)


def _page_label(pages: list[int]) -> int | str:
    if len(pages) == 1:
        return pages[0]
    if pages == list(range(pages[0], pages[-1] + 1)):
        # подряд идущие страницы
        return f"{pages[0]}-{pages[-1]}"
    # разрозненные страницы
    return ", ".join(map(str, pages))


def _assign_ids(chunks: list[dict]) -> list[dict]:
    out = []
    for i, c in enumerate(chunks, start=1):
        pages = sorted(set(c["pages"]))
        out.append({"title": c["title"], "chunk_id": i, "text": c["text"], "page": _page_label(pages), "source": c["source"], "url": c.get("url")})
    return out


def load_chunks() -> list[dict]:
    path = CHUNKS_PATH if CHUNKS_PATH.exists() else SEED_PATH
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw and "chunk_id" in raw[0]:
        return raw
    return _assign_ids(raw)


def _split_paragraphs(page_text: str) -> list[str]:
    lines = [ln.rstrip() for ln in page_text.splitlines()]
    lines = [ln for ln in lines if not _BOILERPLATE.search(ln)]
    paras, cur = [], []
    for ln in lines:
        if not ln.strip():
            if cur:
                paras.append(" ".join(cur))
                cur = []
        else:
            s = ln.strip()
            cur.append(s[:-1] if s.endswith("-") and len(cur) else s)
    if cur:
        paras.append(" ".join(cur))
    return [re.sub(r"\s+", " ", p).strip() for p in paras if len(p.strip()) > 2]


def _is_heading(p: str) -> bool:
    return (len(p) <= 110 and not p.endswith((".", ",", ";")) and p[:1].isupper()
            and not _CAPTION.match(p) and len(p.split()) >= 2)


def _lead_title(p: str) -> str | None:
    m = re.match(r"^([A-ZА-ЯЁ][^:.]{15,180}):\s+\S", p)
    return m.group(1).strip() if m else None


def chunk_pages(pages_text: list[str], source: str, max_chars: int = 2500, min_chars: int = 250) -> list[dict]:
    # Режет текст страниц на смысловые фрагменты. Фрагмент, продолжающийся на следующей странице, склеивается, а его pages содержат все страницы.
    chunks: list[dict] = []
    cur: dict | None = None
    for page_no, text in enumerate(pages_text, start=1):
        # страница-дисклеймер
        if _DISCLAIMER.search(text[:600]) and len(text) < 6000 and "Несанкционированное" in text:
            continue
        for p in _split_paragraphs(text):
            lead = _lead_title(p)
            if lead or (_is_heading(p) and cur is not None and len(cur["text"]) > min_chars):
                cur = {"title": lead or p, "text": p if lead else "", "pages": [page_no], "source": source}
                chunks.append(cur)
            elif _is_heading(p) and cur is None:
                cur = {"title": p, "text": "", "pages": [page_no], "source": source}
                chunks.append(cur)
            else:
                if cur is None:
                    cur = {"title": p[:80], "text": "", "pages": [page_no], "source": source}
                    chunks.append(cur)
                cur["text"] = (cur["text"] + " " + p).strip()
                if page_no not in cur["pages"]:
                    cur["pages"].append(page_no)

    # мелкие хвосты -> к предыдущему
    merged: list[dict] = []
    for c in chunks:
        if merged and len(c["text"]) < min_chars:
            m = merged[-1]
            m["text"] = (m["text"] + " " + c["title"] + ". " + c["text"]).strip()
            m["pages"] = sorted(set(m["pages"] + c["pages"]))
        else:
            merged.append(c)

    # слишком длинные -> режем по предложениям
    final: list[dict] = []
    for c in merged:
        if len(c["text"]) <= max_chars:
            final.append(c)
            continue

        parts = re.split(r"(?<=[.!?])\s+", c["text"])
        buf, n = "", 1
        for s in parts:
            if len(buf) + len(s) > max_chars and buf:
                final.append({**c, "title": f"{c['title']} (ч. {n})", "text": buf.strip()})
                buf, n = "", n + 1
            buf += s + " "
        if buf.strip():
            final.append({**c, "title": f"{c['title']} (ч. {n})" if n > 1 else c["title"], "text": buf.strip()})
    return [c for c in final if c["text"].strip()]


def _read_pdf_pages(pdf_path: Path, ocr_lang: str = "rus+eng") -> list[str]:
    import pdfplumber
    pages: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pg in pdf.pages:
            txt = pg.extract_text() or ""
            if len(txt.strip()) < 30:
                try:
                    import pytesseract
                    img = pg.to_image(resolution=300).original
                    txt = pytesseract.image_to_string(img, lang=ocr_lang)
                except Exception as e:
                    print(f"[warn] OCR недоступен для {pdf_path.name}: {e}", file=sys.stderr)
                    txt = ""
            pages.append(txt)
    return pages


def build_chunks_from_pdfs(pdf_dir: str | Path, out_path: str | Path = CHUNKS_PATH) -> list[dict]:
    raw: list[dict] = []
    for pdf in sorted(Path(pdf_dir).glob("*.pdf")):
        raw += chunk_pages(_read_pdf_pages(pdf), source=pdf.name)
    chunks = _assign_ids(raw)
    Path(out_path).write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    return chunks


_STOP = set("и в во на не что это как а но по из за для от до при или к с со у о об же бы ли "
            "то так также его ее их был была были будет будут есть быть более менее все всё "
            "год года году г руб млн млрд тыс the of and".split())
_TOPIC_HINTS = {"золот": "золото драгметалл ФРС доллар унция ЦБ процентная ставка", "драгмет": "золото драгметалл унция",
                "золотод": "золото драгметалл унция", "гидро": "электроэнергия тариф капзатраты свободный денежный поток ставка ОФЗ",
                "энерг": "электроэнергия тариф капзатраты ставка", "сталь": "сталь прокат экспорт рубль цены металлургия",
                "метал": "сталь прокат экспорт рубль цены", "облиг": "ОФЗ доходность ключевая ставка кредитный спред ЦБ",
                "кредит": "ключевая ставка корпоративное кредитование ОФЗ"}


def _stem(tok: str) -> str:
    return tok[:5] if tok.isalpha() and len(tok) > 5 else tok


def _tokens(text: str) -> list[str]:
    toks = re.findall(r"[а-яёa-z]+|\d+(?:[.,]\d+)?", text.lower().replace("ё", "е"))
    return [_stem(t) for t in toks if t not in _STOP and len(t) > 1]


class _BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.avgdl = sum(map(len, docs)) / max(len(docs), 1)
        self.tf = [Counter(d) for d in docs]
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, q: dict[str, float], i: int) -> tuple[float, list[str]]:
        tf, dl, s, hit = self.tf[i], len(self.docs[i]), 0.0, []
        for t, w in q.items():
            f = tf.get(t, 0)
            if f:
                s += w * self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
                hit.append(t)
        return s, hit


def _news_text(news: Any) -> str:
    if isinstance(news, dict):
        return " ".join(str(news.get(k, "")) for k in ("title", "headline", "text", "body", "content"))
    return str(news or "")


def search_bank_analytics(company_profile: dict, news: Any, top_k: int = 5, chunks: list[dict] | None = None, min_relative_score: float = 0.2, min_matches: int = 2) -> list[dict]:
    chunks = chunks if chunks is not None else load_chunks()
    if not chunks:
        return []

    profile_text = " ".join([str(company_profile.get("industry", "")), str(company_profile.get("region", "")),
                             " ".join(map(str, company_profile.get("products") or [])), str(company_profile.get("profile_text", ""))]).replace(UNKNOWN, " ")
    news_raw = _news_text(news)
    q: dict[str, float] = Counter()

    for t in _tokens(news_raw):
        # новость важнее всего
        q[t] = max(q[t], 1.0)
    for t in _tokens(profile_text):
        # профиль - контекст
        q[t] = max(q[t], 0.3)
    low = (news_raw + " " + profile_text).lower()
    for key, extra in _TOPIC_HINTS.items():
        if key in low:
            for t in _tokens(extra):
                q[t] = max(q[t], 0.4)

    names = {_stem(a.lower().replace("ё", "е")) for a in (COMPANIES.get(str(company_profile.get("company_id", "")).lower(), {}).get("_aliases", []) + []) if a}
    # хотя бы одно совпадение должно быть с НОВОСТЬЮ
    news_terms = set(_tokens(news_raw)) - names
    docs = [_tokens(c["title"] + " " + c["title"] + " " + c["text"]) for c in chunks]
    bm = _BM25(docs)
    scored = []
    for i, c in enumerate(chunks):
        s, hit = bm.score(q, i)
        # упомянута сама компания
        if names & set(docs[i]):
            s *= 1.2
        if len(hit) >= min_matches and s > 0 and len(news_terms & set(hit)) >= 2:
            scored.append((s, i, hit))
    if not scored:
        return []
    scored.sort(reverse=True)
    best = scored[0][0]
    result = []
    for s, i, hit in scored[:top_k]:
        if s < best * min_relative_score:
            break
        c = dict(chunks[i])
        result.append(c)
    return result


def load_news(path: str | Path = NEWS_PATH) -> list[dict]:
    path = Path(path)

    if not path.exists():
        try:
            ParseAndSave(str(path))
        except Exception as e:
            print(f"[warn] Не удалось собрать новости: {e}", file=sys.stderr)
            return []

    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] Не удалось прочитать {path.name}: {e}", file=sys.stderr)
        return []

    return data if isinstance(data, list) else []


def select_company_news(company_profile: dict, news_list: list[dict], top_k: int = 5) -> list[dict]:
    if not news_list:
        return []

    company_id = str(company_profile.get("company_id", "")).lower()
    aliases = COMPANIES.get(company_id, {}).get("_aliases", [])
    aliases = [_normalize_company_name(a) for a in aliases]
    profile_words = set(_tokens(str(company_profile.get("profile_text", ""))))
    industry_words = set(_tokens(str(company_profile.get("industry", ""))))
    region_words = set(_tokens(str(company_profile.get("region", ""))))
    scored = []

    for news in news_list:
        if not isinstance(news, dict):
            continue

        title = str(news.get("title", ""))
        description = str(news.get("description", ""))
        category = str(news.get("category", ""))
        text = f"{title} {description} {category}"
        normalized_text = _normalize_company_name(text)
        news_tokens = set(_tokens(text))

        company_match = 0.0
        for alias in aliases:
            if alias and alias in normalized_text:
                company_match = 1.0
                break

        industry_match = len(industry_words & news_tokens) / max(len(industry_words), 1)
        region_match = len(region_words & news_tokens) / max(len(region_words), 1)
        profile_match = len(profile_words & news_tokens) / max(len(profile_words), 1)

        scores = news.get("scores", {}) if isinstance(news.get("scores", {}), dict) else {}
        relevance = (company_match * 0.45 + industry_match * 0.20 + region_match * 0.10 + profile_match * 0.10 +
                     float(scores.get("profile_fit", 0)) / 100 * 0.05 + float(scores.get("banking_relation", 0)) / 100 * 0.05 +
                     float(scores.get("financial_impact", 0)) / 100 * 0.03 + float(scores.get("freshness", 0)) / 100 * 0.02)

        if company_match == 0 and relevance < 0.08:
            continue

        scored.append((round(relevance, 4), news))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [news for _, news in scored[:top_k]]


def format_for_llm(chunks: list[dict]) -> str:
    # Готовый текстовый блок для промпта ИИ с указанием источника и страниц.
    out = []
    for c in chunks:
        out.append(f"[chunk {c['chunk_id']}] {c['title']}\n Источник: {c['source']}, стр. {c['page']}\n{c['text']}")
    return "\n\n".join(out)


def run_for_company(name_company: str, total_items: int = 5) -> dict:
    profile = get_profile(name_company)

    news_list = load_news()

    all_chunks: list[dict] = []
    for news in news_list:
        found = search_bank_analytics(company_profile=profile, news=news, top_k=total_items)
        all_chunks.extend(found)

    unique_chunks = []
    seen_ids = set()

    for chunk in all_chunks:
        chunk_id = chunk.get("chunk_id")
        if chunk_id not in seen_ids:
            seen_ids.add(chunk_id)
            unique_chunks.append(chunk)

    selected_chunks = unique_chunks[:total_items]
    remaining = max(0, total_items - len(selected_chunks))

    selected_news = []
    if remaining > 0:
        selected_news = select_company_news(profile, news_list, top_k=remaining)

    return {"company_profile": profile, "analytics_chunks": selected_chunks, "selected_news": selected_news,
            "total_items": len(selected_chunks) + len(selected_news), "analytics_text": format_for_llm(selected_chunks)}


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "build":
        res = build_chunks_from_pdfs(sys.argv[2])
        print(f"Готово: {len(res)} фрагментов -> {CHUNKS_PATH}")
    else:
        name = input("Введите название компании: ")
        result = run_for_company(name)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

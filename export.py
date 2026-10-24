"""
export.py - экспорт дайджеста: разобранные новости одной компании в один Markdown-файл.

    from export import build_digest
    text = build_digest(profile, [{"news": {...}, "result": {...ответ /api/analyze...}}, ...])

Выводы аналитики Альфа-Банка и оценка ИИ в дайджесте разделены так же, как в интерфейсе.
"""
import time

BASIS = {"bank_analytics": "Аналитика Альфа-Банка", "ai_estimate": "Оценка ИИ"}


def _items(title, items):
    lines = [f"**{title}**", ""]
    if not items:
        return lines + ["- нет", ""]
    for it in items:
        ids = it.get("chunk_ids") or []
        mark = BASIS.get(it.get("basis"), "Оценка ИИ")
        if ids:
            mark += ", фрагменты: " + ", ".join(map(str, ids))
        lines.append(f"- [{mark}] {it.get('text', '')}")
    return lines + [""]


def _source_line(s):
    if s.get("type") == "news":
        line = f"- Новость: {s.get('title', '')}"
        if s.get("source"):
            line += f" ({s['source']})"
        if s.get("link"):
            line += f" - {s['link']}"
        return line
    line = (f"- Аналитика Альфа-Банка, фрагмент {s.get('chunk_id')}: {s.get('title', '')} "
            f"({s.get('document', '')}, стр. {s.get('page', '?')})")
    if s.get("url"):
        line += f" - {s['url']}"
    return line


def build_digest(profile, entries):
    """profile - профиль компании, entries - список {"news": ..., "result": ...}.
    Новости без успешного анализа пропускаются."""
    profile = profile or {}
    done = [e for e in (entries or []) if (e.get("result") or {}).get("ok")]
    lines = [
        f"# Дайджест новостей: {profile.get('name') or profile.get('company_id', 'компания')}",
        "",
        f"Сформирован: {time.strftime('%d.%m.%Y %H:%M')}. Отрасль: {profile.get('industry', 'неизвестно')}.",
        "",
        "> Выводы помечены: «Аналитика Альфа-Банка» (опираются на материалы банка, указаны фрагменты) "
        "и «Оценка ИИ» (предположение модели, требует проверки). Не является инвестиционной рекомендацией.",
        "",
    ]
    if not done:
        return "\n".join(lines + ["Нет разобранных новостей.", ""])

    for n, e in enumerate(done, 1):
        news, res = e.get("news") or {}, e["result"]
        a = res.get("answer") or {}
        lines += [f"## {n}. {news.get('title', 'Без заголовка')}", ""]
        meta = " · ".join(x for x in (news.get("source"), news.get("date")) if x)
        if meta:
            lines += [f"*{meta}*", ""]
        lines += ["**Что произошло.** " + str(a.get("what_happened", "")), "",
                  "**Почему важно для компании.** " + str(a.get("why_important", "")), ""]
        lines += _items("Риски", a.get("risks"))
        lines += _items("Возможности", a.get("opportunities"))
        lines += ["**Что можно сделать**", ""] + [f"- {x}" for x in a.get("actions") or []] + [""]
        lines += ["**Как это оценивает банк (по аналитике Альфа-Банка).** " + str(a.get("bank_view", "")), "",
                  "**Что неизвестно.** " + str(a.get("uncertainty", "")), ""]
        if res.get("warnings"):
            lines += ["**Проверить вручную:** " + "; ".join(res["warnings"]), ""]
        lines += ["**Источники**", ""] + [_source_line(s) for s in res.get("sources") or []] + [""]
    return "\n".join(lines)

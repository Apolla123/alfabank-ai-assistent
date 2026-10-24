from alfa_rag import find_company_by_name, get_company_profile, search_bank_analytics
from audit_log import check_tool, get_trace, log_event, start_trace
from llm_client import analyze_news, ask_question


# если найдено меньше фрагментов аналитики, агент пробует поискать ещё раз
MIN_CHUNKS = 2   
TOP_K = 5


def _prepare_news(news: dict) -> dict:
    # alfa_rag ищет по полям title/text, а в новостях текст лежит в 'description'. Копируем его в 'text', чтобы поиск учитывал и заголовок, и текст новости.
    news = dict(news)
    if not news.get("text"):
        news["text"] = news.get("description", "")
    return news


def resolve_profile(company: str) -> dict:
    # Принимает id ('polyus') или название ('Полюс', 'полюс') и возвращает профиль."""
    try:
        return get_company_profile(company)
    except KeyError:
        try:
            return get_company_profile(find_company_by_name(company))
        except ValueError as e:
            raise KeyError(str(e))


def _search(profile, news, top_k=TOP_K):
    # Вызов инструмента поиска аналитики с записью в журнал.
    if not check_tool("search_bank_analytics"):
        return []
    found = search_bank_analytics(profile, news, top_k=top_k)
    log_event("tool_call", tool="search_bank_analytics", company=profile.get("company_id"), query=news.get("title", ""), found=len(found), chunk_ids=[c.get("chunk_id") for c in found])
    return found


def _broaden(profile, news):
    # Запасной запрос: заголовок новости + отрасль и продукты компании.
    extra = " ".join(str(profile.get(k, "")) for k in ("industry", "products") if profile.get(k) and profile.get(k) != "неизвестно")
    wide = dict(news)
    wide["title"] = (news.get("title", "") + " " + extra).strip()
    wide["text"] = news.get("title", "")
    return wide


def get_context(company_id: str, news: dict):
    # Возвращает (профиль, новость, найденные куски аналитики).
    check_tool("get_company_profile")
    profile = resolve_profile(company_id)
    log_event("tool_call", tool="get_company_profile", company=profile.get("company_id"))
    news = _prepare_news(news)
    chunks = _search(profile, news)
  
    if len(chunks) < MIN_CHUNKS:
        log_event("decision", note=f"найдено фрагментов: {len(chunks)} (< {MIN_CHUNKS}), повторный поиск с расширенным запросом")
        seen = {c.get("chunk_id") for c in chunks}
        for c in _search(profile, _broaden(profile, news)):
            if c.get("chunk_id") not in seen:
                chunks.append(c)
                seen.add(c.get("chunk_id"))
        chunks = chunks[:TOP_K]
        if not chunks:
            log_event("decision", note="аналитика банка не найдена, все выводы будут оценкой ИИ")
    return profile, news, chunks


def analyze_for_company(company_id: str, news: dict) -> dict:
    # Полный анализ одной новости для одной компании.
    trace_id = start_trace()
    log_event("plan", steps=["профиль компании", "поиск аналитики банка", "повторный поиск, если данных мало", "анализ моделью", "самопроверка и исправление"])
    try:
        profile, news, chunks = get_context(company_id, news)
    except KeyError as e:
        return {"ok": False, "error": str(e), "trace_id": trace_id, "trace": get_trace()}
      
    result = analyze_news(profile, news, chunks)
    result["profile"] = profile
    result["chunks"] = chunks
    result["trace_id"] = trace_id
    result["trace"] = get_trace()
  
    return result


def answer_question(company_id: str, news: dict, question: str) -> dict:
    # Ответ на вопрос пользователя по новости (поле «Задать вопрос»).
    start_trace()
    log_event("plan", steps=["контекст", "ответ на вопрос"], question=question)
  
    try:
        profile, news, chunks = get_context(company_id, news)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
      
    result = ask_question(profile, news, chunks, question)
    result["trace"] = get_trace()
  
    return result

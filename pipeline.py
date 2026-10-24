"""
pipeline.py - склейка: компания + новость -> профиль -> куски аналитики -> анализ LLM.

Использует:
  alfa_rag.get_company_profile / search_bank_analytics  (код Бориса)
  llm_client.analyze_news / ask_question                (код Марины)

Пример:
    from pipeline import analyze_for_company
    result = analyze_for_company("polyus", news)   # news - словарь с title, description, link, source, date
"""
from alfa_rag import find_company_by_name, get_company_profile, search_bank_analytics
from llm_client import analyze_news, ask_question


def _prepare_news(news: dict) -> dict:
    """alfa_rag ищет по полям title/text, а в новостях текст лежит в 'description'.
    Копируем его в 'text', чтобы поиск учитывал и заголовок, и текст новости."""
    news = dict(news)
    if not news.get("text"):
        news["text"] = news.get("description", "")
    return news


def resolve_profile(company: str) -> dict:
    """Принимает id ('polyus') или название ('Полюс', 'полюс') и возвращает профиль."""
    try:
        return get_company_profile(company)
    except KeyError:
        try:
            return get_company_profile(find_company_by_name(company))
        except ValueError as e:
            raise KeyError(str(e))


def get_context(company_id: str, news: dict):
    """Возвращает (профиль, новость, найденные куски аналитики)."""
    profile = resolve_profile(company_id)
    news = _prepare_news(news)
    chunks = search_bank_analytics(profile, news, top_k=5)
    return profile, news, chunks


def analyze_for_company(company_id: str, news: dict) -> dict:
    """Полный анализ одной новости для одной компании.
    Возвращает словарь: ok, answer, sources, warnings, а также profile и chunks (для трассировки)."""
    try:
        profile, news, chunks = get_context(company_id, news)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    result = analyze_news(profile, news, chunks)
    result["profile"] = profile
    result["chunks"] = chunks
    return result


def answer_question(company_id: str, news: dict, question: str) -> dict:
    """Ответ на вопрос пользователя по новости (поле «Задать вопрос»)."""
    try:
        profile, news, chunks = get_context(company_id, news)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    return ask_question(profile, news, chunks, question)

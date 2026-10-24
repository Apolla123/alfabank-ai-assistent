"""
api_routes.py - адреса (эндпоинты) FastAPI для анализа новостей через LLM.

Подключение в main.py (две строки):
    from api_routes import router
    app.include_router(router)

Адреса:
    GET  /api/companies   - список компаний (id, отрасль)
    GET  /api/feed        - лента новостей для компании (?company=polyus&limit=10), без трат на LLM
    POST /api/analyze     - анализ новости для компании
    POST /api/ask         - вопрос по новости

Ключ LLM_API_KEY хранится только на сервере (переменная окружения), в браузер не попадает.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from alfa_rag import COMPANIES, get_company_profile, load_news, select_company_news
from pipeline import analyze_for_company, answer_question, resolve_profile

router = APIRouter(prefix="/api")


class AnalyzeIn(BaseModel):
    company_id: str
    news: dict          # title, description, source, date, link


class AskIn(AnalyzeIn):
    question: str


@router.get("/companies")
def list_companies():
    return [get_company_profile(cid) for cid in COMPANIES]


@router.get("/feed")
def feed(company: str, limit: int = 10):
    """Лента новостей, отобранных под компанию (поиск Бориса, LLM не вызывается)."""
    try:
        profile = resolve_profile(company)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    news = select_company_news(profile, load_news(), top_k=limit)
    return {"ok": True, "profile": profile, "news": news}


@router.post("/analyze")
def analyze(body: AnalyzeIn):
    # обычная def-функция: FastAPI сам запускает её в отдельном потоке, сервер не зависает
    result = analyze_for_company(body.company_id, body.news)
    return result


@router.post("/ask")
def ask(body: AskIn):
    if not body.question.strip():
        return {"ok": False, "error": "Вопрос пустой."}
    return answer_question(body.company_id, body.news, body.question)

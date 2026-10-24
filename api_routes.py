"""
Адреса:
    GET  /api/companies   - список компаний (id, отрасль)
    GET  /api/feed        - лента новостей для компании
    POST /api/analyze     - анализ новости для компании
    POST /api/ask         - вопрос по новости
    POST /api/export      - дайджест разобранных новостей (Markdown-файл)

Ключ LLM_API_KEY хранится только на сервере (переменная окружения), в браузер не попадает.
"""
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from alfa_rag import COMPANIES, get_company_profile, select_company_news
from export import build_digest
from pipeline import analyze_for_company, answer_question, load_news_with_fallback, resolve_profile


router = APIRouter(prefix="/api")


class AnalyzeIn(BaseModel):
    company_id: str
    news: dict          
    

class AskIn(AnalyzeIn):
    question: str


@router.get("/companies")
def list_companies():
    return [get_company_profile(cid) for cid in COMPANIES]


@router.get("/feed")
def feed(company: str, limit: int = 10):
    # Лента новостей, отобранных под компанию.
    try:
        profile = resolve_profile(company)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    news = select_company_news(profile, load_news_with_fallback(), top_k=limit)
    return {"ok": True, "profile": profile, "news": news}


@router.post("/analyze")
def analyze(body: AnalyzeIn):
    result = analyze_for_company(body.company_id, body.news)
    return result


@router.post("/ask")
def ask(body: AskIn):
    if not body.question.strip():
        return {"ok": False, "error": "Вопрос пустой."}
    return answer_question(body.company_id, body.news, body.question)


class ExportIn(BaseModel):
    company_id: str
    items: list         # [{"news": {...}, "result": {...ответ /api/analyze...}}]


@router.post("/export")
def export_digest(body: ExportIn):
    """Дайджест уже разобранных новостей. Модель не вызывается, бюджет не тратится."""
    try:
        profile = resolve_profile(body.company_id)
    except KeyError as e:
        return PlainTextResponse(str(e), status_code=404)
    text = build_digest(profile, body.items)
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="digest_{profile["company_id"]}.md"'})

"""
main.py - запуск сайта: страница + API.

Запуск:  py -m uvicorn main:app --reload      (потом открыть http://127.0.0.1:8000)

ЕСЛИ у команды уже есть свой main.py, этот файл не нужен: достаточно добавить в него
    from api_routes import router
    app.include_router(router)
и положить static/index.html рядом.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api_routes import router

BASE = Path(__file__).parent

app = FastAPI(title="Новостная аналитика для бизнеса")
app.include_router(router)
# PDF-документы банка (если положить их в папку documents/, в источниках появятся ссылки)
if (BASE / "documents").is_dir():
    app.mount("/documents", StaticFiles(directory=BASE / "documents"), name="documents")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/health")
def health():
    return {"ok": True}

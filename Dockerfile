# Сборка:  docker build -t alfa-news .
# Запуск:  docker run -p 8000:8000 -e LLM_API_KEY=sk-... alfa-news
# Ключ передаётся ТОЛЬКО переменной окружения, в образ он не попадает.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Хостинги вроде Render сами задают PORT; по умолчанию 8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]

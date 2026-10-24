"""
llm_client.py - связь с разрешённой LLM, промпт и проверка ответа.

Настройки берутся из переменных окружения (ключ НЕ хранить в коде!):
    LLM_API_KEY  - ключ доступа (ОБЯЗАТЕЛЬНО)
    LLM_API_URL  - базовый адрес API (по умолчанию адрес организаторов; суффикс /chat/completions
                   добавляется сам). Запасной: https://api.alfahack-gateway26.ru/v1
    LLM_MODEL    - название модели (по умолчанию qwen/qwen3.6-35b-a3b)
    LLM_TIMEOUT  - сколько секунд ждать ответ (по умолчанию 60)

Главная функция: analyze_news(company_profile, news, chunks)
Вторая функция:  ask_question(company_profile, news, chunks, question)
"""

import json
import os
import re
import time

import requests

# Разрешённые в кейсе модели (для справки; точные названия для API уточни у организаторов)
ALLOWED_MODELS = [
    "google/gemma-4-26b-a4b-it",
    "z-ai/glm-5.3-flash",
    "qwen/qwen3.6-35b-a3b",
    "deepseek/deepseek-v4.1-flash",
    "qwen/qwen3-vl-30b-a3b-instruct",
    "moonshotai/kimi-k2.6",
]
DEFAULT_URL = "https://changellenge.up.railway.app/v1"
DEFAULT_MODEL = "qwen/qwen3.6-35b-a3b"

RETRIES = 2  # сколько раз повторить запрос при ошибке (после первой попытки)


class LLMError(Exception):
    """Понятная ошибка для показа пользователю."""


# ---------------------------------------------------------------------------
# 1. Отправка запроса к LLM
# ---------------------------------------------------------------------------
def call_llm(messages, temperature=0.2, max_tokens=6000):
    """Отправляет сообщения в LLM и возвращает текст ответа."""
    url = os.getenv("LLM_API_URL", "").strip() or DEFAULT_URL
    key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL
    url = url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    timeout = int(os.getenv("LLM_TIMEOUT", "60"))

    if not key:
        raise LLMError("Не задан ключ: введи команду set LLM_API_KEY=твой_ключ")

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    last_error = "неизвестная ошибка"
    for attempt in range(RETRIES + 1):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.exceptions.Timeout:
            last_error = f"модель не ответила за {timeout} секунд"
        except requests.exceptions.ConnectionError:
            last_error = "нет соединения с сервером модели"
        except requests.exceptions.RequestException as e:
            last_error = f"ошибка запроса: {e}"
        else:
            if resp.status_code == 200:
                try:
                    choice = resp.json()["choices"][0]
                    content = choice["message"]["content"]
                    finish = choice.get("finish_reason")
                except (KeyError, IndexError, ValueError, TypeError):
                    raise LLMError("Модель вернула ответ в неожиданном формате.")
                if content and content.strip():
                    return content
                # пустой ответ: у "думающих" моделей так бывает, если все токены ушли на размышления
                last_error = f"модель вернула пустой ответ (finish_reason={finish})"
            if "budget has been exceeded" in resp.text.lower():
                raise LLMError("Дневной бюджет ключа исчерпан (Budget has been exceeded). "
                               "Лимит сбрасывается в 3:00 по Москве.")
            if resp.status_code in (401, 403):
                raise LLMError("Ключ API не подошёл (ошибка 401/403). Проверь LLM_API_KEY.")
            if resp.status_code == 404:
                raise LLMError("Адрес API не найден (404). Проверь LLM_API_URL и LLM_MODEL.")
            if resp.status_code == 429:
                last_error = "превышен лимит запросов или токенов (429)"
            elif resp.status_code >= 500:
                last_error = f"сервер модели временно недоступен ({resp.status_code})"
            else:
                raise LLMError(f"Ошибка API {resp.status_code}: {resp.text[:200]}")

        if attempt < RETRIES:
            time.sleep(2 * (attempt + 1))  # ждём 2, потом 4 секунды и пробуем снова

    raise LLMError(f"Не удалось получить ответ от модели: {last_error}. "
                   "Попробуй запустить ещё раз или сменить модель (LLM_MODEL).")


# ---------------------------------------------------------------------------
# 2. Промпт
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """Ты аналитический агент для руководителя крупной компании.
Тебе даны: профиль компании, новость и фрагменты аналитики Альфа-Банка.

Правила:
- Не выдумывай факты, цифры, даты и названия. Используй только данные из входа.
- Даты бери ТОЛЬКО из текста новости и текста фрагментов аналитики. Не указывай дату,
  если её нет в тексте новости (поле "Дата" новости можно использовать, если оно заполнено).
  Не используй названия документов и файлов как источник фактов или дат.
- Если данных мало, прямо напиши об этом в поле "uncertainty".
- Не пересказывай новость, а объясни, что она значит именно для ЭТОЙ компании
  (учитывай отрасль, регион, продукты банка, выручку, если они известны).
- Если поле профиля "неизвестно", не делай вид, что оно известно.
- Каждый риск и каждую возможность помечай полем "basis":
    "bank_analytics" - вывод опирается на фрагменты аналитики Альфа-Банка
                       (тогда обязательно укажи их chunk_id в "chunk_ids"),
    "ai_estimate"    - это твоя собственная оценка, предположение (chunk_ids пустой).
- Фрагмент аналитики используй только если он реально относится к новости.
- Если фрагментов нет, все выводы помечай "ai_estimate".
- Отделяй подтверждённое новостью от предположений.

Ответ дай СТРОГО в формате JSON, без текста до и после, без ```:
{
  "what_happened": "кратко, что произошло (только факты из новости)",
  "why_important": "почему это важно именно для этой компании",
  "risks": [{"text": "...", "basis": "bank_analytics|ai_estimate", "chunk_ids": [номера]}],
  "opportunities": [{"text": "...", "basis": "bank_analytics|ai_estimate", "chunk_ids": [номера]}],
  "actions": ["что можно сделать или проверить"],
  "bank_view": "что говорит аналитика банка по теме (или 'Релевантной аналитики банка не найдено')",
  "uncertainty": "чего не хватает или что неясно"
}
"""


def _format_profile(profile):
    return json.dumps(profile, ensure_ascii=False, indent=2)


def _format_news(news):
    return (
        f"Заголовок: {news.get('title', '')}\n"
        f"Источник: {news.get('source', '')}\n"
        f"Дата: {news.get('date') or 'неизвестна'}\n"
        f"Текст: {news.get('description', '')}\n"
        f"Ссылка: {news.get('link', '')}"
    )


def _format_chunks(chunks):
    if not chunks:
        return "(релевантных фрагментов аналитики не найдено)"
    parts = []
    for c in chunks:
        parts.append(
            f"[chunk_id={c.get('chunk_id')}] {c.get('title', '')} "
            f"\n{c.get('text', '')}"
        )
    return "\n\n".join(parts)


def build_messages(profile, news, chunks):
    user = (
        "1. ПРОФИЛЬ КОМПАНИИ:\n" + _format_profile(profile)
        + "\n\n2. НОВОСТЬ:\n" + _format_news(news)
        + "\n\n3. ФРАГМЕНТЫ АНАЛИТИКИ АЛЬФА-БАНКА:\n" + _format_chunks(chunks)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# 3. Разбор и проверка ответа
# ---------------------------------------------------------------------------
REQUIRED_FIELDS = [
    "what_happened", "why_important", "risks", "opportunities",
    "actions", "bank_view", "uncertainty",
]


def parse_json(text):
    """Достаёт JSON из ответа модели (убирает ```json и лишний текст)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            pass
    raise ValueError("ответ не является корректным JSON")


def _numbers(text):
    """Все числа из текста в нормализованном виде (без пробелов, запятая -> точка)."""
    text = str(text).replace("\u00a0", " ")
    text = re.sub(r"(?<=\d)[ ](?=\d{3}\b)", "", text)
    nums = {n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", text)}
    # 12.0 и 12 считаем одним и тем же числом
    return {re.sub(r"\.0+$", "", n) for n in nums}


def validate(answer, profile, news, chunks):
    """
    Проверяет ответ. Возвращает (ошибки, предупреждения).
    Ошибки - ответ нельзя показывать, нужно повторить запрос.
    Предупреждения - показываем ответ, но с пометкой.
    """
    errors, warnings = [], []

    if not isinstance(answer, dict):
        return ["ответ не является объектом JSON"], []

    for f in REQUIRED_FIELDS:
        if f not in answer:
            errors.append(f"нет поля '{f}'")
    if errors:
        return errors, warnings

    valid_ids = {c.get("chunk_id") for c in chunks}

    for group in ("risks", "opportunities"):
        items = answer[group]
        if not isinstance(items, list):
            errors.append(f"'{group}' должен быть списком")
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get("text"):
                errors.append(f"в '{group}' есть пустой или неверный элемент")
                continue
            basis = item.get("basis")
            ids = item.get("chunk_ids") or []
            if basis not in ("bank_analytics", "ai_estimate"):
                errors.append(f"в '{group}' неверное значение basis: {basis}")
            if basis == "bank_analytics" and not ids:
                errors.append(f"в '{group}' вывод 'из аналитики банка' без chunk_id")
            fake = [i for i in ids if i not in valid_ids]
            if fake:
                errors.append(f"в '{group}' выдуманные источники chunk_id: {fake}")

    if errors:
        return errors, warnings

    # Предупреждение: числа в ответе, которых нет во входных данных
    source_text = " ".join(
        [json.dumps(profile, ensure_ascii=False), news.get("title", ""),
         news.get("description", ""), str(news.get("date") or "")]
        + [c.get("text", "") + " " + c.get("title", "") for c in chunks]
    )
    allowed = _numbers(source_text)
    answer_text = " ".join(
        [str(answer["what_happened"]), str(answer["why_important"]), str(answer["bank_view"])]
        + [i.get("text", "") for i in answer["risks"] + answer["opportunities"]]
    )
    unknown = sorted(_numbers(answer_text) - allowed)
    if unknown:
        warnings.append(
            "В ответе есть числа, которых нет во входных данных (проверь вручную): "
            + ", ".join(unknown)
        )

    if not chunks and any(i.get("basis") == "bank_analytics"
                          for i in answer["risks"] + answer["opportunities"]):
        errors.append("ссылка на аналитику банка, хотя фрагментов не было")

    return errors, warnings


def build_sources(news, chunks, answer):
    """Собирает список источников из реальных данных (а не из слов модели)."""
    used_ids = set()
    for item in answer.get("risks", []) + answer.get("opportunities", []):
        used_ids.update(item.get("chunk_ids") or [])

    sources = [{
        "type": "news",
        "title": news.get("title", ""),
        "source": news.get("source", ""),
        "link": news.get("link", ""),
    }]
    for c in chunks:
        if c.get("chunk_id") in used_ids:
            sources.append({
                "type": "bank_analytics",
                "chunk_id": c.get("chunk_id"),
                "title": c.get("title", ""),
                "document": c.get("source", ""),
                "page": c.get("page"),
                "url": c.get("url"),
            })
    return sources


# ---------------------------------------------------------------------------
# 4. Главные функции
# ---------------------------------------------------------------------------
def analyze_news(company_profile, news, chunks, max_attempts=2):
    """
    Возвращает словарь:
      {"ok": True,  "answer": {...}, "sources": [...], "warnings": [...]}
      {"ok": False, "error": "понятный текст ошибки"}
    """
    chunks = chunks or []
    messages = build_messages(company_profile, news, chunks)
    last_problem = ""

    for _ in range(max_attempts):
        try:
            raw = call_llm(messages)
        except LLMError as e:
            return {"ok": False, "error": str(e)}

        try:
            answer = parse_json(raw)
        except ValueError as e:
            last_problem = str(e)
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "Ответ не разобрался как JSON. "
                 "Верни ТОЛЬКО корректный JSON по заданной схеме."},
            ]
            continue

        errors, warnings = validate(answer, company_profile, news, chunks)
        if not errors:
            return {
                "ok": True,
                "answer": answer,
                "sources": build_sources(news, chunks, answer),
                "warnings": warnings,
            }

        last_problem = "; ".join(errors)
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "В ответе ошибки: " + last_problem
             + ". Исправь и верни ТОЛЬКО корректный JSON. Используй только "
               "chunk_id из переданных фрагментов."},
        ]

    return {"ok": False, "error": f"Модель не смогла дать корректный ответ ({last_problem})."}


def ask_question(company_profile, news, chunks, question):
    """Ответ на уточняющий вопрос по новости (обычный текст)."""
    system = (
        "Ты аналитик для руководителя компании. Отвечай на вопрос, опираясь ТОЛЬКО "
        "на профиль компании, новость и фрагменты аналитики банка ниже. "
        "Если ответа в данных нет, так и скажи, не выдумывай. "
        "Отмечай, что из аналитики банка, а что - твоя оценка."
    )
    user = (
        "ПРОФИЛЬ:\n" + _format_profile(company_profile)
        + "\n\nНОВОСТЬ:\n" + _format_news(news)
        + "\n\nАНАЛИТИКА БАНКА:\n" + _format_chunks(chunks or [])
        + "\n\nВОПРОС: " + question
    )
    try:
        return {"ok": True, "text": call_llm(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=800,
        )}
    except LLMError as e:
        return {"ok": False, "error": str(e)}

"""
audit_log.py - журнал действий ИИ-части (трассируемость и безопасность).

Что пишем (в файл logs/audit.jsonl, одна строка = одно событие, формат JSON):
  - plan         план шагов агента
  - tool_call    обращение к инструменту (поиск аналитики, профиль компании)
  - llm_call     запрос к модели (какая модель, сколько длился, чем закончился)
  - self_check   результат самопроверки ответа
  - decision     решения агента (например, "мало фрагментов - повторный поиск")

Чего НЕ пишем: ключ API (он маскируется), полные тексты запросов и ответов
(только размер), чтобы в журнал не попадали чувствительные данные.

Каждый запуск анализа получает trace_id; события запуска также возвращаются в
результате (поле "trace"), чтобы интерфейс мог показать «как ИИ пришёл к выводу».
"""
import contextvars
import json
import os
import re
import threading
import time
import uuid

LOG_PATH = os.getenv("AUDIT_LOG_PATH", "logs/audit.jsonl")

# Инструменты, которые разрешено вызывать агенту. Все - только чтение.
# Записи, удаления, запуска команд и произвольных запросов в интернет у агента нет.
ALLOWED_TOOLS = {"get_company_profile", "search_bank_analytics", "call_llm"}

_trace = contextvars.ContextVar("audit_trace", default=None)
_lock = threading.Lock()


def mask(value):
    """Прячет ключи вида sk-... и точное значение LLM_API_KEY."""
    text = str(value)
    key = os.getenv("LLM_API_KEY", "").strip()
    if key:
        text = text.replace(key, "***")
    return re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "sk-***", text)


def _clean(value, limit=300):
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_clean(v, limit) for v in value[:20]]
    if isinstance(value, dict):
        return {str(k): _clean(v, limit) for k, v in list(value.items())[:20]}
    text = mask(value)
    return text if len(text) <= limit else text[:limit] + "..."


def start_trace():
    """Начинает новую трассу (один запуск анализа). Возвращает trace_id."""
    trace = {"id": uuid.uuid4().hex[:8], "events": []}
    _trace.set(trace)
    return trace["id"]


def get_trace():
    """События текущего запуска (копия) - для показа в интерфейсе."""
    trace = _trace.get()
    return list(trace["events"]) if trace else []


def log_event(kind, **data):
    """Записывает событие в журнал и в текущую трассу."""
    trace = _trace.get()
    event = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "trace_id": trace["id"] if trace else None,
        "kind": kind,
    }
    event.update({k: _clean(v) for k, v in data.items()})
    if trace is not None:
        trace["events"].append(event)
    try:
        folder = os.path.dirname(LOG_PATH)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with _lock, open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass  # журнал не должен ломать работу сервиса (например, диск только для чтения)
    return event


def check_tool(name):
    """Разрешён ли инструмент. Если нет - записываем отказ и возвращаем False."""
    if name in ALLOWED_TOOLS:
        return True
    log_event("tool_denied", tool=name)
    return False

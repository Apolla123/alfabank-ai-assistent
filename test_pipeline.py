import json
import sys
from pipeline import analyze_for_company


company = sys.argv[1] if len(sys.argv) > 1 else "polyus"
news = {"title": "ЦБ сохранил ключевую ставку на уровне 14%",
        "description": "Банк России оставил ключевую ставку без изменений на уровне 14% годовых и заявил, что дальнейшие решения будут зависеть от инфляции.",
        "source": "Тестовая новость", "date": "11.09.2026", "link": ""}
print(f"Компания: {company}\nНовость: {news['title']}\nОтправляю в модель, подожди до минуты...\n")
result = analyze_for_company(company, news)

if result["ok"]:
    print("Найдено кусков аналитики:", len(result["chunks"]))
    print(json.dumps(result["answer"], ensure_ascii=False, indent=2))
    print("\nИСТОЧНИКИ:")
    print(json.dumps(result["sources"], ensure_ascii=False, indent=2))
    if result["warnings"]:
        print("\nПРЕДУПРЕЖДЕНИЯ:", result["warnings"])
    print("\nШАГИ АГЕНТА (журнал также в logs/audit.jsonl):")
    for e in result.get("trace", []):
        print(" -", e["kind"], {k: v for k, v in e.items() if k not in ("time", "trace_id", "kind")})
else:
    print("ОШИБКА:", result["error"])

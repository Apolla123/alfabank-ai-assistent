"""Тесты экспорта дайджеста, тестовых данных и ссылок на документы. Без интернета и ключа."""
import json
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("AUDIT_LOG_PATH", os.path.join(tempfile.gettempdir(), "audit_test.jsonl"))
if "feedparser" not in sys.modules:
    try:
        import feedparser  # noqa: F401
    except ImportError:                       # для проверки без установки библиотек
        sys.modules["feedparser"] = types.ModuleType("feedparser")

from export import build_digest  # noqa: E402

class TestDataTests(unittest.TestCase):
    def test_test_news_file_is_valid(self):
        with open(os.path.join(ROOT, "data", "test_news.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertGreaterEqual(len(data), 5)
        self.assertEqual(len({n["id"] for n in data}), len(data))
        for n in data:
            self.assertTrue(n["title"] and "Синтетические" in n["source"])


class ExportTests(unittest.TestCase):
    def test_digest_separates_bank_and_ai_and_skips_failed(self):
        ok = {"ok": True, "answer": {
            "what_happened": "w", "why_important": "y",
            "risks": [{"text": "риск1", "basis": "bank_analytics", "chunk_ids": [3]},
                      {"text": "риск2", "basis": "ai_estimate", "chunk_ids": []}],
            "opportunities": [], "actions": ["a"], "bank_view": "bv", "uncertainty": "u"},
            "sources": [{"type": "news", "title": "T", "source": "S", "link": "http://x"},
                        {"type": "bank_analytics", "chunk_id": 3, "title": "Ф", "document": "d.pdf", "page": 2, "url": None}],
            "warnings": []}
        text = build_digest({"company_id": "polyus", "industry": "золото"},
                            [{"news": {"title": "Новость"}, "result": ok},
                             {"news": {"title": "Упала"}, "result": {"ok": False, "error": "e"}}])
        self.assertIn("Аналитика Альфа-Банка, фрагменты: 3", text)
        self.assertIn("[Оценка ИИ] риск2", text)
        self.assertIn("d.pdf", text)
        self.assertNotIn("Упала", text)

    def test_empty_digest(self):
        self.assertIn("Нет разобранных новостей", build_digest({"company_id": "x"}, []))


if __name__ == "__main__":
    unittest.main()

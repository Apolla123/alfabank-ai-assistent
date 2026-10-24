# Запуск из корня проекта: py -m unittest discover -s tests -v
import os
import sys
import tempfile
import unittest
import audit_log
import llm_client as lc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AUDIT_LOG_PATH"] = os.path.join(tempfile.gettempdir(), "audit_test.jsonl")

PROFILE = {"company_id": "polyus", "industry": "золото", "revenue": "неизвестно"}
NEWS = {"title": "ЦБ сохранил ставку 14%", "description": "Ставка 14% годовых.",
        "date": "11.09.2026", "source": "t", "link": ""}
CHUNKS = [{"chunk_id": 1, "title": "Ставка 14,0%", "text": "Прогноз 12,0% к 2027",
           "source": "doc.pdf", "page": 1, "url": None}]


def good_answer():
    return {"what_happened": "ЦБ сохранил ставку 14%", "why_important": "дорогой кредит",
            "risks": [{"text": "долг дорожает", "basis": "bank_analytics", "chunk_ids": [1]}],
            "opportunities": [{"text": "золото", "basis": "ai_estimate", "chunk_ids": []}],
            "actions": ["проверить долг"], "bank_view": "ставка 14,0%", "uncertainty": "нет данных"}


class ParseTests(unittest.TestCase):
    def test_plain_and_fenced_json(self):
        self.assertEqual(lc.parse_json('{"a": 1}'), {"a": 1})
        self.assertEqual(lc.parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(lc.parse_json('текст {"a": 1} текст'), {"a": 1})

    def test_bad_json(self):
        with self.assertRaises(ValueError):
            lc.parse_json("не json")


class ValidateTests(unittest.TestCase):
    def test_good_answer_passes(self):
        errors, warnings = lc.validate(good_answer(), PROFILE, NEWS, CHUNKS)
        self.assertEqual((errors, warnings), ([], []))

    def test_missing_field(self):
        a = good_answer()
        del a["actions"]
        errors, _ = lc.validate(a, PROFILE, NEWS, CHUNKS)
        self.assertTrue(errors)

    def test_fake_chunk_id(self):
        a = good_answer()
        a["risks"][0]["chunk_ids"] = [99]
        errors, _ = lc.validate(a, PROFILE, NEWS, CHUNKS)
        self.assertTrue(any("99" in e for e in errors))

    def test_bank_view_without_chunk(self):
        a = good_answer()
        a["risks"][0]["chunk_ids"] = []
        errors, _ = lc.validate(a, PROFILE, NEWS, CHUNKS)
        self.assertTrue(errors)

    def test_unknown_number_warns(self):
        a = good_answer()
        a["why_important"] = "рост на 37%"
        _, warnings = lc.validate(a, PROFILE, NEWS, CHUNKS)
        self.assertTrue(warnings)

    def test_news_date_and_comma_numbers_allowed(self):
        a = good_answer()
        a["what_happened"] = "11.09.2026 ставка 14% и прогноз 12%"
        _, warnings = lc.validate(a, PROFILE, NEWS, CHUNKS)
        self.assertEqual(warnings, [])


class SourcesTests(unittest.TestCase):
    def test_sources_only_from_real_chunks(self):
        sources = lc.build_sources(NEWS, CHUNKS, good_answer())
        self.assertEqual([s["type"] for s in sources], ["news", "bank_analytics"])
        self.assertEqual(sources[1]["chunk_id"], 1)


class AnalyzeFlowTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._orig = lc.call_llm

    def tearDown(self):
        lc.call_llm = self._orig

    def test_retry_after_bad_json(self):
        import json
        answers = iter(["мусор", json.dumps(good_answer(), ensure_ascii=False)])
        lc.call_llm = lambda messages, **kw: next(answers)
        result = lc.analyze_news(PROFILE, NEWS, CHUNKS)
        self.assertTrue(result["ok"])

    def test_llm_error_is_reported(self):
        def boom(messages, **kw):
            raise lc.LLMError("бюджет")
        lc.call_llm = boom
        result = lc.analyze_news(PROFILE, NEWS, CHUNKS)
        self.assertFalse(result["ok"])
        self.assertIn("бюджет", result["error"])

    def test_self_revision_used_when_better(self):
        import json
        first = good_answer()
        first["why_important"] = "рост на 37%"
        answers = iter([json.dumps(first, ensure_ascii=False),
                        json.dumps(good_answer(), ensure_ascii=False)])
        lc.call_llm = lambda messages, **kw: next(answers)
        result = lc.analyze_news(PROFILE, NEWS, CHUNKS)
        self.assertTrue(result["ok"])
        self.assertEqual(result["warnings"], [])


class AuditTests(unittest.TestCase):
    def test_key_is_masked(self):
        os.environ["LLM_API_KEY"] = "sk-SECRET123456"
        self.assertNotIn("SECRET", audit_log.mask("ключ sk-SECRET123456 и sk-ABCDEF123456"))
        audit_log.start_trace()
        audit_log.log_event("test", text="sk-SECRET123456")
        self.assertNotIn("SECRET", str(audit_log.get_trace()))

    def test_unknown_tool_denied(self):
        audit_log.start_trace()
        self.assertFalse(audit_log.check_tool("delete_files"))
        self.assertTrue(audit_log.check_tool("search_bank_analytics"))


if __name__ == "__main__":
    unittest.main()

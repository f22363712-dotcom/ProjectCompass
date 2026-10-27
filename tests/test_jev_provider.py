from datetime import datetime, timezone
import unittest

from project_compass.jev_provider import JevDecisionProvider
from project_compass.models import CandidateFact, EvidenceEvent, EvidenceRelationMethod, FactKind, FactStatus


def fact():
    return CandidateFact(
        "fact-1",
        FactKind.TASK,
        "task.demo",
        "Build demo",
        FactStatus.PROPOSED,
        ("e1",),
        "alice",
        datetime(2026, 10, 24, tzinfo=timezone.utc),
    )


def evidence():
    return EvidenceEvent(
        "e2",
        "session-1",
        "alice",
        "test_result",
        "7 tests passed",
        datetime(2026, 10, 24, tzinfo=timezone.utc),
        {"tool": "unittest"},
    )


class JevProviderTest(unittest.TestCase):
    def test_sends_bounded_noul_question_and_returns_probability(self):
        requests = []
        usage = []

        def transport(payload, headers, timeout):
            requests.append((payload, headers, timeout))
            return {"model": "jev-1.13.0", "answers": {"supports": {"type": "noul", "noul": 0.82}}, "usage": {"input_tokens": 100, "output_tokens": 12, "total_tokens": 112}}

        provider = JevDecisionProvider("secret", transport=transport, usage_logger=usage.append)
        suggestion = provider.suggest(fact(), evidence())

        self.assertIsNotNone(suggestion)
        self.assertIs(suggestion.method, EvidenceRelationMethod.JEV)
        self.assertEqual(suggestion.score, 0.82)
        self.assertEqual(requests[0][0]["questions"]["supports"]["type"], "noul")
        self.assertEqual(requests[0][1]["Authorization"], "Bearer secret")
        self.assertEqual(usage[0]["input_tokens"], 100)
        self.assertEqual(usage[0]["total_tokens"], 112)
        self.assertTrue(usage[0]["success"])
        self.assertNotIn("secret", str(usage[0]))

    def test_missing_key_and_transport_failure_return_none_without_raising(self):
        calls = []
        usage = []

        def transport(*args):
            calls.append(args)
            raise TimeoutError("timed out")

        self.assertIsNone(JevDecisionProvider(None, transport=transport, usage_logger=usage.append).suggest(fact(), evidence()))
        self.assertEqual(calls, [])

        self.assertIsNone(JevDecisionProvider("secret", transport=transport, usage_logger=usage.append).suggest(fact(), evidence()))
        self.assertEqual(usage[-1]["success"], False)
        self.assertNotIn("secret", str(usage[-1]))

    def test_malformed_response_and_out_of_range_probability_fall_back(self):
        for response in (None, [], {"answers": {"supports": []}}, {"answers": {"supports": {"noul": 1.5}}}):
            usage = []

            def transport(*args, response=response):
                return response

            suggestion = JevDecisionProvider("secret", transport=transport, usage_logger=usage.append).suggest(fact(), evidence())

            self.assertIsNone(suggestion)
            self.assertEqual(usage[-1]["success"], False)

    def test_unexpected_transport_exception_is_redacted_and_falls_back(self):
        usage = []

        def transport(*args):
            raise RuntimeError("Bearer secret")

        suggestion = JevDecisionProvider("secret", transport=transport, usage_logger=usage.append).suggest(fact(), evidence())

        self.assertIsNone(suggestion)
        self.assertEqual(usage[-1]["success"], False)
        self.assertNotIn("secret", str(usage[-1]))

    def test_usage_logger_failure_does_not_break_jev_or_fallback(self):
        def broken_logger(entry):
            raise OSError("log unavailable")

        def transport(payload, headers, timeout):
            return {"model": "jev-1.13.0", "answers": {"supports": {"noul": 0.8}}, "usage": {}}

        suggestion = JevDecisionProvider("secret", transport=transport, usage_logger=broken_logger).suggest(fact(), evidence())

        self.assertEqual(suggestion.score, 0.8)


if __name__ == "__main__":
    unittest.main()

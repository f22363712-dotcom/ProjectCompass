from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from project_compass.codex_extractor import CodexExtractor
from project_compass.sample_data import sample_events


class CodexExtractorTest(unittest.TestCase):
    def source(self):
        return replace(sample_events()[0], id="source", kind="assistant_message", content="Verify the final candidate on the phone.", metadata={"role": "assistant"})

    def runner(self, calls, *, tool=False, bad_quote=False, warning=False):
        def run(args, **kwargs):
            calls.append((args, kwargs))
            self.assertNotIn(self.source().content, " ".join(args))
            self.assertIn(self.source().content, kwargs["input"])
            result = {"candidates": [{"source_id": "source", "kind": "task", "subject_key": "phone-check",
                                      "quote": "Invented completion" if bad_quote else self.source().content, "task_state": "pending"}]}
            Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(result), encoding="utf-8")
            rows = [{"type": "turn.completed", "usage": {"input_tokens": 120, "output_tokens": 30, "cached_input_tokens": 40}}]
            if tool:
                rows.append({"type": "item.completed", "item": {"type": "command_execution", "command": "ignored"}})
            if warning:
                rows.append({"type": "item.completed", "item": {"type": "error", "message": "Nonfatal protocol warning"}})
            return subprocess.CompletedProcess(args, 0, "\n".join(map(json.dumps, rows)), "")
        return run

    def test_structured_batch_logs_actual_usage_and_reuses_cache(self):
        calls, usage = [], []
        with tempfile.TemporaryDirectory() as directory:
            provider = CodexExtractor(command="codex-test", runner=self.runner(calls), usage_logger=usage.append, member_id="developer-a")
            first = provider.extract([self.source()], Path(directory))
            second = provider.extract([self.source()], Path(directory))
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(usage), 1)
            self.assertEqual(usage[0]["total_tokens"], 150)
            self.assertEqual(usage[0]["cached_input_tokens"], 40)
            self.assertTrue(usage[0]["cache_hit"])
            self.assertTrue(usage[0]["success"])
            self.assertEqual(usage[0]["model"], "gpt-6.1-sol")
            self.assertEqual(first[0].metadata["member_id"], "developer-a")
            self.assertTrue(provider.last_result["cache_reused"])

    def test_tool_activity_or_invalid_quote_is_rejected_without_cache(self):
        for config in ({"tool": True}, {"bad_quote": True}):
            calls, usage = [], []
            with self.subTest(config=config), tempfile.TemporaryDirectory() as directory:
                provider = CodexExtractor(command="codex-test", runner=self.runner(calls, **config), usage_logger=usage.append)
                with self.assertRaises(ValueError):
                    provider.extract([self.source()], Path(directory))
                self.assertFalse(list(Path(directory).glob("*.json")))
                self.assertFalse(usage[0]["success"])
                self.assertEqual(usage[0]["total_tokens"], 150)
                if config.get('bad_quote'):
                    rejected = list((Path(directory) / 'rejected').glob('*.json'))
                    self.assertTrue(rejected, 'rejected final response lost; retry would need another model call')

    def test_timeout_logs_unknown_usage_and_does_not_retry(self):
        usage, calls = [], []
        def fail(*args, **kwargs):
            calls.append(1)
            raise subprocess.TimeoutExpired("codex", 120)
        with tempfile.TemporaryDirectory() as directory:
            provider = CodexExtractor(command="codex-test", runner=fail, usage_logger=usage.append)
            with self.assertRaises(ValueError):
                provider.extract([self.source()], Path(directory))
        self.assertEqual(len(calls), 1)
        self.assertIsNone(usage[0]["total_tokens"])
        self.assertFalse(usage[0]["success"])

    def test_non_tool_error_item_is_recorded_as_warning_with_valid_final_json(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = CodexExtractor(command="codex-test", runner=self.runner([], warning=True))
            result = provider.extract([self.source()], Path(directory))
            self.assertEqual(len(result), 1)
            self.assertEqual(provider.last_result["protocol_warnings"], 1)


if __name__ == "__main__":
    unittest.main()

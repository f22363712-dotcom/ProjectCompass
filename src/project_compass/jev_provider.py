from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from .evidence_linking import RelationSuggestion
from .models import CandidateFact, EvidenceEvent, EvidenceRelationMethod


Transport = Callable[[dict[str, Any], dict[str, str], float], dict[str, Any]]
UsageLogger = Callable[[dict[str, Any]], None]


def _http_transport(endpoint: str) -> Transport:
    def send(payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    return send


class JevDecisionProvider:
    """Optional TypeSafe Jev adapter for one bounded evidence-support question."""

    def __init__(
        self,
        api_key: str | None,
        *,
        endpoint: str = "https://api.typesafe.ai/v1/systemone",
        timeout: float = 8.0,
        transport: Transport | None = None,
        usage_logger: UsageLogger | None = None,
    ) -> None:
        self.api_key = api_key
        self.endpoint = endpoint
        self.timeout = timeout
        self.transport = transport or _http_transport(endpoint)
        self.usage_logger = usage_logger

    def suggest(self, fact: CandidateFact, evidence: EvidenceEvent) -> RelationSuggestion | None:
        if not self.api_key:
            return None
        payload = {
            "model": "jev-latest",
            "state": {
                "fact": {"id": fact.id, "kind": fact.kind.value, "subject_key": fact.subject_key, "value": fact.value},
                "evidence": {"id": evidence.id, "kind": evidence.kind, "author": evidence.author, "content": evidence.content},
            },
            "questions": {
                "supports": {
                    "type": "noul",
                    "instructions": "Does this evidence support the stated fact?",
                    "criteria": {
                        "true": "The evidence directly supports the fact without requiring an unstated assumption.",
                        "false": "The evidence does not support the fact or is unrelated.",
                    },
                }
            },
        }
        started = time.perf_counter()
        try:
            response = self.transport(
                payload,
                {"Authorization": f"Bearer {self.api_key}"},
                self.timeout,
            )
            if not isinstance(response, dict):
                raise ValueError("invalid response shape")
            answers = response.get("answers", {})
            if not isinstance(answers, dict):
                raise ValueError("invalid answers shape")
            answer = answers.get("supports", {})
            if not isinstance(answer, dict):
                raise ValueError("invalid support answer shape")
            score = float(answer["noul"])
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise ValueError("support probability out of range")
            self._log_usage(response, started, success=True)
            return RelationSuggestion(EvidenceRelationMethod.JEV, score, "jev-noul-support")
        except Exception:
            self._log_usage({}, started, success=False)
            return None

    def _log_usage(self, response: dict[str, Any], started: float, *, success: bool) -> None:
        if self.usage_logger is None:
            return
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        if not isinstance(usage, dict):
            usage = {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total_tokens = usage.get("total_tokens")
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        try:
            self.usage_logger({
                "model": response.get("model", "jev-latest") if isinstance(response, dict) else "jev-latest",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cache_hit": usage.get("cache_hit"),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "success": success,
            })
        except Exception:
            # Usage logging must never turn an optional model into a hard dependency.
            return

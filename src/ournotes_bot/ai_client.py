"""Small, bounded client for one structured AI request."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen


class AIClientError(Exception):
    """A provider response could not be safely used."""


@dataclass(frozen=True)
class ModelResponse:
    data: dict[str, Any]
    usage: dict[str, int]


class AIClient:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def request(self, system_prompt: str, query: str, *, timeout: float = 12.0,
                max_tokens: int = 180) -> ModelResponse:
        if not self.base_url.startswith("https://"):
            raise AIClientError("AI API must use HTTPS")
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
        }).encode("utf-8")
        request = Request(self.base_url + "/chat/completions", data=body, headers={
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        })
        try:
            with urlopen(request, timeout=max(0.1, timeout)) as response:
                raw = response.read(20_001)
            if len(raw) > 20_000:
                raise AIClientError("AI response too large")
            payload = json.loads(raw)
            result = json.loads(payload["choices"][0]["message"]["content"])
            if not isinstance(result, dict):
                raise AIClientError("AI response is not an object")
            usage = self._usage(payload.get("usage"))
            return ModelResponse(result, usage)
        except AIClientError:
            raise
        except (OSError, ValueError, KeyError, TypeError, IndexError,
                json.JSONDecodeError) as exc:
            raise AIClientError("AI provider request failed") from exc

    @staticmethod
    def _usage(raw: object) -> dict[str, int]:
        if not isinstance(raw, dict):
            return {}
        aliases = {
            "prompt_tokens": "prompt_tokens",
            "completion_tokens": "completion_tokens",
            "total_tokens": "total_tokens",
            "prompt_cache_hit_tokens": "cache_hit_tokens",
            "prompt_cache_miss_tokens": "cache_miss_tokens",
        }
        return {
            target: value for source, target in aliases.items()
            if isinstance((value := raw.get(source)), int)
            and not isinstance(value, bool) and value >= 0
        }

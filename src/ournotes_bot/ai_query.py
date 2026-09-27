"""Backward-compatible facade for the bounded natural-language query agent."""

from __future__ import annotations

from .ai_client import AIClient, ModelResponse
from .ai_quota import DailyQuota
from .commands import CommandResult
from .config import Settings
from .data import SongRepository
from .local_query import (
    local_card_rarity_question as _local_card_rarity_question,
    local_entity_question as _local_entity_question,
    local_skill_question as _local_skill_question,
    local_song_filter as _local_song_filter,
)
from .query_agent import AgentOutcome, AgentState, QueryAgent, is_ai_request, query_text
from .query_capabilities import GLOBAL_RULES
from .query_metrics import QueryMetrics
from .query_validation import AMBIGUOUS_ENTITY, UNKNOWN_ENTITY, UNSUPPORTED, OutcomeCode
from .structured_query import QueryResult, QuerySpec


# Kept as a public compatibility name. Actual calls use router or capability prompts.
SYSTEM_PROMPT = GLOBAL_RULES
_query_text = query_text


class AIQueryParser:
    """Thin facade retained for main.py, qq.py and existing integrations."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._quota = DailyQuota(settings.ai_quota_file or settings.cache_file.with_name("ai-quota.json"))
        self._metrics = QueryMetrics(
            settings.ai_metrics_file or settings.cache_file.with_name("ai-metrics.json")
        )
        self._client = AIClient(settings.ai_base_url, settings.ai_api_key, settings.ai_model)
        self._agent = QueryAgent(
            ai_enabled=bool(settings.ai_api_key),
            daily_limit=settings.ai_daily_limit,
            quota=self._quota,
            metrics=self._metrics,
            requester=lambda question, prompt, timeout: self._request(
                question, prompt, timeout,
            ),
        )

    def command_for(self, content: str) -> str | None:
        return self._agent.command_for(content)

    def spec_for(self, content: str) -> QuerySpec | None:
        return self._agent.spec_for(content)

    def outcome_code_for(self, content: str) -> OutcomeCode | None:
        return self._agent.outcome_code_for(content)

    def state_for(self, content: str) -> AgentState | None:
        return self._agent.state_for(content)

    def answer(self, content: str, repository: SongRepository) -> str:
        return self.answer_with_outcome(content, repository).text

    def answer_with_plan(self, content: str, repository: SongRepository) -> tuple[str, QueryResult | CommandResult | None]:
        outcome = self.answer_with_outcome(content, repository)
        return outcome.text, outcome.result

    def answer_with_outcome(self, content: str, repository: SongRepository) -> AgentOutcome:
        return self._agent.run(content, repository)

    def _request(self, query: str, system_prompt: str = SYSTEM_PROMPT,
                 timeout: float = 12.0) -> ModelResponse:
        return self._client.request(system_prompt, query, timeout=timeout)

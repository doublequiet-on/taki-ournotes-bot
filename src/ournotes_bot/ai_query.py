"""Compatibility facade for bounded natural-language queries."""

from __future__ import annotations

import re
from collections import OrderedDict
from threading import RLock

from .ai_client import AIClient, AIClientError, ModelResponse
from .ai_quota import DailyQuota, QuotaUnavailable
from .commands import CommandResult, handle_command, resolve_command
from .config import Settings
from .data import SongRepository
from .entity_lexicon import alias_version, find_anchor, mentions_outside_catalog
from .local_query import (
    local_card_rarity_question as _local_card_rarity_question,
    local_entity_question as _local_entity_question,
    local_skill_question as _local_skill_question,
    local_song_filter as _local_song_filter,
    parse_local_query,
)
from .query_metrics import QueryMetrics
from .query_capabilities import (
    CAPABILITIES,
    GLOBAL_RULES,
    capability_for_spec,
    local_route,
    route_prompt,
)
from .query_validation import (
    AMBIGUOUS_ENTITY,
    UNKNOWN_ENTITY,
    UNSUPPORTED,
    OutcomeCode,
    message_for,
    validate_capability_action,
    validate_route,
)
from .structured_query import QueryResult, QuerySpec, answer_for


SYSTEM_PROMPT = GLOBAL_RULES

_DIRECT_COMMAND = re.compile(
    r"^[/／]?(?:查曲|查谱面|查谱|查卡|查支援卡|支援卡|song|chart|card|support)\s+", re.I,
)
_UNSUPPORTED_TERMS = re.compile(
    r"推荐|最强|排行|攻略|预测|档线|代练|代肝|账号|抽卡建议|编成|怎么打|如何打"
)


def is_ai_request(content: str) -> bool:
    cleaned = re.sub(r"<@!?\w+>", "", content).strip()
    return bool(re.match(r"^[/／]?(?:问|ask)(?:\s|$)", cleaned, re.I))


def _query_text(content: str) -> str:
    cleaned = re.sub(r"<@!?\w+>", "", content).strip()
    return re.sub(r"^[/／]?(?:问|ask)\s*", "", cleaned, flags=re.I).strip()


class AIQueryParser:
    """Keep the public API stable while delegating parsing responsibilities."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._quota = DailyQuota(settings.ai_quota_file or settings.cache_file.with_name("ai-quota.json"))
        self._metrics = QueryMetrics(
            settings.ai_metrics_file or settings.cache_file.with_name("ai-metrics.json")
        )
        self._client = AIClient(settings.ai_base_url, settings.ai_api_key, settings.ai_model)
        self._lock = RLock()
        self._cache: OrderedDict[str, QuerySpec | str] = OrderedDict()
        self._outcomes: OrderedDict[str, OutcomeCode] = OrderedDict()
        self._cache_source: tuple[object, int, int, int, int, int | None] | None = None

    @staticmethod
    def _source(repository: SongRepository) -> tuple[object, int, int, int, int, int | None]:
        return (
            repository.metadata.get("cached_at"), len(repository.songs), len(repository.cards),
            len(repository.support_cards), int(repository.member_skill_index_ready()), alias_version(),
        )

    def _sync_cache(self, repository: SongRepository) -> None:
        source = self._source(repository)
        with self._lock:
            if source != self._cache_source:
                self._cache.clear()
                self._outcomes.clear()
                self._cache_source = source

    def _remember(self, query: str, plan: QuerySpec | str) -> None:
        with self._lock:
            self._cache[query] = plan
            self._cache.move_to_end(query)
            if len(self._cache) > 128:
                self._cache.popitem(last=False)

    def _remember_outcome(self, query: str, code: OutcomeCode) -> None:
        with self._lock:
            self._outcomes[query] = code
            self._outcomes.move_to_end(query)
            if len(self._outcomes) > 128:
                self._outcomes.popitem(last=False)

    def _cached(self, query: str) -> QuerySpec | str | None:
        with self._lock:
            plan = self._cache.get(query)
            if plan is not None:
                self._cache.move_to_end(query)
            return plan

    def command_for(self, content: str) -> str | None:
        if not is_ai_request(content):
            return None
        plan = self._cached(_query_text(content))
        return plan.command_label() if isinstance(plan, QuerySpec) else plan

    def spec_for(self, content: str) -> QuerySpec | None:
        plan = self._cached(_query_text(content)) if is_ai_request(content) else None
        return plan if isinstance(plan, QuerySpec) else None

    def outcome_code_for(self, content: str) -> OutcomeCode | None:
        if not is_ai_request(content):
            return None
        with self._lock:
            return self._outcomes.get(_query_text(content))

    def answer(self, content: str, repository: SongRepository) -> str:
        return self.answer_with_plan(content, repository)[0]

    def answer_with_plan(self, content: str, repository: SongRepository) -> tuple[str, QueryResult | CommandResult | None]:
        """Return request-owned records; cache locking never covers model I/O."""
        self._sync_cache(repository)
        query = _query_text(content)
        tracked = is_ai_request(content)
        if tracked:
            self._metrics.increment("ask_total")
        if not query or len(query) > 100:
            return self._terminal(
                query, OutcomeCode.INVALID_ARGUMENTS,
                "用法：/问 <想查的歌曲、谱面、成员卡、技能或支援卡>，最多 100 字。",
                tracked, local=True,
            )
        if mentions_outside_catalog(query):
            return self._terminal(query, OutcomeCode.UNKNOWN_ENTITY, UNKNOWN_ENTITY, tracked, local=True)
        if _UNSUPPORTED_TERMS.search(query):
            return self._terminal(query, OutcomeCode.UNSUPPORTED, UNSUPPORTED, tracked, local=True)
        if _DIRECT_COMMAND.match(query):
            selected = resolve_command(query, repository)
            self._remember(query, query)
            self._remember_outcome(query, OutcomeCode.SUCCESS)
            if tracked:
                self._metrics.increment("local_success")
            return handle_command(query, repository, resolved=selected) or UNSUPPORTED, selected

        local = parse_local_query(query, repository)
        if isinstance(local, str):
            code = self._message_code(local)
            return self._terminal(query, code, local, tracked, local=True)
        if local is not None:
            answer, result, code = self._execute(local, repository)
            self._remember(query, local)
            self._remember_outcome(query, code)
            if tracked:
                self._metrics.increment("local_success")
            return answer, result

        if not self.settings.ai_api_key:
            return self._terminal(
                query, OutcomeCode.NOT_CONFIGURED,
                "自然语言查询尚未配置 AI；请用 /查曲、/查谱面、/查卡 或 /查支援卡。",
                tracked,
            )

        cached = self._cached(query)
        if cached is not None:
            self._metrics.increment("cache_hit_success")
            if isinstance(cached, QuerySpec):
                answer, result, code = self._execute(cached, repository)
                self._remember_outcome(query, code)
                return answer, result
            selected = resolve_command(cached, repository)
            self._remember_outcome(query, OutcomeCode.SUCCESS)
            return handle_command(cached, repository, resolved=selected) or UNSUPPORTED, selected

        capability_id = local_route(query)
        prefetched = None
        if capability_id is None:
            data, error = self._model_data(
                query, route_prompt(), "ai_route_requested", tracked,
            )
            if error:
                return self._terminal(query, error[0], error[1], tracked)
            assert data is not None
            routed = validate_route(data)
            if routed.capability_id is None:
                self._record_ai_terminal(routed.code, tracked)
                return self._terminal(query, routed.code, message_for(routed.code), tracked)
            capability_id = routed.capability_id
            prefetched = routed.prefetched

        capability = CAPABILITIES[capability_id]
        if prefetched is None:
            anchor = find_anchor(
                capability.intent, query, repository,
                explicit_card_ids=capability.intent == "card" and "技能" in query,
            )
            evidence = (() if anchor.entity is None else
                        (f"{anchor.entity.kind}:{anchor.entity.value}",))
            data, error = self._model_data(
                query, capability.prompt(evidence=evidence), "ai_parse_requested", tracked,
            )
            if error:
                return self._terminal(query, error[0], error[1], tracked)
            assert data is not None
        else:
            data = prefetched

        validated = validate_capability_action(data, capability, query, repository)
        if validated.spec is None:
            self._record_ai_terminal(validated.code, tracked)
            return self._terminal(query, validated.code, message_for(validated.code), tracked)
        answer, result, code = self._execute(validated.spec, repository)
        self._remember(query, validated.spec)
        self._remember_outcome(query, code)
        if tracked:
            metric = {
                OutcomeCode.EMPTY: "ai_empty",
                OutcomeCode.DATA_UNAVAILABLE: "ai_data_unavailable",
            }.get(code, "ai_success")
            self._metrics.increment(metric)
        return answer, result

    def _terminal(self, query: str, code: OutcomeCode, message: str, tracked: bool,
                  *, local: bool = False) -> tuple[str, None]:
        self._remember_outcome(query, code)
        if tracked and local:
            self._metrics.increment("local_terminal_reject")
        return message, None

    def _model_data(self, query: str, prompt: str, metric: str,
                    tracked: bool) -> tuple[dict | None, tuple[OutcomeCode, str] | None]:
        try:
            available = self._quota.reserve(self.settings.ai_daily_limit)
        except QuotaUnavailable:
            return None, (
                OutcomeCode.QUOTA_UNAVAILABLE,
                "AI 额度记录不可用；仍可使用 /查曲、/查谱面、/查卡、/查支援卡及常见技能问法。请联系管理员检查额度文件。",
            )
        if not available:
            if tracked:
                self._metrics.increment("quota_exhausted")
            return None, (
                OutcomeCode.QUOTA_EXHAUSTED,
                "今日自然语言查询额度已用完；仍可使用 /查曲、/查谱面、/查卡、/查支援卡及常见技能问法。",
            )
        if tracked:
            self._metrics.increment(metric)
        try:
            response = self._request(query, prompt, 12.0)
        except (AIClientError, OSError, ValueError, KeyError, TypeError, IndexError):
            if tracked:
                self._metrics.increment("ai_provider_error")
            return None, (
                OutcomeCode.TEMPORARY_FAILURE,
                "自然语言解析暂不可用；请直接使用 /查曲、/查谱面、/查卡 或 /查支援卡。",
            )
        try:
            return self._response_data(response), None
        except TypeError:
            if tracked:
                self._metrics.increment("ai_invalid_output")
            return None, (OutcomeCode.INVALID_OUTPUT, UNSUPPORTED)

    def _record_ai_terminal(self, code: OutcomeCode, tracked: bool) -> None:
        if not tracked:
            return
        metric = {
            OutcomeCode.UNSUPPORTED: "ai_unsupported",
            OutcomeCode.UNKNOWN_ENTITY: "ai_unknown_entity",
            OutcomeCode.AMBIGUOUS: "ai_ambiguous",
        }.get(code, "ai_invalid_output")
        self._metrics.increment(metric)

    @staticmethod
    def _message_code(message: str) -> OutcomeCode:
        if message == UNKNOWN_ENTITY:
            return OutcomeCode.UNKNOWN_ENTITY
        if message == AMBIGUOUS_ENTITY:
            return OutcomeCode.AMBIGUOUS
        return OutcomeCode.INVALID_ARGUMENTS

    @staticmethod
    def _result_code(result: QueryResult, repository: SongRepository) -> OutcomeCode:
        spec = result.spec
        if ((spec.skill_query or spec.skill_kind) and spec.intent == "card"
                and not repository.member_skill_index_ready()):
            return OutcomeCode.DATA_UNAVAILABLE
        if spec.intent == "song" and not result.songs:
            return OutcomeCode.EMPTY
        if spec.intent == "card" and not result.cards:
            return OutcomeCode.EMPTY
        if spec.intent == "support_card" and not result.support_cards:
            return OutcomeCode.EMPTY
        if spec.intent == "chart" and result.chart is None:
            return OutcomeCode.EMPTY
        return OutcomeCode.SUCCESS

    def _execute(self, spec: QuerySpec, repository: SongRepository) -> tuple[str, QueryResult, OutcomeCode]:
        result = capability_for_spec(spec).executor(spec, repository)
        return answer_for(spec, repository, result=result), result, self._result_code(result, repository)

    def _request(self, query: str, system_prompt: str = SYSTEM_PROMPT,
                 timeout: float = 12.0) -> ModelResponse:
        return self._client.request(system_prompt, query, timeout=timeout)

    def _response_data(self, response: ModelResponse | dict) -> dict:
        if isinstance(response, ModelResponse):
            if response.usage:
                self._metrics.increment_many({"ai_" + key: value for key, value in response.usage.items()})
            return response.data
        if not isinstance(response, dict):
            raise TypeError("AI response is not an object")
        return response

# L3
# Input: 用户问题、SongRepository、能力目录、请求器、额度／指标对象及时间策略。
# Output: run 返回 AgentOutcome，包含文本、QueryResult／CommandResult／TerminalOutcome 载荷及 AgentState。
# Pos: Query / Natural 的有界查询编排与终态处理；见 L2-2.md。
# Effects/Dependencies: 维护内存计划／终态缓存与状态，持久预占额度、写匿名指标及进程内计数；受控调用 AI，本地路径也可经别名与 Data 发生文件／网络 I/O。

"""Bounded action/observation state machine for natural-language queries."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from time import monotonic
from typing import Callable

from ..ai_client import AIClientError, AIInvalidResponse, ModelResponse
from .ai_quota import DailyQuota, QuotaUnavailable
from ..commands import PAGE_SIZE, CommandResult, handle_command, resolve_command
from ..data import SongRepository
from ..query.entity_lexicon import alias_version, find_anchor, mentions_outside_catalog
from .local_query import is_explicit_empty_subject_query, parse_local_query
from .query_capabilities import CAPABILITIES, Capability, capability_for_spec, local_route, route_prompt
from .query_debug import QUERY_DEBUG_COUNTERS
from .query_metrics import QueryMetrics
from .query_validation import (
    AMBIGUOUS_ENTITY,
    UNKNOWN_ENTITY,
    UNSUPPORTED,
    OutcomeCode,
    ValidationResult,
    message_for,
    validate_capability_action,
    validate_route,
)
from ..structured_query import QueryResult, QuerySpec, answer_for


MAX_MODEL_CALLS = 3
MAX_TOOL_EXECUTIONS = 2
MAX_REPAIRS = 1
MAX_STEPS = 5
TOTAL_BUDGET_SECONDS = 18.0
TERMINAL_CACHE_TTL_SECONDS = 300.0
CACHE_CAPACITY = 128

_DIRECT_COMMAND = re.compile(
    r"^[/／]?(?:查曲|查谱面|查谱|查卡|查角色卡|查卡面|查支援卡面|查SNAP卡面|查SNAP|SNAP|查支援卡|支援卡|song|chart|card|support)\s+", re.I,
)
_UNSUPPORTED_TERMS = re.compile(
    r"推荐|最强|排行|攻略|预测|档线|代练|代肝|账号|抽卡建议|编成|怎么打|如何打|"
    r"哪个好|哪个更好|比较强弱"
)


def is_ai_request(content: str) -> bool:
    cleaned = re.sub(r"<@!?\w+>", "", content).strip()
    return bool(re.match(r"^[/／]?(?:问|ask)(?:\s|$)", cleaned, re.I))


def query_text(content: str) -> str:
    cleaned = re.sub(r"<@!?\w+>", "", content).strip()
    return re.sub(r"^[/／]?(?:问|ask)\s*", "", cleaned, flags=re.I).strip()


@dataclass(frozen=True)
class RouteAction:
    capability_id: str


@dataclass(frozen=True)
class CallToolAction:
    capability_id: str
    payload: dict


@dataclass(frozen=True)
class AskClarificationAction:
    code: OutcomeCode


@dataclass(frozen=True)
class RejectAction:
    code: OutcomeCode


AgentAction = RouteAction | CallToolAction | AskClarificationAction | RejectAction


class ObservationCode(str, Enum):
    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"


@dataclass(frozen=True)
class Observation:
    code: ObservationCode
    invalid_fields: tuple[str, ...] = ()
    allowed: tuple[str, ...] = ()


@dataclass
class AgentState:
    question: str
    started_at: float
    deadline: float
    capability_id: str | None = None
    current_spec: QuerySpec | None = None
    model_calls: int = 0
    tool_executions: int = 0
    steps: int = 0
    repairs: int = 0
    last_action: AgentAction | None = None
    last_observation: Observation | None = None
    final_code: OutcomeCode | None = None


@dataclass(frozen=True)
class TerminalOutcome:
    code: OutcomeCode
    message: str


@dataclass(frozen=True)
class AgentOutcome:
    text: str
    payload: QueryResult | CommandResult | TerminalOutcome
    state: AgentState

    @property
    def result(self) -> QueryResult | CommandResult | None:
        return None if isinstance(self.payload, TerminalOutcome) else self.payload

    @property
    def code(self) -> OutcomeCode:
        return self.state.final_code or OutcomeCode.TEMPORARY_FAILURE


@dataclass(frozen=True)
class CachedOutcome:
    kind: str
    code: OutcomeCode
    spec: QuerySpec | None = None
    command: str | None = None
    message: str | None = None
    expires_at: float | None = None


ModelRequester = Callable[[str, str, float], ModelResponse | dict]


class QueryAgent:
    def __init__(self, *, ai_enabled: bool, daily_limit: int, quota: DailyQuota,
                 metrics: QueryMetrics, requester: ModelRequester,
                 clock: Callable[[], float] = monotonic) -> None:
        self.ai_enabled = ai_enabled
        self.daily_limit = daily_limit
        self.quota = quota
        self.metrics = metrics
        self.requester = requester
        self.clock = clock
        self._lock = RLock()
        self._cache: OrderedDict[str, CachedOutcome] = OrderedDict()
        self._states: OrderedDict[str, AgentState] = OrderedDict()
        self._cache_source: tuple[object, ...] | None = None

    @staticmethod
    def _source(repository: SongRepository) -> tuple[object, ...]:
        return (
            repository.metadata.get("cached_at"), len(repository.songs), len(repository.cards),
            len(repository.support_cards), int(repository.member_skill_index_ready()), alias_version(),
            (repository.song_traits.saved or {}).get("fetched_at"), repository.song_traits.stale,
        )

    def _sync_cache(self, repository: SongRepository) -> None:
        source = self._source(repository)
        with self._lock:
            if source != self._cache_source:
                self._cache.clear()
                self._states.clear()
                self._cache_source = source

    def _cache_get(self, question: str) -> CachedOutcome | None:
        now = self.clock()
        with self._lock:
            cached = self._cache.get(question)
            if cached is not None and cached.expires_at is not None and cached.expires_at <= now:
                del self._cache[question]
                return None
            if cached is not None:
                self._cache.move_to_end(question)
            return cached

    def _cache_put(self, question: str, cached: CachedOutcome) -> None:
        with self._lock:
            self._cache[question] = cached
            self._cache.move_to_end(question)
            if len(self._cache) > CACHE_CAPACITY:
                self._cache.popitem(last=False)

    def _remember_state(self, question: str, state: AgentState) -> None:
        with self._lock:
            self._states[question] = state
            self._states.move_to_end(question)
            if len(self._states) > CACHE_CAPACITY:
                self._states.popitem(last=False)

    def command_for(self, content: str) -> str | None:
        if not is_ai_request(content):
            return None
        cached = self._cache_get(query_text(content))
        if cached is None:
            return None
        if cached.spec is not None:
            return cached.spec.command_label()
        return cached.command

    def spec_for(self, content: str) -> QuerySpec | None:
        if not is_ai_request(content):
            return None
        cached = self._cache_get(query_text(content))
        return cached.spec if cached is not None else None

    def state_for(self, content: str) -> AgentState | None:
        if not is_ai_request(content):
            return None
        with self._lock:
            return self._states.get(query_text(content))

    def outcome_code_for(self, content: str) -> OutcomeCode | None:
        state = self.state_for(content)
        return state.final_code if state is not None else None

    def run(self, content: str, repository: SongRepository) -> AgentOutcome:
        self._sync_cache(repository)
        question = query_text(content)
        tracked = is_ai_request(content)
        started = self.clock()
        state = AgentState(question, started, started + TOTAL_BUDGET_SECONDS)
        if tracked:
            self.metrics.increment("ask_total")

        if not question or len(question) > 100:
            return self._finish_terminal(
                state, OutcomeCode.INVALID_ARGUMENTS,
                "用法：/问 <想查的歌曲、谱面、成员卡、技能或支援卡>，最多 100 字。",
                tracked=tracked, local=True, cache=False,
            )

        from ..query.continuation import parse_operation, MISSING
        if parse_operation(question) is not None:
            return self._finish_terminal(state, OutcomeCode.INVALID_ARGUMENTS, MISSING,
                                         tracked=tracked, local=True, cache=False)

        # Cutoffs are live source snapshots: bypass stale plan/terminal caches and AI quotas.
        from ..query.event_cutoff_query import parse_natural_cutoff
        cutoff = parse_natural_cutoff(question)
        if cutoff is not None:
            outcome = self._execute_spec(state, QuerySpec("event_cutoff", cutoff_request=cutoff), repository)
            if tracked:
                self.metrics.increment("local_success" if outcome.code == OutcomeCode.SUCCESS else "local_terminal_reject")
            self._remember_state(question, state)
            return outcome

        from ..query.field_query import parse_field_question
        field = parse_field_question(question, repository)
        if isinstance(field, str):
            return self._finish_terminal(state, self._message_code(field), field, tracked=tracked, local=True, cache=False)
        if field is not None:
            outcome = self._execute_spec(state, field, repository)
            if tracked:
                self.metrics.increment("local_success" if outcome.code == OutcomeCode.SUCCESS else "local_terminal_reject")
            self._remember_state(question, state)
            return outcome

        cached = self._cache_get(question)
        if cached is not None:
            return self._from_cache(state, cached, repository, tracked)

        if mentions_outside_catalog(question):
            return self._finish_terminal(
                state, OutcomeCode.UNKNOWN_ENTITY, UNKNOWN_ENTITY,
                tracked=tracked, local=True, cache=True,
            )
        from ..query.efficiency_query import MARKER, FORBIDDEN
        if (_UNSUPPORTED_TERMS.search(question)
                and (not MARKER.search(question) or FORBIDDEN.search(question))):
            return self._finish_terminal(
                state, OutcomeCode.UNSUPPORTED, UNSUPPORTED,
                tracked=tracked, local=True, cache=True,
            )
        if _DIRECT_COMMAND.match(question):
            selected = resolve_command(question, repository)
            text = handle_command(question, repository, resolved=selected) or UNSUPPORTED
            state.final_code = OutcomeCode(selected.status) if selected else OutcomeCode.UNSUPPORTED
            self._cache_put(question, CachedOutcome(
                "command", state.final_code, command=question,
            ))
            self._remember_state(question, state)
            if tracked:
                self.metrics.increment("local_success")
            return AgentOutcome(text, selected, state)

        local = parse_local_query(question, repository)
        if isinstance(local, str):
            code = self._message_code(local)
            return self._finish_terminal(
                state, code, local, tracked=tracked, local=True, cache=True,
            )
        if local is not None:
            outcome = self._execute_spec(state, local, repository)
            if tracked:
                self.metrics.increment("local_success")
            self._cache_spec(question, local, outcome.code)
            self._remember_state(question, state)
            return outcome

        if not self.ai_enabled:
            return self._finish_terminal(
                state, OutcomeCode.NOT_CONFIGURED,
                "自然语言查询尚未配置 AI；请用 /查曲、/查谱面、/查卡 或 /查支援卡。",
                tracked=tracked, cache=False,
            )

        capability_id = local_route(question)
        prefetched = None
        if capability_id is None:
            data, terminal = self._model_data(
                state, question, route_prompt(), "ai_route_requested", tracked,
            )
            if terminal is not None:
                return self._finish_model_terminal(state, terminal, tracked)
            assert data is not None
            routed = validate_route(data)
            if routed.capability_id is None:
                state.last_action = RejectAction(routed.code)
                self._record_ai_terminal(routed.code, tracked)
                return self._finish_terminal(
                    state, routed.code, message_for(routed.code), tracked=tracked,
                    cache=self._cacheable(routed.code),
                )
            action = RouteAction(routed.capability_id)
            state.last_action = action
            state.capability_id = action.capability_id
            capability_id = action.capability_id
            prefetched = routed.prefetched
        else:
            state.capability_id = capability_id
            state.last_action = RouteAction(capability_id)

        capability = CAPABILITIES[capability_id]
        evidence = self._evidence(capability, question, repository)
        if prefetched is None:
            data, terminal = self._model_data(
                state, question, capability.prompt(evidence=evidence),
                "ai_parse_requested", tracked,
            )
            if terminal is not None:
                return self._finish_model_terminal(state, terminal, tracked)
            assert data is not None
        else:
            data = prefetched

        allow_empty_subject = is_explicit_empty_subject_query(
            question, capability.intent, repository,
        )
        while True:
            state.last_action = CallToolAction(capability.id, data)
            validated = validate_capability_action(
                data, capability, question, repository,
                allow_empty_subject=allow_empty_subject,
            )
            if validated.spec is None:
                observation = self._validation_observation(validated)
                state.last_observation = observation
                if validated.repairable and self._can_repair(state):
                    data, terminal = self._repair(
                        state, question, capability, evidence, observation, tracked,
                    )
                    if terminal is not None:
                        return self._finish_model_terminal(state, terminal, tracked)
                    assert data is not None
                    continue
                action: AgentAction
                if validated.code == OutcomeCode.AMBIGUOUS:
                    action = AskClarificationAction(validated.code)
                else:
                    action = RejectAction(validated.code)
                state.last_action = action
                self._record_ai_terminal(validated.code, tracked)
                return self._finish_terminal(
                    state, validated.code, message_for(validated.code), tracked=tracked,
                    cache=self._cacheable(validated.code),
                )

            state.current_spec = validated.spec
            try:
                outcome = self._execute_spec(state, validated.spec, repository)
            except ValueError:
                observation = Observation(
                    ObservationCode.INVALID_ARGUMENTS, ("arguments",),
                    capability.allowed_parameters,
                )
                state.last_observation = observation
                if self._can_repair(state):
                    data, terminal = self._repair(
                        state, question, capability, evidence, observation, tracked,
                    )
                    if terminal is not None:
                        return self._finish_model_terminal(state, terminal, tracked)
                    assert data is not None
                    continue
                self._record_ai_terminal(OutcomeCode.INVALID_ARGUMENTS, tracked)
                return self._finish_terminal(
                    state, OutcomeCode.INVALID_ARGUMENTS, UNSUPPORTED,
                    tracked=tracked, cache=True,
                )
            except (OSError, KeyError, TypeError, IndexError):
                state.last_observation = Observation(ObservationCode.TEMPORARY_FAILURE)
                if tracked:
                    self.metrics.increment("ai_provider_error")
                return self._finish_terminal(
                    state, OutcomeCode.TEMPORARY_FAILURE,
                    "自然语言解析暂不可用；请直接使用 /查曲、/查谱面、/查卡 或 /查支援卡。",
                    tracked=tracked, cache=False,
                )

            self._cache_spec(question, validated.spec, outcome.code)
            self._remember_state(question, state)
            if (outcome.code == OutcomeCode.SUCCESS
                    and self._has_useful_result(outcome.payload)):
                QUERY_DEBUG_COUNTERS.record_useful_query()
            if tracked:
                metric = {
                    OutcomeCode.EMPTY: "ai_empty",
                    OutcomeCode.DATA_UNAVAILABLE: "ai_data_unavailable",
                }.get(outcome.code, "ai_success")
                self.metrics.increment(metric)
            return outcome

    def _from_cache(self, state: AgentState, cached: CachedOutcome,
                    repository: SongRepository, tracked: bool) -> AgentOutcome:
        metric = "cache_hit_success" if cached.code == OutcomeCode.SUCCESS else "cache_hit_terminal"
        if tracked:
            self.metrics.increment(metric)
        if cached.kind == "terminal":
            return self._finish_terminal(
                state, cached.code, cached.message or UNSUPPORTED,
                tracked=tracked, cache=False,
            )
        if cached.spec is not None:
            outcome = self._execute_spec(state, cached.spec, repository)
            self._remember_state(state.question, state)
            return outcome
        assert cached.command is not None
        selected = resolve_command(cached.command, repository)
        state.final_code = OutcomeCode(selected.status) if selected else OutcomeCode.UNSUPPORTED
        self._remember_state(state.question, state)
        return AgentOutcome(
            handle_command(cached.command, repository, resolved=selected) or UNSUPPORTED,
            selected, state,
        )

    def _model_data(self, state: AgentState, question: str, prompt: str, metric: str,
                    tracked: bool) -> tuple[dict | None, TerminalOutcome | None]:
        remaining = state.deadline - self.clock()
        if (remaining <= 0 or state.model_calls >= MAX_MODEL_CALLS
                or state.steps >= MAX_STEPS):
            return None, TerminalOutcome(
                OutcomeCode.TEMPORARY_FAILURE,
                "自然语言解析暂不可用；请直接使用 /查曲、/查谱面、/查卡 或 /查支援卡。",
            )
        try:
            available = self.quota.reserve(self.daily_limit)
        except QuotaUnavailable:
            return None, TerminalOutcome(
                OutcomeCode.QUOTA_UNAVAILABLE,
                "AI 额度记录不可用；仍可使用 /查曲、/查谱面、/查卡、/查支援卡及常见技能问法。请联系管理员检查额度文件。",
            )
        if not available:
            if tracked:
                self.metrics.increment("quota_exhausted")
            return None, TerminalOutcome(
                OutcomeCode.QUOTA_EXHAUSTED,
                "今日自然语言查询额度已用完；仍可使用 /查曲、/查谱面、/查卡、/查支援卡及常见技能问法。",
            )
        state.model_calls += 1
        state.steps += 1
        if tracked:
            self.metrics.increment(metric)
        try:
            response = self.requester(question, prompt, min(12.0, remaining))
        except AIInvalidResponse:
            return None, TerminalOutcome(OutcomeCode.INVALID_OUTPUT, UNSUPPORTED)
        except (AIClientError, OSError, ValueError, KeyError, TypeError, IndexError):
            if tracked:
                self.metrics.increment("ai_provider_error")
            return None, TerminalOutcome(
                OutcomeCode.TEMPORARY_FAILURE,
                "自然语言解析暂不可用；请直接使用 /查曲、/查谱面、/查卡 或 /查支援卡。",
            )
        if isinstance(response, ModelResponse):
            if response.usage:
                self.metrics.increment_many({
                    "ai_" + key: value for key, value in response.usage.items()
                })
            data = response.data
        else:
            data = response
        if not isinstance(data, dict):
            return None, TerminalOutcome(OutcomeCode.INVALID_OUTPUT, UNSUPPORTED)
        QUERY_DEBUG_COUNTERS.record_api_success()
        return data, None

    def _repair(self, state: AgentState, question: str, capability: Capability,
                evidence: tuple[str, ...], observation: Observation,
                tracked: bool) -> tuple[dict | None, TerminalOutcome | None]:
        state.repairs += 1
        summary = json.dumps({
            "code": observation.code.value,
            "invalid_fields": list(observation.invalid_fields),
            "allowed": list(observation.allowed),
        }, ensure_ascii=False, separators=(",", ":"))
        prompt = (
            capability.prompt(evidence=evidence)
            + "\n上一次结构化参数校验失败。只根据以下 Observation 修正参数一次；"
              "不得删除用户条件、替换实体或扩大范围：\n" + summary
        )
        return self._model_data(
            state, question, prompt, "ai_repair_requested", tracked,
        )

    @staticmethod
    def _evidence(capability: Capability, question: str,
                  repository: SongRepository) -> tuple[str, ...]:
        anchor = find_anchor(
            capability.intent, question, repository,
            explicit_card_ids=capability.intent == "card" and "技能" in question,
        )
        if anchor.entity is None:
            return ()
        return (f"{anchor.entity.kind}:{anchor.entity.value}",)

    def _execute_spec(self, state: AgentState, spec: QuerySpec,
                      repository: SongRepository) -> AgentOutcome:
        if (state.tool_executions >= MAX_TOOL_EXECUTIONS or state.steps >= MAX_STEPS
                or self.clock() >= state.deadline):
            raise OSError("agent budget exhausted")
        state.tool_executions += 1
        state.steps += 1
        if spec.field:
            from ..structured_query import resolve_query
            result = resolve_query(spec, repository)
        else:
            result = capability_for_spec(spec).executor(spec, repository)
        code = self._result_code(result, repository)
        state.last_observation = Observation(self._observation_code(code))
        state.final_code = code
        return AgentOutcome(answer_for(spec, repository, result=result), result, state)

    def _finish_model_terminal(self, state: AgentState, terminal: TerminalOutcome,
                               tracked: bool) -> AgentOutcome:
        if terminal.code == OutcomeCode.INVALID_OUTPUT:
            self._record_ai_terminal(terminal.code, tracked)
        return self._finish_terminal(
            state, terminal.code, terminal.message, tracked=tracked,
            cache=self._cacheable(terminal.code),
        )

    def _finish_terminal(self, state: AgentState, code: OutcomeCode, message: str, *,
                         tracked: bool, local: bool = False, cache: bool) -> AgentOutcome:
        state.final_code = code
        if local and tracked:
            self.metrics.increment("local_terminal_reject")
        if cache:
            self._cache_put(state.question, CachedOutcome(
                "terminal", code, message=message,
                expires_at=self.clock() + TERMINAL_CACHE_TTL_SECONDS,
            ))
        self._remember_state(state.question, state)
        terminal = TerminalOutcome(code, message)
        return AgentOutcome(message, terminal, state)

    def _cache_spec(self, question: str, spec: QuerySpec, code: OutcomeCode) -> None:
        expires = None if code == OutcomeCode.SUCCESS else self.clock() + TERMINAL_CACHE_TTL_SECONDS
        self._cache_put(question, CachedOutcome(
            "spec", code, spec=spec, expires_at=expires,
        ))

    @staticmethod
    def _cacheable(code: OutcomeCode) -> bool:
        return code in {
            OutcomeCode.UNSUPPORTED,
            OutcomeCode.UNKNOWN_ENTITY,
            OutcomeCode.AMBIGUOUS,
            OutcomeCode.INVALID_OUTPUT,
            OutcomeCode.INVALID_ARGUMENTS,
        }

    def _can_repair(self, state: AgentState) -> bool:
        return (state.repairs < MAX_REPAIRS and state.model_calls < MAX_MODEL_CALLS
                and state.tool_executions < MAX_TOOL_EXECUTIONS
                and state.steps < MAX_STEPS and self.clock() < state.deadline)

    def _record_ai_terminal(self, code: OutcomeCode, tracked: bool) -> None:
        if not tracked:
            return
        metric = {
            OutcomeCode.UNSUPPORTED: "ai_unsupported",
            OutcomeCode.UNKNOWN_ENTITY: "ai_unknown_entity",
            OutcomeCode.AMBIGUOUS: "ai_ambiguous",
        }.get(code, "ai_invalid_output")
        self.metrics.increment(metric)

    @staticmethod
    def _validation_observation(validated: ValidationResult) -> Observation:
        if validated.code == OutcomeCode.AMBIGUOUS:
            code = ObservationCode.AMBIGUOUS
        else:
            code = ObservationCode.INVALID_ARGUMENTS
        return Observation(code, validated.invalid_fields, validated.allowed)

    @staticmethod
    def _message_code(message: str) -> OutcomeCode:
        if hasattr(message, "status"):
            return OutcomeCode(message.status)
        if message == UNKNOWN_ENTITY:
            return OutcomeCode.UNKNOWN_ENTITY
        if message == AMBIGUOUS_ENTITY:
            return OutcomeCode.AMBIGUOUS
        return OutcomeCode.INVALID_ARGUMENTS

    @staticmethod
    def _result_code(result: QueryResult, repository: SongRepository) -> OutcomeCode:
        spec = result.spec
        if result.short_text is not None:
            return OutcomeCode(result.status)
        if result.cutoff is not None:
            return OutcomeCode(result.cutoff.status)
        if result.song_selection and result.song_selection.unavailable:
            return OutcomeCode.DATA_UNAVAILABLE
        if result.catalog is not None:
            return OutcomeCode(result.catalog.status)
        if spec.intent == "efficiency":
            return OutcomeCode(result.meta.status)
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

    @staticmethod
    def _has_useful_result(result: QueryResult | CommandResult | TerminalOutcome) -> bool:
        """Whether the final reply contains retrieved records, not an empty/page notice."""
        if not isinstance(result, QueryResult):
            return False
        spec = result.spec
        if result.cutoff is not None:
            return any(board.scores for board in result.cutoff.boards)
        if spec.intent == "efficiency":
            return bool(result.meta and result.meta.rows)
        if spec.intent == "chart":
            return result.chart is not None and bool(result.chart[1])
        if spec.intent == "card":
            if spec.subject is not None and spec.subject.kind == "card":
                return bool(result.cards)
            rows = result.cards
        elif spec.intent == "support_card":
            if spec.subject is not None and spec.subject.kind == "support_card":
                return bool(result.support_cards)
            rows = result.support_cards
        else:
            rows = result.songs
        return (spec.page - 1) * PAGE_SIZE < len(rows)

    @staticmethod
    def _observation_code(code: OutcomeCode) -> ObservationCode:
        return {
            OutcomeCode.SUCCESS: ObservationCode.SUCCESS,
            OutcomeCode.EMPTY: ObservationCode.EMPTY,
            OutcomeCode.DATA_UNAVAILABLE: ObservationCode.DATA_UNAVAILABLE,
        }.get(code, ObservationCode.TEMPORARY_FAILURE)

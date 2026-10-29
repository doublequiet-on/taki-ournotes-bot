# L3: Native model, usage and exact reference-pricing contracts; no IO.
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
import re


class ModelError(ValueError):
    """Finite diagnostic code, never provider prose or player content."""


def require(value, code):
    if not value:
        raise ModelError(code)


@dataclass(frozen=True)
class Usage:
    input_total: int | None = None
    input_cached: int | None = None
    output_total: int | None = None
    output_reasoning: int | None = None
    total: int | None = None
    status: str = 'unknown'

    def __post_init__(self):
        values = (self.input_total, self.input_cached, self.output_total, self.output_reasoning, self.total)
        require(all(v is None or type(v) is int and 0 <= v < 2**53 for v in values), 'usage_shape')
        require(self.status in ('known', 'partial', 'unknown'), 'usage_shape')
        require(self.input_cached is None or self.input_total is None or self.input_cached <= self.input_total, 'usage_shape')
        require(self.output_reasoning is None or self.output_total is None or self.output_reasoning <= self.output_total, 'usage_shape')
        require(None in (self.total, self.input_total, self.output_total) or self.total == self.input_total+self.output_total, 'usage_shape')
        require(self.status != 'known' or None not in (self.input_total, self.input_cached, self.output_total), 'usage_shape')


@dataclass(frozen=True)
class Price:
    # All rates are integer micro-currency / million tokens. Reference cost is
    # exact micro-currency, weighted quota is the equivalent reference tokens.
    currency: str
    input_rate: int
    cached_rate: int
    output_rate: int
    reference_rate: int
    pricing_version: str
    fetched_at: float
    estimated: bool
    source: str

    def __post_init__(self):
        require(self.currency in ('USD', 'CNY') and type(self.estimated) is bool, 'pricing_configuration')
        require(all(type(v) is int and 0 < v <= 10**12 for v in
                    (self.input_rate, self.cached_rate, self.output_rate, self.reference_rate)), 'pricing_configuration')
        require(isinstance(self.pricing_version, str) and re.fullmatch(r'[\w./:-]{1,120}', self.pricing_version), 'pricing_configuration')
        require(type(self.fetched_at) in (int,float) and 0 <= self.fetched_at < 10**12, 'pricing_configuration')
        require(self.source in ('registered-default', 'openrouter-models'), 'pricing_configuration')

    @property
    def reference_unit(self):
        return f'{self.currency}:{self.reference_rate}:per-million'

    def amount(self, usage):
        numerator = 0
        if usage.input_total is not None and usage.input_cached is not None:
            numerator += (usage.input_total-usage.input_cached)*self.input_rate + usage.input_cached*self.cached_rate
        elif usage.input_total is not None and self.input_rate == self.cached_rate:
            numerator += usage.input_total*self.input_rate
        # Do not guess the price split from total tokens or a missing cache count.
        if usage.output_total is not None:
            numerator += usage.output_total*self.output_rate
        return Fraction(numerator, self.reference_rate), Fraction(numerator, 1_000_000)

    def reserve(self, input_bound, output_bound):
        return Fraction(input_bound*max(self.input_rate,self.cached_rate) + output_bound*self.output_rate,
                        self.reference_rate)


@dataclass(frozen=True)
class ModelProfile:
    provider: str
    model: str
    config_version: str
    wire_profile: str
    capability_evidence: str
    price: Price
    api_key: str = field(repr=False)
    max_input_tokens: int = 32768
    max_output_tokens: int = 768
    user_daily_limit: int = 100000
    group_daily_limit: int = 1000000
    parallel_control: bool = False
    cache_usage: bool = True
    reasoning_usage: bool = False
    user_rate_requests: int = 3
    group_rate_requests: int = 30
    rate_window_seconds: int = 10
    reasoning_effort: str | None = None

    def __post_init__(self):
        require(self.provider in ('deepseek', 'openai', 'openrouter'), 'provider_unsupported')
        require(self.wire_profile == self.provider+'-chat-v1' or
                (self.provider=='openai' and self.wire_profile=='openai-responses-v1'), 'wire_profile_unsupported')
        require(self.reasoning_effort is None or (self.wire_profile=='openai-responses-v1'
                and self.reasoning_effort in ('none','low','medium','high','xhigh','max')), 'unsupported_parameter')
        require(isinstance(self.model,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:-]{0,119}', self.model), 'model_configuration')
        require(isinstance(self.config_version,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}',self.config_version), 'model_configuration')
        require(isinstance(self.capability_evidence,str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}',self.capability_evidence), 'capability_unverified')
        require(isinstance(self.api_key,str) and 1 <= len(self.api_key) <= 512 and not any(c.isspace() for c in self.api_key), 'model_not_configured')
        require(type(self.max_input_tokens) is int and 8192 <= self.max_input_tokens <= 65536, 'model_configuration')
        output_limit=8192 if self.wire_profile=='openai-responses-v1' else 1536
        require(type(self.max_output_tokens) is int and 64 <= self.max_output_tokens <= output_limit, 'model_configuration')
        require(all(type(v) is int and 0 <= v <= 10**12 for v in (self.user_daily_limit,self.group_daily_limit)), 'model_configuration')
        require(all(type(v) is bool for v in (self.parallel_control,self.cache_usage,self.reasoning_usage)), 'model_configuration')
        require(not self.parallel_control or self.provider in ('openai','openrouter'), 'unsupported_parameter')
        require(all(type(v) is int and 1 <= v <= 1000 for v in
                    (self.user_rate_requests,self.group_rate_requests)), 'model_configuration')
        require(type(self.rate_window_seconds) is int and 1 <= self.rate_window_seconds <= 3600, 'model_configuration')


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str
    response_items: tuple = ()


@dataclass(frozen=True)
class AttemptResult:
    status: str
    usage: Usage = Usage()
    tool_calls: tuple[ToolCall, ...] = ()
    text: str | None = None
    error_code: str | None = None
    # One complete provider response, never duplicated per executable call.
    output_items: tuple = ()

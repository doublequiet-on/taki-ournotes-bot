# L3: Shared deterministic parser facade, without a model client or quota writer.
# Input: Settings, command text and a captured SongRepository view.
# Output: Existing AgentOutcome/QuerySpec/CommandResult; shared QueryAgent grammar.
# Effects: Local query source reads and metrics only; request policy can tighten to off.
from dataclasses import replace

from ..domain.query_policy import CURRENT_POLICY
from .query_agent import QueryAgent
from .query_metrics import QueryMetrics


class LocalQueryParser:
    def __init__(self, settings):
        self.settings = settings
        self._metrics = QueryMetrics(settings.ai_metrics_file or settings.cache_file.with_name('ai-metrics.json'))
        self._agent = QueryAgent(ai_enabled=False, daily_limit=0, quota=None,
                                 metrics=self._metrics, requester=None)

    def metrics_snapshot(self):
        return self._metrics.snapshot()

    def policy_capabilities(self):
        return {'model_configured': self._agent.ai_enabled, 'daily_limit': self._agent.daily_limit}

    def command_for(self, content):
        return self._agent.command_for(content)

    def spec_for(self, content):
        return self._agent.spec_for(content)

    def outcome_code_for(self, content):
        return self._agent.outcome_code_for(content)

    def state_for(self, content):
        return self._agent.state_for(content)

    def answer(self, content, repository):
        return self.answer_with_outcome(content, repository).text

    def answer_with_plan(self, content, repository):
        outcome = self.answer_with_outcome(content, repository)
        return outcome.text, outcome.result

    def answer_with_outcome(self, content, repository):
        policy = CURRENT_POLICY.get()
        token = CURRENT_POLICY.set(replace(policy, natural_query='off' if policy.natural_query == 'off' else 'local'))
        try:
            # Native local grammar permits 500 characters. The local ceiling
            # prevents every DeferredModel branch regardless of configured keys.
            return self._agent.run(content, repository, defer_model=True, native=True)
        finally:
            CURRENT_POLICY.reset(token)

    def prepare_local(self, content, repository):
        return self.answer_with_outcome(content, repository)

    def prepare_native_local(self, content, repository):
        return self.answer_with_outcome(content, repository)

    def sweep(self):
        self._agent.sweep()

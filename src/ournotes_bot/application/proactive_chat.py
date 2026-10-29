# L3: Bounded group observation and versioned participation opportunities.
# Input: Host-approved group policy, normalized human ChatMessages and model decisions.
# Output: Immutable candidates and send-time validity; body-free budget diagnostics.
# Effects: Volatile memory only; no model, platform, ledger or persistent data access.
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
import json
import time
from uuid import uuid4

from ..domain.chat import ChatMessage


@dataclass(frozen=True)
class ProactivePolicy:
    enabled: bool = False
    service_subject_id: str = ''
    approval_ref: str = ''
    max_calls_per_hour: int = 6
    max_replies_per_hour: int = 3
    max_cost_micro_per_hour: int = 0

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('proactive_policy_invalid')
        if any(type(value) is not str or len(value) > 512
               for value in (self.service_subject_id, self.approval_ref)):
            raise ValueError('proactive_policy_invalid')
        if any(type(value) is not int or not 1 <= value <= 120
               for value in (self.max_calls_per_hour, self.max_replies_per_hour)):
            raise ValueError('proactive_policy_invalid')
        if type(self.max_cost_micro_per_hour) is not int or self.max_cost_micro_per_hour < 0:
            raise ValueError('proactive_policy_invalid')

    @property
    def available(self):
        return (self.enabled and bool(self.service_subject_id.strip())
                and bool(self.approval_ref.strip()) and self.max_cost_micro_per_hour > 0)


@dataclass(frozen=True)
class ProactiveCandidate:
    candidate_id: str
    scope: tuple
    version: int
    policy_version: int
    service_subject_id: str
    messages: tuple[ChatMessage, ...]
    created_at: float
    expires_at: float
    reserved_cost_micro: int
    deferred: bool = False
    evaluation: int = 1


@dataclass
class _Observed:
    message: ChatMessage
    at: float
    size: int


@dataclass
class _Ticket:
    candidate_id: str
    scope: tuple
    version: int
    policy_version: int
    service_subject_id: str
    created_at: float
    expires_at: float
    reserved_cost_micro: int
    evaluation: int
    cost_micro: int | None = None
    decision: str = ''
    delivered: bool = False

    @property
    def charged_cost(self):
        return self.reserved_cost_micro if self.cost_micro is None else self.cost_micro


@dataclass
class _Group:
    policy: ProactivePolicy
    policy_version: int = 1
    version: int = 0
    rows: list[_Observed] = field(default_factory=list)
    seen: OrderedDict = field(default_factory=OrderedDict)
    tickets: dict[str, _Ticket] = field(default_factory=dict)
    replies: list[float] = field(default_factory=list)
    last_message_at: float = 0
    last_delivery_at: float = float('-inf')
    active: str | None = None
    dirty: bool = False
    addressed_active: bool = False
    deferred_at: float | None = None
    evaluation: int = 0


class ProactiveController:
    """One host thread owns this controller; approved billing stays with the host.

    A call budget unit is one candidate/participation evaluation, including its
    tool loop. The shared TurnBudget separately bounds actual model requests.
    The host reserves a worst-case *whole evaluation* fee before calling candidate.
    """
    def __init__(self, *, clock=time.monotonic, coalesce_seconds=1.5,
                 cooldown_seconds=60, candidate_seconds=60, max_defer_rechecks=2,
                 max_messages=120, window_seconds=900, group_bytes=256*1024,
                 max_bytes=8*1024*1024, max_scopes=1024):
        if not 1 <= coalesce_seconds <= 2 or cooldown_seconds < 0 or candidate_seconds <= 0:
            raise ValueError('proactive_limits_invalid')
        if (type(max_defer_rechecks) is not int or not 0 <= max_defer_rechecks <= 2
                or any(type(value) is not int or value <= 0 for value in
                       (max_messages, group_bytes, max_bytes, max_scopes))
                or not 0 < window_seconds <= 900 or max_messages > 120
                or group_bytes > 256*1024 or max_bytes > 8*1024*1024
                or max_scopes > 1024 or group_bytes > max_bytes):
            raise ValueError('proactive_limits_invalid')
        self.clock = clock
        self.coalesce_seconds = coalesce_seconds
        self.cooldown_seconds = cooldown_seconds
        self.candidate_seconds = candidate_seconds
        self.max_defer_rechecks = max_defer_rechecks
        self.max_messages, self.window_seconds = max_messages, window_seconds
        self.group_bytes, self.max_bytes, self.max_scopes = group_bytes, max_bytes, max_scopes
        self.groups = {}

    def configure(self, scope, policy):
        if not isinstance(scope, tuple) or not scope or not isinstance(policy, ProactivePolicy):
            raise ValueError('proactive_scope_or_policy_invalid')
        hash(scope)
        group = self.groups.get(scope)
        if group is None:
            if len(self.groups) >= self.max_scopes:
                raise ValueError('proactive_scope_limit')
            self.groups[scope] = _Group(policy)
            return
        if group.policy == policy:
            return
        group.policy, group.policy_version = policy, group.policy_version + 1
        self._cancel(group)
        if not policy.available:
            group.rows.clear()
            group.seen.clear()

    @staticmethod
    def _cancel(group):
        group.version += 1
        group.active, group.deferred_at, group.dirty = None, None, False
        group.evaluation = 0

    def cancel(self, scope):
        group = self.groups.get(scope)
        if group is not None:
            self._cancel(group)

    def set_addressed(self, scope, active):
        """Host brackets explicit dialogue through its final delivery receipt."""
        if type(active) is not bool:
            raise ValueError('proactive_addressed_invalid')
        group = self.groups.get(scope)
        if group is not None:
            self._cancel(group)
            group.addressed_active = active

    def clear(self, scope):
        """Forget observation bodies and cancel candidates; keep real budget use."""
        group = self.groups.get(scope)
        if group is not None:
            self._cancel(group)
            group.rows.clear()
            group.seen.clear()

    def sweep(self):
        now = self.clock()
        for group in self.groups.values():
            retained = [row for row in group.rows if row.at > now - self.window_seconds]
            if len(retained) != len(group.rows):
                group.rows = retained
                self._cancel(group)
            for message_id, at in list(group.seen.items()):
                if at <= now - self.window_seconds:
                    del group.seen[message_id]
            group.replies[:] = [at for at in group.replies if at > now - 3600]
            group.tickets = {key: value for key, value in group.tickets.items()
                             if value.created_at > now - 3600 or key == group.active}
            if group.active is not None:
                ticket = group.tickets[group.active]
                if ticket.expires_at <= now:
                    group.active = None
        while self.retained_bytes() > self.max_bytes:
            group = min((item for item in self.groups.values() if item.rows),
                        key=lambda item: item.rows[0].at)
            group.rows.pop(0)
            self._cancel(group)

    def retained_bytes(self):
        return sum(row.size for group in self.groups.values() for row in group.rows)

    def observe(self, scope, message, *, addressed=False, is_bot=False):
        self.sweep()
        group = self.groups.get(scope)
        if group is None or not group.policy.available or is_bot:
            return False
        if not isinstance(message, ChatMessage) or message.role != 'user':
            return False
        if (not message.message_id or not message.speaker_id
                or any(type(value) is not str for value in
                       (message.message_id, message.speaker_id, message.text, message.display_name))):
            raise ValueError('proactive_message_invalid')
        if message.message_id in group.seen:
            return False
        now = self.clock()
        self._cancel(group)
        group.last_message_at = now
        group.seen[message.message_id] = now
        while len(group.seen) > self.max_messages * 4:
            group.seen.popitem(last=False)
        size = len(json.dumps(asdict(message), ensure_ascii=False).encode('utf-8'))
        if size > self.group_bytes:
            # An omitted new message must invalidate the old answer, not wake a
            # model with a misleadingly complete older scene.
            group.rows.clear()
            return False
        group.rows.append(_Observed(message, now, size))
        while (len(group.rows) > self.max_messages
               or sum(row.size for row in group.rows) > self.group_bytes):
            group.rows.pop(0)
        group.dirty = not addressed
        self.sweep()
        return True

    @staticmethod
    def _cost(group):
        return sum(ticket.charged_cost for ticket in group.tickets.values())

    def candidate(self, scope, *, reserve_cost_micro):
        if type(reserve_cost_micro) is not int or reserve_cost_micro < 0:
            raise ValueError('proactive_reservation_invalid')
        self.sweep()
        group = self.groups.get(scope)
        now = self.clock()
        if (group is None or not group.policy.available or group.addressed_active
                or group.active is not None or not group.rows
                or now < group.last_message_at + self.coalesce_seconds
                or now < group.last_delivery_at + self.cooldown_seconds):
            return None
        deferred = group.deferred_at is not None
        if not group.dirty and not (deferred and now >= group.deferred_at):
            return None
        if (len(group.tickets) >= group.policy.max_calls_per_hour
                or len(group.replies) >= group.policy.max_replies_per_hour
                or self._cost(group) + reserve_cost_micro > group.policy.max_cost_micro_per_hour):
            return None
        group.evaluation = group.evaluation + 1 if deferred else 1
        ticket = _Ticket(uuid4().hex, scope, group.version, group.policy_version,
                         group.policy.service_subject_id, now, now + self.candidate_seconds,
                         reserve_cost_micro, group.evaluation)
        group.tickets[ticket.candidate_id] = ticket
        group.active, group.deferred_at, group.dirty = ticket.candidate_id, None, False
        return ProactiveCandidate(ticket.candidate_id, scope, ticket.version, ticket.policy_version,
                                  ticket.service_subject_id, tuple(row.message for row in group.rows),
                                  now, ticket.expires_at, reserve_cost_micro, deferred, group.evaluation)

    def _ticket(self, candidate):
        group = self.groups.get(candidate.scope)
        ticket = group.tickets.get(candidate.candidate_id) if group else None
        if (ticket is None or ticket.version != candidate.version
                or ticket.policy_version != candidate.policy_version
                or ticket.service_subject_id != candidate.service_subject_id):
            return group, None
        return group, ticket

    def valid(self, candidate):
        self.sweep()
        group, ticket = self._ticket(candidate)
        return bool(ticket and group.policy.available and not group.addressed_active
                    and group.active == ticket.candidate_id and group.version == ticket.version
                    and group.policy_version == ticket.policy_version
                    and group.policy.service_subject_id == ticket.service_subject_id
                    and self.clock() < ticket.expires_at and not ticket.delivered
                    and self.clock() >= group.last_delivery_at + self.cooldown_seconds
                    and len(group.replies) < group.policy.max_replies_per_hour
                    and self._cost(group) <= group.policy.max_cost_micro_per_hour)

    def can_send(self, candidate):
        if not self.valid(candidate):
            return False
        _, ticket = self._ticket(candidate)
        return ticket.decision == 'reply'

    def settle(self, candidate, *, actual_cost_micro):
        """Also settle stale/skip calls. Unknown usage keeps its full reservation."""
        if actual_cost_micro is not None and (type(actual_cost_micro) is not int or actual_cost_micro < 0):
            raise ValueError('proactive_cost_invalid')
        _, ticket = self._ticket(candidate)
        if ticket is None:
            return False
        if ticket.cost_micro is not None and actual_cost_micro not in (None, ticket.cost_micro):
            raise ValueError('proactive_cost_conflict')
        if actual_cost_micro is not None:
            ticket.cost_micro = actual_cost_micro
        return True

    def decide(self, candidate, decision, *, actual_cost_micro=None, defer_seconds=2):
        if decision not in {'reply', 'skip', 'defer'}:
            raise ValueError('proactive_decision_invalid')
        if not 1 <= defer_seconds <= 5:
            raise ValueError('proactive_defer_invalid')
        if not self.settle(candidate, actual_cost_micro=actual_cost_micro):
            return False
        group, ticket = self._ticket(candidate)
        if ticket.decision:
            if ticket.decision != decision:
                raise ValueError('proactive_decision_conflict')
            return self.valid(candidate)
        ticket.decision = decision
        if not self.valid(candidate):
            return False
        if decision == 'reply':
            return True
        group.active = None
        if decision == 'defer' and ticket.evaluation <= self.max_defer_rechecks:
            group.deferred_at = self.clock() + defer_seconds
        return True

    def delivered(self, candidate):
        """Record an actual successful ACK, even when policy changed in flight.

        This is receipt accounting, never send authorization. Check can_send
        immediately before the host's existing send operation.
        """
        group, ticket = self._ticket(candidate)
        if ticket is None or ticket.decision != 'reply' or ticket.delivered:
            return False
        ticket.delivered = True
        now = self.clock()
        group.replies.append(now)
        group.last_delivery_at = now
        self._cancel(group)
        return True

    def status(self, scope):
        self.sweep()
        group = self.groups.get(scope)
        if group is None:
            return {'enabled': False, 'service_approved': False, 'buffered_messages': 0}
        return {'enabled': group.policy.enabled, 'service_approved': group.policy.available,
                'version': group.version, 'policy_version': group.policy_version,
                'buffered_messages': len(group.rows), 'buffered_bytes': sum(row.size for row in group.rows),
                'global_buffered_bytes': self.retained_bytes(), 'active': group.active is not None,
                'addressed_active': group.addressed_active, 'hourly_calls': len(group.tickets),
                'hourly_replies': len(group.replies), 'hourly_cost_micro': self._cost(group),
                'incomplete_costs': sum(ticket.cost_micro is None for ticket in group.tickets.values())}

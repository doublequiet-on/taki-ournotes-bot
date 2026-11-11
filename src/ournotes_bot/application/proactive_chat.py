# L3: Bounded group observation and versioned participation opportunities.
# Input: Host-approved group policy, normalized human ChatMessages and model decisions.
# Output: Immutable candidates and send-time validity; body-free budget diagnostics.
# Effects: Volatile observations and opportunity knowledge leases only; no model,
# platform, ledger or persistent data access. Startup waits at most eight seconds.
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
    cooldown_seconds: int = 60

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
        if type(self.cooldown_seconds) is not int or not 0 <= self.cooldown_seconds <= 86400:
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
    followup: object = None
    opportunity_id: str = ''
    continuation: bool = False
    lightweight: bool = False
    evidence_route: str = ''


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
    opportunity_id: str = ''
    held_cost_micro: int | None = None

    @property
    def charged_cost(self):
        if self.cost_micro is not None:return self.cost_micro
        return self.reserved_cost_micro if self.held_cost_micro is None else self.held_cost_micro


@dataclass
class _Opportunity:
    id: str
    expires_at: float
    cost_micro: int
    evaluations: int = 0
    tools: int = 0
    knowledge_leases: dict = field(default_factory=dict)
    send_interrupted: bool = False


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
    opportunity: _Opportunity | None = None
    pending_since: float | None = None
    coalesce_seconds: float | None = None
    continuation_user_id: str | None = None
    target_id: str | None = None
    pending_opportunity: str | None = None
    send_started: bool = False


class ProactiveController:
    """One host thread owns this controller; approved billing stays with the host.

    Hourly tickets count candidate evaluations. Replacements and defers share a
    fixed opportunity deadline, evaluation ceiling and tool count. The host
    applies its model-call/cost ceiling to their existing persistent attempt rows.
    """
    def __init__(self, *, clock=time.monotonic, coalesce_seconds=1.5,
                 candidate_seconds=60, max_defer_rechecks=2,
                 max_messages=120, window_seconds=900, group_bytes=256*1024,
                 max_bytes=8*1024*1024, max_scopes=1024):
        if not 1 <= coalesce_seconds <= 2 or candidate_seconds <= 0:
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
        if group.opportunity is not None:group.opportunity.knowledge_leases.clear()
        if not policy.available:
            group.rows.clear()
            group.seen.clear()

    @staticmethod
    def _cancel(group):
        if group.send_started and group.opportunity is not None:
            group.opportunity.send_interrupted = True
        group.version += 1
        group.active, group.deferred_at, group.dirty = None, None, False
        group.pending_since = None
        group.evaluation = 0
        group.target_id = None
        group.pending_opportunity = None
        group.send_started = False

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
            if group.opportunity is not None:group.opportunity.knowledge_leases.clear()
            group.rows.clear()
            group.seen.clear()

    def sweep(self):
        now = self.clock()
        for group in self.groups.values():
            if group.opportunity is not None and group.opportunity.expires_at <= now:
                group.opportunity.knowledge_leases.clear()
                if group.pending_opportunity == group.opportunity.id:
                    self._cancel(group)
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

    def target(self, scope):
        group = self.groups.get(scope)
        if group is not None and (group.active or group.dirty or group.deferred_at is not None):
            return next((row.message for row in reversed(group.rows)
                         if row.message.message_id==group.target_id),None)

    def observe(self, scope, message, *, addressed=False, is_bot=False, turn='advance'):
        if turn not in ('advance','supplement','interjection','closed'):
            raise ValueError('proactive_turn_invalid')
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
        pending_since = group.pending_since
        group.seen[message.message_id] = now
        while len(group.seen) > self.max_messages * 4:
            group.seen.popitem(last=False)
        size = len(json.dumps(asdict(message), ensure_ascii=False).encode('utf-8'))
        if size > self.group_bytes:
            # An omitted new message must invalidate the old answer, not wake a
            # model with a misleadingly complete older scene.
            group.rows.clear()
            self._cancel(group)
            return False
        group.rows.append(_Observed(message, now, size))
        while (len(group.rows) > self.max_messages
               or sum(row.size for row in group.rows) > self.group_bytes):
            group.rows.pop(0)
        if turn == 'interjection' and not addressed and self.target(scope) is not None:
            self.sweep()
            return True
        self._cancel(group)
        group.last_message_at = now
        group.dirty = (not addressed and turn in ('advance','supplement')
                       and not (group.opportunity is not None and group.opportunity.send_interrupted
                                and now < group.opportunity.expires_at))
        if group.dirty:
            group.pending_since = pending_since if pending_since is not None else now
            group.target_id = message.message_id
            if turn=='supplement' and group.opportunity is not None and now<group.opportunity.expires_at:
                group.pending_opportunity=group.opportunity.id
        self.sweep()
        return True

    def start_delay(self, scope):
        group = self.groups[scope]
        ready_at = group.last_message_at + (group.coalesce_seconds if group.coalesce_seconds is not None else self.coalesce_seconds)
        if group.pending_since is not None:
            ready_at = min(ready_at, group.pending_since + 8)
        return max(0, ready_at - self.clock())

    def knowledge_leases(self, candidate):
        group, _ = self._ticket(candidate)
        if not self.valid(candidate):
            raise ValueError('proactive_opportunity_expired')
        return group.opportunity.knowledge_leases

    @staticmethod
    def _cost(group):
        return sum(ticket.charged_cost for ticket in group.tickets.values())

    def remaining_cost(self,scope):
        group=self.groups.get(scope)
        opportunity=group.opportunity if group else None
        if opportunity is None or self.clock()>=opportunity.expires_at:return None
        # Only settled cost reduces the next candidate's estimate. Unknown
        # tickets remain held by _cost and the persistent attempt ledger.
        spent=sum(ticket.cost_micro or 0 for ticket in group.tickets.values()
                  if ticket.opportunity_id==opportunity.id)
        return max(0,opportunity.cost_micro-spent)

    def candidate(self, scope, *, reserve_cost_micro, message_id=None):
        if type(reserve_cost_micro) is not int or reserve_cost_micro < 0:
            raise ValueError('proactive_reservation_invalid')
        self.sweep()
        group = self.groups.get(scope)
        now = self.clock()
        if (group is None or not group.policy.available or group.addressed_active
                or group.active is not None or not group.rows
                or message_id is not None and message_id != group.target_id
                or self.start_delay(scope) > 0
                or now < group.last_delivery_at + group.policy.cooldown_seconds):
            return None
        deferred = group.deferred_at is not None
        if not group.dirty and not (deferred and now >= group.deferred_at):
            return None
        remaining=self.remaining_cost(scope)
        if remaining is not None:
            if not remaining:return None
            reserve_cost_micro=min(reserve_cost_micro,remaining)
        if (len(group.tickets) >= group.policy.max_calls_per_hour
                or len(group.replies) >= group.policy.max_replies_per_hour
                or self._cost(group) + reserve_cost_micro > group.policy.max_cost_micro_per_hour):
            return None
        opportunity=group.opportunity
        if opportunity is None or now>=opportunity.expires_at:
            if opportunity is not None and not group.dirty:return None
            opportunity=group.opportunity=_Opportunity(uuid4().hex,now+self.candidate_seconds,reserve_cost_micro)
        if opportunity.send_interrupted or opportunity.evaluations>=self.max_defer_rechecks+1:return None
        target=self.target(scope)
        if target is None:return None
        opportunity.evaluations+=1
        group.evaluation = group.evaluation + 1 if deferred else 1
        ticket = _Ticket(uuid4().hex, scope, group.version, group.policy_version,
                         group.policy.service_subject_id, now, opportunity.expires_at,
                         reserve_cost_micro, group.evaluation,opportunity_id=opportunity.id)
        group.tickets[ticket.candidate_id] = ticket
        group.active, group.deferred_at, group.dirty = ticket.candidate_id, None, False
        group.pending_since = None
        group.pending_opportunity = None
        messages=tuple(row.message for row in group.rows if row.message.message_id!=target.message_id)+(target,)
        return ProactiveCandidate(ticket.candidate_id, scope, ticket.version, ticket.policy_version,
                                  ticket.service_subject_id, messages,
                                  now, ticket.expires_at, reserve_cost_micro, deferred, group.evaluation,
                                  opportunity_id=opportunity.id)

    def opportunity_budget(self,candidate):
        group,ticket=self._ticket(candidate)
        opportunity=group.opportunity if group else None
        if ticket is None or opportunity is None or ticket.opportunity_id!=opportunity.id:
            raise ValueError('proactive_opportunity_expired')
        return ([row.candidate_id for row in group.tickets.values() if row.opportunity_id==opportunity.id],
                opportunity.cost_micro)

    def take_tool(self,candidate,limit):
        if not self.valid(candidate):return False
        opportunity=self.groups[candidate.scope].opportunity
        if opportunity.tools>=limit:return False
        opportunity.tools+=1
        return True

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
                    and self.clock() >= group.last_delivery_at + group.policy.cooldown_seconds
                    and len(group.replies) < group.policy.max_replies_per_hour
                    and self._cost(group) <= group.policy.max_cost_micro_per_hour)

    def can_send(self, candidate):
        if not self.valid(candidate):
            return False
        _, ticket = self._ticket(candidate)
        return ticket.decision == 'reply'

    def mark_send_started(self, candidate):
        """Keep the opportunity closed to replacements once intent has been granted."""
        self.groups[candidate.scope].send_started = True

    def settle(self, candidate, *, actual_cost_micro, held_cost_micro=None):
        """Unknown usage retains the attempt ledger bound, or the full opportunity hold."""
        _, ticket = self._ticket(candidate)
        return self._settle_cost(ticket, actual_cost_micro, held_cost_micro)

    def pending_costs(self, scope):
        """Billing identities only; never grants candidate or sending permission."""
        group = self.groups.get(scope)
        return tuple(key for key, ticket in group.tickets.items() if ticket.cost_micro is None) if group else ()

    def reconcile_cost(self, scope, candidate_id, *, actual_cost_micro, held_cost_micro=None):
        group = self.groups.get(scope)
        ticket = group.tickets.get(candidate_id) if group else None
        if ticket is None or ticket.cost_micro is not None:return False
        return self._settle_cost(ticket, actual_cost_micro, held_cost_micro)

    @staticmethod
    def _settle_cost(ticket, actual_cost_micro, held_cost_micro):
        for value in (actual_cost_micro,held_cost_micro):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError('proactive_cost_invalid')
        if ticket is None:
            return False
        if ticket.cost_micro is not None and actual_cost_micro not in (None, ticket.cost_micro):
            raise ValueError('proactive_cost_conflict')
        if actual_cost_micro is not None:
            ticket.cost_micro = actual_cost_micro
        if held_cost_micro is not None:ticket.held_cost_micro=held_cost_micro
        return True

    def decide(self, candidate, decision, *, actual_cost_micro=None, held_cost_micro=None, defer_seconds=2):
        if decision not in {'reply', 'skip', 'defer'}:
            raise ValueError('proactive_decision_invalid')
        if not 1 <= defer_seconds <= 5:
            raise ValueError('proactive_defer_invalid')
        if not self.settle(candidate, actual_cost_micro=actual_cost_micro,held_cost_micro=held_cost_micro):
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
        if group.active==ticket.candidate_id and group.version==ticket.version:
            group.send_started = False
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

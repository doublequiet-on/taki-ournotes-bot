# L3: Sanitized capability and group-service metadata for authenticated administration.
# Effects: Reads existing approval files; no probes, approval writes or message content.
from dataclasses import asdict
from ..domain.model_calls import ModelError


def status(runtime, group_id=None):
    result={'engine':'agent' if runtime.agent else 'legacy','profiles':[]}
    for role,config in (('primary',runtime.config),('fallback',runtime.fallback),('proactive',runtime.proactive_config)):
        item={'role':role,'configured':False,'capabilities':[]}
        try:
            snapshot=config.snapshot()
            if group_id:snapshot=snapshot.for_group(group_id)
            capabilities=runtime.proactive_capabilities if role=='proactive' else runtime.capabilities
            services=runtime.agent.proactive.services if role=='proactive' and runtime.agent else runtime.agent.services if runtime.agent else None
            item.update(configured=True,model=snapshot.profile.model,config_version=snapshot.profile.config_version)
            for kind in ('web','vision','proactive'):
                row={'kind':kind,'implemented':True,'approved':False,'verification':'not_probed'}
                try:
                    if kind=='proactive':
                        proof=capabilities.proactive(snapshot,group_id) if group_id else False
                        row['reason']='group_required' if group_id is None else 'approval_absent_or_profile_changed'
                    else:
                        proof=getattr(capabilities,kind)(snapshot)
                        row['reason']='approval_absent_or_profile_changed'
                    row['approved']=bool(proof)
                    if proof and kind!='proactive':row['evidence_ref']=proof.evidence_ref
                    row['eligible']=bool(proof) and runtime.agent is not None
                    if proof:row['reason']='approved' if runtime.agent else 'agent_disabled'
                except (ModelError,OSError) as exc:
                    row.update(eligible=False,reason=str(exc) if isinstance(exc,ModelError) else 'capability_read_failed')
                item['capabilities'].append(row)
            if group_id and runtime.agent:
                policy=services.policy(snapshot,group_id)
                item['services']={'reason':services.code,'approved':policy is not None}
                if policy:
                    item['services'].update(currency=policy.currency,summary=asdict(policy.summary),proactive=asdict(policy.proactive))
                proactive=next(c for c in item['capabilities'] if c['kind']=='proactive')
                if not policy or not policy.proactive.enabled:
                    proactive.update(eligible=False,reason=services.code if not policy else 'service_disabled')
                elif role=='primary' and runtime.dedicated_proactive():
                    proactive.update(eligible=False,reason='dedicated_proactive_model')
                elif max(policy.proactive.max_calls_per_hour,policy.proactive.max_replies_per_hour)>120:
                    proactive.update(eligible=False,reason='proactive_limits_unsupported')
        except ModelError as exc:item['reason']=str(exc)
        result['profiles'].append(item)
    return result

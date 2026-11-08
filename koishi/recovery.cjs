'use strict'
// Called by the measured task10 entry only after its two-role FD barrier.
// createGateway is the trusted local composition, never a Console/RPC argument.
const {setTimeout:sleep}=require('node:timers/promises')
async function serveRecoveryGateway(session, instance, createGateway) {
  if(session.binding?.version!==1||session.binding.role!=='gateway'||session.binding.instance!==instance)
    throw Error('recovery_entry_binding_mismatch')
  session.assertCurrent()
  let runtime
  try {
    runtime=await createGateway(session.assertCurrent)
    let phase=session.state()
    while(phase==='active'){await sleep(50);phase=session.state()}
    // Authorization is already denied while the backend drains its writers.
    while(phase==='quiesce'){await sleep(50);phase=session.state()}
    if(phase!=='drain')throw Error('recovery_stop_not_ordered')
    await runtime.close()
    if(runtime.recoveryDrained()!==true)throw Error('gateway_drain_unconfirmed')
    phase=session.report('drained',{gateway_drained:true})
    // Keep this role alive until the backend has sealed the ACK tail and closed.
    while(phase==='close'){await sleep(50);phase=session.state()}
    if(phase!=='stopped')throw Error('recovery_stop_not_ordered')
  } finally {
    // On loss/cancellation, local cleanup is mandatory but is not a host receipt.
    if(runtime)await runtime.close()
  }
}
module.exports={serveRecoveryGateway}

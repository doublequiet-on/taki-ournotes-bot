'use strict'
// Private inherited descriptor, not a listening service or a disk approval.
const fs = require('node:fs')
const { setTimeout: sleep } = require('node:timers/promises')

class ChildSession {
  constructor() {
    this.fd = Number(process.env.TAKI_RECOVERY_FD)
    this.binding = JSON.parse(process.env.TAKI_RECOVERY_BINDING)
    delete process.env.TAKI_RECOVERY_FD
    delete process.env.TAKI_RECOVERY_BINDING
    if (!Number.isInteger(this.fd) || this.fd < 3) throw new Error('recovery_fd_invalid')
    this.sequence = 0
    this.closed = false
    this.revoked = false
    this.assertCurrent = this.assertCurrent.bind(this)
  }
  request(op, facts = null) {
    if (this.closed) throw new Error('recovery_session_closed')
    try {
      const frame = Buffer.from(JSON.stringify({binding: this.binding, op, sequence: ++this.sequence, facts}) + '\n')
      let offset = 0
      while (offset < frame.length) offset += fs.writeSync(this.fd, frame, offset, frame.length - offset)
      const bytes = [], one = Buffer.alloc(1)
      while (bytes.length < 4096) {
        if (fs.readSync(this.fd, one, 0, 1, null) !== 1) throw new Error('recovery_disconnected')
        if (one[0] === 10) break
        bytes.push(one[0])
      }
      if (bytes.length >= 4096) throw new Error('recovery_frame_too_large')
      const reply = JSON.parse(Buffer.from(bytes).toString())
      if (Object.keys(reply).sort().join(',') !== 'sequence,state' || reply.sequence !== this.sequence) throw new Error('recovery_reply_invalid')
      return reply.state
    } catch (error) {
      this.closed = true
      fs.closeSync(this.fd)
      throw new Error('recovery_session_closed', {cause: error})
    }
  }
  async wait() {
    const deadline = Date.now() + 30000
    while (Date.now() < deadline) {
      const state = this.request('wait')
      if (state === 'active') return
      if (!['waiting', 'preparing'].includes(state)) break
      await sleep(20)
    }
    this.closed = true
    fs.closeSync(this.fd)
    throw new Error('recovery_barrier_timeout')
  }
  assertCurrent() {
    if (this.revoked || this.request('assert') !== 'active') {
      this.revoked = true
      throw new Error('recovery_session_revoked')
    }
  }
  state() { return this.request('state') }
  report(stage, facts) { return this.request(stage, facts) }
}
module.exports = { ChildSession }
if (require.main === module) {
  ;(async () => {
    if (process.argv.length !== 4 || process.argv[2] !== '--entry') throw new Error('trusted_entry_required')
    const session = new ChildSession()
    await session.wait()
    session.assertCurrent()
    await require(process.argv[3]).start(session)
  })().catch(error => { console.error(error.message); process.exitCode = 1 })
}

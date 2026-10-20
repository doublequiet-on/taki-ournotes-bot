// Bounded observation side channel. enqueue() never waits on HTTP or changes transport outcomes.
import { randomUUID } from 'node:crypto'
export interface Span {
  request_id: string; delivery_id: string; kind: 'upload' | 'send' | 'receipt_submit'
  position: number; attempt: string | null; duration_ms: number
}
export class Telemetry {
  private queue: (Span & { event_id: string })[] = []
  private timer: ReturnType<typeof setInterval>
  private pending?: Promise<void>
  dropped = 0
  private lastSuccess: number | null = null
  private lastFailure: number | null = null
  snapshot() { return { queued: this.queue.length, dropped_events: this.dropped,
    last_success_at: this.lastSuccess, last_failure_at: this.lastFailure,
    availability: this.dropped || this.lastFailure && (!this.lastSuccess || this.lastFailure > this.lastSuccess) ? 'partial' : 'available' } }
  constructor(private transmit: (events: unknown[]) => Promise<unknown>) {
    this.timer = setInterval(() => void this.flush(), 1000)
    this.timer.unref()
  }
  enqueue(span: Span) {
    if (this.queue.length >= 256) { this.dropped++; return }
    this.queue.push({ ...span, event_id: randomUUID() })
  }
  flush(): Promise<void> {
    if (this.pending) return this.pending
    const batch = this.queue.splice(0, 32)
    if (!batch.length) return Promise.resolve()
    this.pending = (async () => {
      // IDs and finite numeric fields bound each event; enforce bytes as well.
      if (Buffer.byteLength(JSON.stringify(batch)) > 24000) { this.dropped += batch.length; return }
      for (let attempt = 0; attempt < 2; attempt++) {
        try { await this.transmit(batch); this.lastSuccess = Date.now()/1000; return }
        catch { this.lastFailure = Date.now()/1000; if (attempt === 1) this.dropped += batch.length }
      }
    })().finally(() => { this.pending = undefined })
    return this.pending
  }
  async close() {
    clearInterval(this.timer)
    // Closing is bounded by the transmitter timeout. Unflushed records are explicitly lost.
    await this.pending
    await this.flush()
    this.dropped += this.queue.length
    this.queue = []
  }
}

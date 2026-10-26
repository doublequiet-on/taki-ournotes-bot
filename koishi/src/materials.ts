// L3: validated image bytes -> owned QQ material lease; never sends messages.
// Effects: bounded official metadata calls, credential-free COS PUT, private atomic ledger.
import { createHash, randomUUID } from 'node:crypto'
import { mkdir, open, readFile, rename } from 'node:fs/promises'
import { join } from 'node:path'

export interface MaterialLease { url: string; width: number; height: number; release(): void }
export interface MaterialWait { signal?: AbortSignal; deadline?: number }
export interface ImageMaterials { acquire(bytes: Uint8Array, wait?: MaterialWait): Promise<MaterialLease> }
export type MaterialAPI = (method: 'POST' | 'DELETE', path: string, data?: unknown) => Promise<any>
type Entry = { key: string; created: number; used: number; state: 'owned' | 'completion_unknown' | 'deletion_unknown'; imageId?: string; url?: string; uploadId?: string }
type Cold = { bytes: Uint8Array; shape: ReturnType<typeof imageShape>; key: string; valid(): boolean;
  resolve(value: MaterialLease): void; reject(error: unknown): void }
type StateWork = { run(): Promise<void> }
// Compatibility release A preserves v1 writers; release B changes only this floor.
const minimumSchema: 1 | 2 = 2
export class MaterialAborted extends Error { constructor() { super('material waiter inactive') } }
export class MaterialAPIError extends Error {
  constructor(readonly absent = false) { super('material API rejected') }
}
/** Preserve only the documented absent-ID fact, including HTTP-success business errors. */
export async function materialRequest(request: () => Promise<{data: any}>): Promise<any> {
  try {
    const response = await request()
    if(response.data?.code && response.data.code !== 0) throw new MaterialAPIError(response.data.code === 40035008)
    return response.data
  } catch(error) {
    if(error instanceof MaterialAPIError) throw error
    throw new MaterialAPIError((error as any)?.response?.data?.code === 40035008)
  }
}
const token = /^[A-Za-z0-9_-]{1,512}$/
const uri = /^qqbot-media:\/\/image\/v1\/[A-Za-z0-9_-]{1,1024}$/
const lifetime = 89 * 86400000

export function imageShape(bytes: Uint8Array): { width: number; height: number; type: string; ext: string } {
  const b = Buffer.from(bytes)
  let width = 0, height = 0, type = '', ext = ''
  if (b.length >= 24 && b.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])) && b.toString('ascii',12,16) === 'IHDR') {
    width=b.readUInt32BE(16); height=b.readUInt32BE(20); type='image/png'; ext='png'
  } else if (b[0]===255 && b[1]===216) {
    let i=2
    while (i+4<=b.length) {
      if (b[i++]!==255) break
      while (b[i]===255) i++
      const marker=b[i++]
      if (marker===217 || marker===218) break
      const size=b.readUInt16BE(i)
      if(size<2 || i+size>b.length) break
      if ([192,193,194].includes(marker) && size>=8) {
        height=b.readUInt16BE(i+3); width=b.readUInt16BE(i+5); type='image/jpeg'; ext='jpg'; break
      }
      i+=size
    }
  }
  if (!width || !height || width>16384 || height>16384 || width*height>40000000 || !bytes.length || bytes.length>3000000)
    throw Error('invalid material image')
  return {width,height,type,ext}
}

export async function putMaterial(url: string, bytes: Uint8Array, type: string): Promise<string> {
  const parsed=new URL(url)
  if (parsed.protocol!=='https:' || parsed.username || parsed.password || parsed.port && parsed.port!=='443'
    || !/^[a-zA-Z0-9-]+\.cos\.[a-z0-9-]+\.myqcloud\.com$/.test(parsed.hostname)) throw Error('invalid material upload host')
  // Deliberately separate from bot.http: no QQ headers, redirects or retries.
  const response=await fetch(url,{method:'PUT',headers:{'Content-Type':type},body:Buffer.from(bytes),
    redirect:'error',signal:AbortSignal.timeout(15000)})
  await response.body?.cancel()
  if (!response.ok) throw Error('material PUT rejected')
  return (response.headers.get('etag') || '').replace(/^"|"$/g,'').toLowerCase()
}

export class MaterialStore implements ImageMaterials {
  private stateQueue: StateWork[] = []
  private stateRunning?: Promise<void>
  private queue: Cold[] = []
  private running?: Promise<void>
  private calls = new Set<Promise<MaterialLease>>()
  private cancelWaiters = new Set<()=>void>()
  private closing = false
  private closed?: Promise<void>
  private schema: 1 | 2 = minimumSchema
  private entries?: Entry[]
  private active=new Map<string,number>()
  private blocked=false
  private pending=0
  private pendingBytes=0
  private reserved=0
  private uploading?: string
  private coldPhase: 'idle' | 'checking' | 'prepare' | 'put' | 'complete' | 'delete' = 'idle'
  private file: string
  constructor(private directory: string, private appId: string, private api: MaterialAPI,
    private put=putMaterial, private now=Date.now,
    private limits: Readonly<{maxPending:number;maxBytes:number}>={maxPending:8,maxBytes:24_000_000}) {
    if(!Number.isInteger(limits.maxPending) || limits.maxPending<1 || limits.maxPending>8
      || !Number.isInteger(limits.maxBytes) || limits.maxBytes<1 || limits.maxBytes>24_000_000)
      throw Error('invalid material queue limits')
    this.limits=Object.freeze({...limits})
    this.file=join(directory,createHash('sha256').update(appId).digest('hex')+'.json')
  }
  private state<T>(operation:()=>Promise<T>, cancellable?:(cancel:()=>void)=>void): Promise<T> {
    return new Promise<T>((resolve,reject)=>{
      const item:StateWork={run:async()=>{
        try {resolve(await operation())}catch(error){reject(error)}
      }}
      this.stateQueue.push(item)
      // Only an initial lookup opts in. Never detach a running mutation or a
      // late ownership commit from the serialized state owner.
      cancellable?.(()=>{
        const index=this.stateQueue.indexOf(item)
        if(index>=0) {this.stateQueue.splice(index,1);reject(new MaterialAborted())}
      })
      this.pumpState()
    })
  }
  private pumpState() {
    if(this.stateRunning) return
    const item=this.stateQueue.shift()
    if(!item) return
    this.stateRunning=Promise.resolve().then(()=>item.run()).finally(()=>{
      this.stateRunning=undefined;this.pumpState()
    })
  }
  private available() {
    if(this.blocked) throw Error('material ledger requires recovery')
  }
  private async save() {
    const tmp=this.file+'.'+randomUUID()+'.tmp'
    try {
      await mkdir(this.directory,{recursive:true,mode:0o700})
      const entries=this.entries!.map(({state,...entry})=>this.schema===1?entry:{...entry,state})
      if(this.schema===1 && this.entries!.some(e=>e.state==='deletion_unknown')) throw Error('material schema downgrade')
      const file=await open(tmp,'wx',0o600)
      try { await file.writeFile(JSON.stringify({schema:this.schema,appId:this.appId,entries}));await file.sync() }
      finally { await file.close() }
      await rename(tmp,this.file)
      if(process.platform!=='win32') {
        const directory=await open(this.directory,'r')
        try { await directory.sync() } finally { await directory.close() }
      }
    } catch(error) { this.blocked=true; throw error }
  }
  private async load() {
    if (this.entries) return
    try {
      const raw=await readFile(this.file,'utf8')
      if (raw.length>200000) throw Error('material ledger size')
      const state=JSON.parse(raw)
      if(![1,2].includes(state.schema) || state.appId!==this.appId || !Array.isArray(state.entries) || state.entries.length>64
        || state.entries.some((e:Entry)=>!e || typeof e.key!=='string' || !/^[a-f0-9]{64}$/.test(e.key) || !Number.isFinite(e.created)
          || !Number.isFinite(e.used) || e.used<e.created || e.used>this.now()+300000
          || Object.keys(e).some(k=>!['key','created','used','imageId','url','uploadId',...(state.schema===2?['state']:[])].includes(k))
          || (e.imageId!==undefined && (typeof e.imageId!=='string' || !token.test(e.imageId) || typeof e.url!=='string' || !uri.test(e.url)))
          || (e.uploadId!==undefined && (typeof e.uploadId!=='string' || !token.test(e.uploadId)))
          || (e.imageId===undefined && (!e.uploadId || e.url!==undefined))
          || (state.schema===2 && (!['owned','completion_unknown','deletion_unknown'].includes(e.state)
            || (e.state==='completion_unknown' ? !e.uploadId || e.imageId!==undefined || e.url!==undefined : !e.imageId))))) throw Error('invalid material ledger')
      if(new Set(state.entries.map((e:Entry)=>e.key)).size!==state.entries.length) throw Error('duplicate material ownership')
      this.schema=Math.max(minimumSchema,state.schema) as 1 | 2
      this.entries=state.entries.map((e:Entry)=>({...e,state:state.schema===1?(e.imageId?'owned':'completion_unknown'):e.state}))
    } catch(error) {
      if((error as NodeJS.ErrnoException).code!=='ENOENT') {this.blocked=true;throw error}
      this.entries=[]
    }
  }
  diagnostics() {
    const entries=this.entries ?? [],count=(state:Entry['state'])=>entries.filter(e=>e.state===state).length
    const unresolved=entries.filter(e=>e.state!=='owned')
    return { schema:this.schema, loaded:!!this.entries, blocked:this.blocked, closing:this.closing,
      owned:count('owned'), completion_unknown:count('completion_unknown'), deletion_unknown:count('deletion_unknown'),
      capacity:64, occupied:entries.length+this.reserved, pending:this.pending, pending_bytes:this.pendingBytes,
      queued:this.queue.length, running:!!this.running, cold_phase:this.coldPhase,
      state_queued:this.stateQueue.length, state_active:!!this.stateRunning,
      oldest_unresolved_entry_age_ms:unresolved.length?Math.max(0,this.now()-Math.min(...unresolved.map(e=>e.created))):null }
  }
  private lease(entry:Entry, shape:{width:number;height:number}): MaterialLease {
    const key=entry.key
    this.active.set(key,(this.active.get(key)||0)+1)
    let released=false
    return {url:entry.url!,width:shape.width,height:shape.height,release:()=>{
      if(released) return
      released=true
      const remaining=this.active.get(key)!-1
      if(remaining) this.active.set(key,remaining); else this.active.delete(key)
    }}
  }
  private async reuse(entry:Entry, shape:{width:number;height:number}) {
    entry.used=this.now()
    await this.save()
    return this.lease(entry,shape)
  }
  acquire(bytes: Uint8Array, wait: MaterialWait = {}): Promise<MaterialLease> {
    const {signal,deadline}=wait
    const valid=()=>!this.closing && !signal?.aborted && (deadline===undefined || this.now()<deadline)
    if(deadline!==undefined && !Number.isFinite(deadline)) return Promise.reject(Error('invalid material deadline'))
    if(!valid()) return Promise.reject(new MaterialAborted())
    if(this.pending>=this.limits.maxPending || bytes.length>this.limits.maxBytes-this.pendingBytes)
      return Promise.reject(Error('material queue capacity'))
    const captured=Uint8Array.from(bytes), shape=imageShape(captured)
    const key=createHash('sha256').update(captured).digest('hex')
    this.pending++;this.pendingBytes+=captured.length
    let queued:Cold|undefined
    let cancelLookup:(()=>void)|undefined
    const cancel=()=>{
      cancelLookup?.()
      const index=queued?this.queue.indexOf(queued):-1
      if(index>=0) { this.queue.splice(index,1);queued!.reject(new MaterialAborted()) }
    }
    this.cancelWaiters.add(cancel)
    signal?.addEventListener('abort',cancel,{once:true})
    const remaining=deadline===undefined?undefined:deadline-this.now()
    const timeout=remaining===undefined || remaining>2147483647?undefined:setTimeout(cancel,Math.max(0,remaining))
    timeout?.unref()
    const task=(async()=>{
      const cached=await this.state(async()=>{
        this.available()
        await this.load()
        if(!valid()) throw new MaterialAborted()
        const entry=this.entries!.find(e=>e.key===key)
        if(entry && entry.state!=='owned') {
          if(entry.state==='completion_unknown' && this.uploading===key) return undefined
          throw Error('material ownership unresolved')
        }
        return entry && this.now()-entry.used<lifetime ? this.reuse(entry,shape) : undefined
      },cancel=>{cancelLookup=cancel})
      cancelLookup=undefined
      if(cached) {
        if(!valid()) {cached.release();throw new MaterialAborted()}
        return cached
      }
      // Only cold work is serialized across network waits. Every waiter obtains
      // its own lease after rechecking the published entry.
      if(!valid()) throw new MaterialAborted()
      return new Promise<MaterialLease>((resolve,reject)=>{
        queued={bytes:captured,shape,key,valid,resolve,reject}
        this.queue.push(queued);this.pump()
      })
    })()
    const tracked=task.finally(()=>{
      this.pending--;this.pendingBytes-=captured.length
      signal?.removeEventListener('abort',cancel)
      this.cancelWaiters.delete(cancel)
      if(timeout) clearTimeout(timeout)
      this.calls.delete(tracked)
    })
    this.calls.add(tracked)
    return tracked
  }
  private pump() {
    if(this.running || this.closing) return
    const item=this.queue.shift()
    if(!item) return
    this.running=this.upload(item.bytes,item.shape,item.key,item.valid).then(lease=>{
      if(!item.valid()) { lease.release();throw new MaterialAborted() }
      return lease
    }).then(item.resolve,item.reject).finally(()=>{this.running=undefined;this.pump()})
  }
  close():Promise<void> {
    if(!this.closed) {
      this.closing=true
      for(const cancel of this.cancelWaiters) cancel()
      this.closed=(async()=>{
        await Promise.allSettled([...this.calls])
        await this.running
        await this.state(async()=>undefined)
        await this.stateRunning
      })()
    }
    return this.closed
  }
  private async upload(bytes:Uint8Array, shape:{width:number;height:number;type:string;ext:string}, key:string, valid:()=>boolean): Promise<MaterialLease> {
    let reserved=false
    this.uploading=key
    this.coldPhase='checking'
    try {
      const cached=await this.state(async()=>{
        this.available()
        if(!valid()) throw new MaterialAborted()
        const entries=this.entries!, now=this.now()
        let entry=entries.find(e=>e.key===key)
        if(entry && (entry.state!=='owned' || now-entry.used>=lifetime)) {
          if(entry.state!=='owned') throw Error('material ownership unresolved')
          if(this.active.has(key)) throw Error('material still leased')
          await this.remove(entry,valid);entry=undefined
        }
        if(entry) return this.reuse(entry,shape)
        // Reclamation remains serialized with state writes and hits, including
        // DELETE, so no new lease can race an outstanding deletion.
        while(entries.length+this.reserved>=64) {
          if(!valid()) throw new MaterialAborted()
          const old=entries.filter(e=>!this.active.has(e.key) && now-e.used>300000 && e.state==='owned')
            .sort((a,b)=>a.used-b.used)[0]
          if(!old) throw Error('owned material capacity')
          await this.remove(old,valid)
        }
        this.reserved++;reserved=true
      })
      if(cached) return cached
      this.available()
      if(!valid()) throw new MaterialAborted()
      const md5=createHash('md5').update(bytes).digest('hex')
      this.coldPhase='prepare'
      const prepared=await this.api('POST','/v2/media-images/prepare',{file_name:key+'.'+shape.ext,
        file_size:bytes.length,content_type:shape.type,md5,sha1:createHash('sha1').update(bytes).digest('hex')})
      if(!token.test(prepared?.upload_id || '') || typeof prepared.presigned_url!=='string') throw Error('invalid material prepare')
      this.available()
      this.coldPhase='put'
      const etag=await this.put(prepared.presigned_url,bytes,shape.type)
      if(etag!==md5) throw Error('material ETag mismatch')
      const entry=await this.state(async()=>{
        this.available()
        const now=this.now(), entry:Entry={key,created:now,used:now,state:'completion_unknown',uploadId:prepared.upload_id}
        this.entries!.push(entry);this.reserved--;reserved=false
        // Persist ownership uncertainty before complete; cache hits may proceed
        // during complete, but they serialize their full snapshots through state().
        await this.save()
        return entry
      })
      this.available()
      this.coldPhase='complete'
      const completed=await this.api('POST','/v2/media-images/complete',{upload_id:prepared.upload_id,md5:etag})
      if(!token.test(completed?.image_id || '') || !uri.test(completed?.url || '')) throw Error('invalid material completion')
      return await this.state(async()=>{
        entry.imageId=completed.image_id;entry.url=completed.url;entry.used=this.now();entry.state='owned'
        // A different hit may have failed to save while complete was in flight.
        // Retain this returned ownership fact, but a blocked store grants no lease.
        await this.save()
        this.available()
        return this.lease(entry,shape)
      })
    } finally {
      this.uploading=undefined
      this.coldPhase='idle'
      if(reserved) await this.state(async()=>{this.reserved--})
    }
  }
  private async remove(entry:Entry,valid:()=>boolean) {
    this.available()
    if(!valid()) throw new MaterialAborted()
    this.coldPhase='delete'
    if(this.schema===2) {
      entry.state='deletion_unknown'
      await this.save()
      this.available()
      if(!valid()) {
        // No DELETE was attempted, so its pre-action marker can be safely undone.
        entry.state='owned'
        await this.save()
        throw new MaterialAborted()
      }
    }
    try { await this.api('DELETE','/v2/media-images/'+entry.imageId+'/delete') }
    catch(error) {
      // Only this documented absent-ID response confirms a previously deleted item.
      if(!(error instanceof MaterialAPIError && error.absent) && (error as any)?.response?.data?.code!==40035008) throw error
    }
    this.entries!.splice(this.entries!.indexOf(entry),1)
    await this.save()
  }
}

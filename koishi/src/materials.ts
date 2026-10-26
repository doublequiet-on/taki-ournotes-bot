// L3: validated image bytes -> owned QQ material lease; never sends messages.
// Effects: bounded official metadata calls, credential-free COS PUT, private atomic ledger.
import { createHash, randomUUID } from 'node:crypto'
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

export interface MaterialLease { url: string; width: number; height: number; release(): void }
export interface ImageMaterials { acquire(bytes: Uint8Array): Promise<MaterialLease> }
export type MaterialAPI = (method: 'POST' | 'DELETE', path: string, data?: unknown) => Promise<any>
type Entry = { key: string; created: number; used: number; imageId?: string; url?: string; uploadId?: string }
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
  private tail: Promise<unknown> = Promise.resolve()
  private entries?: Entry[]
  private active=new Map<string,number>()
  private blocked=false
  private file: string
  constructor(private directory: string, private appId: string, private api: MaterialAPI,
    private put=putMaterial, private now=Date.now) {
    this.file=join(directory,createHash('sha256').update(appId).digest('hex')+'.json')
  }
  private async save() {
    const tmp=this.file+'.'+randomUUID()+'.tmp'
    await mkdir(this.directory,{recursive:true,mode:0o700})
    await writeFile(tmp,JSON.stringify({schema:1,appId:this.appId,entries:this.entries}),{mode:0o600,flag:'wx'})
    await rename(tmp,this.file)
  }
  private async load() {
    if (this.entries) return
    try {
      const raw=await readFile(this.file,'utf8')
      if (raw.length>200000) throw Error('material ledger size')
      const state=JSON.parse(raw)
      if(state.schema!==1 || state.appId!==this.appId || !Array.isArray(state.entries) || state.entries.length>64
        || state.entries.some((e:Entry)=>!e || !/^[a-f0-9]{64}$/.test(e.key) || !Number.isFinite(e.created)
          || !Number.isFinite(e.used) || e.used<e.created || e.used>this.now()+300000
          || (e.imageId!==undefined && (!token.test(e.imageId) || !uri.test(e.url || ''))))) throw Error('invalid material ledger')
      this.entries=state.entries
    } catch(error) {
      if((error as NodeJS.ErrnoException).code!=='ENOENT') throw error
      this.entries=[]
    }
  }
  acquire(bytes: Uint8Array): Promise<MaterialLease> {
    const captured=Uint8Array.from(bytes), shape=imageShape(captured)
    const task=this.tail.then(async()=>{
      if(this.blocked) throw Error('material ledger requires recovery')
      await this.load()
      const entries=this.entries!, now=this.now()
      const key=createHash('sha256').update(captured).digest('hex')
      let entry=entries.find(e=>e.key===key)
      if(entry && (!entry.imageId || now-entry.used>=lifetime)) {
        if(!entry.imageId) throw Error('material completion unresolved')
        await this.remove(entry); entry=undefined
      }
      if(!entry) {
        // Reserve capacity for other features. Never list or delete foreign materials.
        while(entries.length>=64) {
          const old=entries.filter(e=>!this.active.has(e.key) && now-e.used>300000 && e.imageId)
            .sort((a,b)=>a.used-b.used)[0]
          if(!old) throw Error('owned material capacity')
          await this.remove(old)
        }
        const md5=createHash('md5').update(captured).digest('hex')
        const prepared=await this.api('POST','/v2/media-images/prepare',{file_name:key+'.'+shape.ext,
          file_size:captured.length,content_type:shape.type,md5,sha1:createHash('sha1').update(captured).digest('hex')})
        if(!token.test(prepared?.upload_id || '') || typeof prepared.presigned_url!=='string') throw Error('invalid material prepare')
        const etag=await this.put(prepared.presigned_url,captured,shape.type)
        if(etag!==md5) throw Error('material ETag mismatch')
        // Persist reservation BEFORE complete. A lost completion response may own a
        // material with unknown ID; retain its capacity slot and never guess/delete it.
        entry={key,created:now,used:now,uploadId:prepared.upload_id}; entries.push(entry)
        await this.save()
        const completed=await this.api('POST','/v2/media-images/complete',{upload_id:prepared.upload_id,md5:etag})
        if(!token.test(completed?.image_id || '') || !uri.test(completed?.url || '')) throw Error('invalid material completion')
        entry.imageId=completed.image_id; entry.url=completed.url
      }
      entry.used=now
      try { await this.save() } catch(error) { this.blocked=true; throw error }
      this.active.set(key,(this.active.get(key)||0)+1)
      let released=false
      return {url:entry.url!,width:shape.width,height:shape.height,release:()=>{
        if(released) return
        released=true
        const remaining=this.active.get(key)!-1
        if(remaining) this.active.set(key,remaining); else this.active.delete(key)
      }}
    })
    this.tail=task.catch(()=>undefined)
    return task
  }
  private async remove(entry:Entry) {
    try { await this.api('DELETE','/v2/media-images/'+entry.imageId+'/delete') }
    catch(error) {
      // Only this documented absent-ID response confirms a previously deleted item.
      if((error as any)?.response?.data?.code!==40035008) throw error
    }
    this.entries!.splice(this.entries!.indexOf(entry),1)
    try { await this.save() } catch(error) { this.blocked=true; throw error }
  }
}

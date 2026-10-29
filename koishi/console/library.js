// L3: Knowledge source inventory, review metadata and explicit full-snapshot publication.
import { h } from './runtime.js';
import { button, stamp } from './views.js';
const sourceKind=v=>({official_original:'官方原文',official_summary:'官方摘要',translation:'译文',fan_analysis:'爱好者分析',synthetic_fixture:'合成验收资料'})[v]||v;
const review=v=>({approved:'已审核',draft:'草稿',withdrawn:'已撤回',collected:'已采集',missing_source:'缺来源',pending_validation:'待核验',unavailable:'不可用'})[v]||v||'未知';
export function libraryView(s){
 const v=s.library.value,p=s.libraryPreview.value,kind=s.libraryKind.value,story=kind==='story';
 const previewStale=p&&JSON.stringify(p.guard)!==JSON.stringify(v?.guard);
 const blocked=s.busy.value||s.stale.value||!v?.guard||!s.guard.value?.gateway_epoch;
 const withdraw=row=>{const reason=window.prompt('撤回原因（会停止使用该资料，相关在途回答会在证据核验时失效）');if(!reason?.trim())return;if(window.confirm(`确认撤回“${row.title||row.id}”？`))void s.libraryAction('withdraw',{id:row.id,reason:reason.trim()});};
 return h('section',{class:'library-management'},[h('h2','知识资料管理'),h('p','ON 百科与剧情资料分别管理；群聊天记忆在群管理中查看。'),
 h('div',{class:'filter-tabs'},[['knowledge','已审核 ON 百科'],['story','剧情与派生人格']].map(([key,label])=>h('button',{'aria-pressed':kind===key,disabled:s.busy.value,onClick:()=>s.selectLibrary(key)},label))),
 button('刷新资料状态',()=>s.refreshLibrary(v?.offset||0),s.busy.value),
 !v?h('p','正在读取资料状态'):v.availability==='unavailable'?h('p','此资料库尚未配置。'):h('div',[
 h('p',`版本 ${v.revision||'未发布'} · ${v.total} 项来源／文档 · ${stamp(v.observed_at)}`),
 v.summary&&h('p',`可用已审核文档 ${v.summary.approved_documents} · 索引片段 ${v.summary.indexed_chunks} · ${v.summary.code==='ready'?'索引就绪':v.summary.code}`),
 h('ul',{class:'record-list'},(v.items||[]).map(row=>h('li',[h('strong',row.title||row.id),h('p',`${row.id} · ${story?(row.withdrawn?'已撤回':review(row.coverage)):(row.published?'已发布':review(row.review)+' · 当前未生效')}`),
 story?h('p',`${sourceKind(row.kind)} · 来源版本 ${row.version} · 场景 ${row.scene_count} · 发布 ${row.published_at||'未知'} · 采集 ${row.acquired_at||'未知'}`):h('p',`区服 ${(row.servers||[]).join(' / ')} · 审核 ${row.reviewed_by||'未知'} · ${row.reviewed_at||'未知'}`),
 story?h('p',row.uri):h('ul',(row.sources||[]).map(source=>h('li',`${source.kind} · ${source.uri} · ${source.locator}`))),
 row.source_count>3&&h('small',`当前展示 3 / ${row.source_count} 个来源，完整记录见资料包。`),row.confirmation_ref&&h('p','人工确认编号：'+row.confirmation_ref),row.withdrawal&&h('p',`撤回 ${row.withdrawal.withdrawn_at} · ${row.withdrawal.reason}`),row.scope&&h('p',`适用说明：${row.scope.scope_note} · 游戏版本 ${row.scope.game_version||'未知'} · ${row.scope.valid_from||'未知'} 至 ${row.scope.valid_to||'未知'}`),
 button('撤回此资料',()=>withdraw(row),blocked||(story?row.withdrawn:!row.published))]))),
 !(v.items||[]).length&&h('p','暂无资料。'),
 v.next_offset!=null&&button('下一页资料',()=>s.refreshLibrary(v.next_offset),s.busy.value),v.offset>0&&button('返回第一页',()=>s.refreshLibrary(),s.busy.value),
 story&&h('details',[h('summary','已存版本与人格审核状态'),h('p',v.persona?`当前人格：${v.persona.persona_id} · 已独立批准`:'当前没有生效的独立人格批准'),
 h('p',`派生结论：已审核 ${v.claim_counts?.approved||0} · 草稿 ${v.claim_counts?.draft||0}`),...(v.personas||[]).map(row=>h('p',`${row.id} · ${review(row.review)}（资料包内状态）`)),
 v.version_count>50&&h('p',`当前展示 50 / ${v.version_count} 个已存版本，更早资料可用本地工具检查。`),...(v.versions||[]).map(revision=>h('p',[h('code',revision),button('比较此版本',()=>s.libraryAction('compare',{revision}),blocked)])),h('small','旧版本比较沿用当前撤回记录；发布资料包不会批准人格。')])]),
 h('details',[h('summary','导入资料与版本差异'),h('p',story?'选择 taki-story-bundle/1 JSON 资料包，保留来源层级与派生材料审核状态。':'选择 taki-console-knowledge/1 JSON，包含 files（相对 Markdown 文件名到正文）、approvals 和 delivery，使用内容方已提供的批准与交付记录。'),
 h('p','整库快照导入会移除候选中缺失的旧资料。浏览器入口限制 18 KB；更大的资料包使用现有本地导入工具。'),
 h('label',['资料包 JSON',h('textarea',{'aria-label':'资料包 JSON',rows:8,disabled:s.busy.value,value:s.libraryText.value,onInput:e=>{s.libraryText.value=e.target.value;s.libraryPreview.value=null;s.libraryConfirmed.value=false;}})]),
 h('label',['选择 JSON 文件',h('input',{type:'file',accept:'.json,application/json',disabled:s.busy.value,onChange:async e=>{const file=e.target.files?.[0];if(!file)return;if(file.size>18000){s.message.value='资料包超过 18 KB，请使用本地导入工具。';return;}const input=e.target,selectedKind=s.libraryKind.value,content=await file.text();if(!input.isConnected||!s.logged.value||selectedKind!==s.libraryKind.value||input.files?.[0]!==file)return;s.libraryText.value=content;s.libraryPreview.value=null;s.libraryConfirmed.value=false;}})]),
 h('small','导入文本仅保留在当前管理会话，刷新后请重新选择文件。'),button('校验并预览差异',()=>s.libraryAction('preview'),blocked||!s.libraryText.value.trim()||new TextEncoder().encode(s.libraryText.value).length>18000)]),
 p&&h('section',[h('h3','待确认的版本差异'),previewStale&&h('p',{role:'alert'},'正式资料版本已变化，请重新生成差异预览。'),h('p',`候选版本 ${p.candidate_revision} · 共 ${p.changes.total_changes} 项变更`),h('p',`新增 ${p.changes.added.join('、')||'无'}；移除 ${p.changes.removed.join('、')||'无'}；修改 ${p.changes.modified.join('、')||'无'}`),
 h('p','校验只确认格式和已有审核记录匹配。请核对正文、来源、适用范围及全部移除项。'),h('pre',{class:'library-diff'},p.changes.diff||'没有内容差异'),
 p.changes.truncated&&h('p',{role:'alert'},'差异超过浏览器完整审阅范围，不能从此预览发布；请使用本地审核流程。'),
 h('label',{class:'toggle'},[h('input',{type:'checkbox',disabled:blocked||previewStale||!p.preview_id,checked:s.libraryConfirmed.value,onChange:e=>s.libraryConfirmed.value=e.target.checked}),'我已审阅差异并确认替换正式资料集']),
 button('确认发布此版本',()=>{if(window.confirm('确认发布已预览的完整资料集？现有回答的证据会重新核验。'))void s.libraryAction('publish');},blocked||previewStale||!p.preview_id||!s.libraryConfirmed.value),h('small','预览 10 分钟有效，服务重启或版本变化后需重新预览；结果未确认时先刷新核对。')])]);
}

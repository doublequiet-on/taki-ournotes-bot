import { costsView, costAlerts } from './costs.js';
import { h, ref, watch, onMounted, onUnmounted, resolveComponent } from './runtime.js';
import { sessionState } from './state.js';
import { button, overviewView, requestsView, groupsView, settingsView } from './views.js';
import { attentionView, sourcesView, operationsSettings } from './ops.js';
import { modelOverview, modelSettings, trustedView } from './model.js';
const navigation=[['overview','总览','home'],['requests','请求记录','list'],['costs','模型与费用','database'],['groups','群管理','groups'],['sources','数据来源','database'],['settings','设置','settings']];
const iconPaths={home:'m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z',list:'M9 5h12M9 12h12M9 19h12M3 5h.01M3 12h.01M3 19h.01',groups:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',database:'M21 5c0 2-4 3-9 3S3 7 3 5s4-3 9-3 9 1 9 3ZM3 5v14c0 2 4 3 9 3s9-1 9-3V5M3 12c0 2 4 3 9 3s9-1 9-3',settings:'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1ZM16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',menu:'M4 6h16M4 12h16M4 18h16',logout:'M9 3H3v18h6M9 12h12m-5-5 5 5-5 5',sun:'M12 2v2M12 20v2M2 12h2M20 12h2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0'};
const icon=name=>h('svg',{viewBox:'0 0 24 24',width:20,height:20,fill:'none',stroke:'currentColor','stroke-width':1.7,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'},[h('path',{d:iconPaths[name]||iconPaths.list})]);
const portrait=className=>h('img',{class:className,src:new URL('./brand/taki.png',import.meta.url).href,alt:''});
const recoveryKey=Symbol.for('taki.console.recovery.v1');
const recovery=globalThis[recoveryKey]||=( {current:null,snapshot:null} );
const storageKey='taki.console.recovery.v1';
let recoveryReadError=false;
function storeRecovery(snapshot){
  try {const hasEdits=snapshot.global||snapshot.group||snapshot.model||snapshot.jobs.length||snapshot.controls.length||snapshot.probes.length||snapshot.trusted.length;
    if(!hasEdits){sessionStorage.removeItem(storageKey);return true;}
    const value=JSON.stringify(snapshot);if(value.length>65536)return false;sessionStorage.setItem(storageKey,value);return true;}catch{return false;}
}
function takeRecovery(){
  try {const value=sessionStorage.getItem(storageKey);if(!value)return null;if(value.length>65536)throw Error('recovery too large');
    const snapshot=JSON.parse(value),record=v=>!!v&&typeof v==='object'&&!Array.isArray(v),edit=v=>v===null||(record(v)&&record(v.draft));
    if(!record(snapshot)||snapshot.schema!==1||typeof snapshot.notice!=='string'||!edit(snapshot.global)||!edit(snapshot.group)||!edit(snapshot.model)
      ||snapshot.global&&!Array.isArray(snapshot.global.draft.groupAllowlist)
      ||snapshot.group&&typeof snapshot.group.id!=='string'||snapshot.model&&!record(snapshot.model.draft.price)
      ||!['jobs','controls','probes','trusted'].every(key=>Array.isArray(snapshot[key])&&snapshot[key].every(item=>Array.isArray(item)&&item.length===2)))throw Error('invalid recovery');
    return snapshot;
  }catch{recoveryReadError=true;return null;}
}
function clearStoredRecovery(){try{sessionStorage.removeItem(storageKey);}catch{}}
function clearRecovery(){clearStoredRecovery();recovery.snapshot=null;}
function releaseState(state){state.suspend();recovery.snapshot=state.captureEdits();storeRecovery(recovery.snapshot);if(recovery.current===state)recovery.current=null;}
const Page={setup() {
  if(recovery.current)releaseState(recovery.current);
  const s=sessionState();s.restoreEdits(recovery.snapshot||takeRecovery());recovery.snapshot=null;recovery.current=s;
  const media=window.matchMedia('(max-width: 760px)');
  const password=ref(''),tab=ref('overview'),drawer=ref(false),groupOpen=ref(false),narrow=ref(media.matches),theme=ref(''),storageAvailable=ref(true),recoveryWarning=ref(recoveryReadError),params={};recoveryReadError=false;
  try{theme.value=localStorage.getItem('taki.console.theme')||'';}catch{}
  let timer,ticks=0,currentRequest=null,polling=false,inspectorTrigger=null,focusedRequest=null;
  const open=id => {inspectorTrigger=document.activeElement;focusedRequest=null;if(currentRequest!==id)s.detail.value=null;currentRequest=id;selectTab('requests');const url=new URL(window.location.href);url.searchParams.set('request_id',id);window.history.replaceState(null,'',url);void s.load(s.detail,'requests/detail',id);};
  const search=attention => {const p={attention:String(attention)};if(params.state)p.state=params.state;if(params.group_id)p.group_id=params.group_id;
    delete params.state;if(p.state)params.state=p.state;params.attention=p.attention;delete params.cursor;void s.load(s.requests,'requests/list',p);};
  const returnFocus=()=>{if(narrow.value)requestAnimationFrame(()=>{if(inspectorTrigger?.isConnected)inspectorTrigger.focus();else document.querySelector('.taki-console .page-panel')?.focus();});};
  const closeDetail=()=>{currentRequest=null;s.detail.value=null;const url=new URL(window.location.href);url.searchParams.delete('request_id');window.history.replaceState(null,'',url);returnFocus();};
  const closeGroup=()=>{groupOpen.value=false;returnFocus();};
  const closeDrawer=()=>{drawer.value=false;requestAnimationFrame(()=>document.querySelector('.taki-console .menu-button')?.focus());};
  const openDrawer=()=>{drawer.value=true;requestAnimationFrame(()=>document.querySelector('.console-sidebar nav [aria-current="page"]')?.focus());};
  const openGroup=id=>{inspectorTrigger=document.activeElement;s.selectGroup(id);if(s.groupId.value===id){groupOpen.value=true;if(narrow.value)requestAnimationFrame(()=>document.querySelector('.group-inspector [aria-label="关闭群详情"]')?.focus());}};
  const selectTab=key=>{const wasOpen=drawer.value;if(tab.value!==key){tab.value=key;const layout=document.querySelector('.taki-console')?.parentElement;if(layout)layout.scrollTop=0;}drawer.value=false;if(key==='groups'&&s.groupDirty.value)groupOpen.value=true;if(wasOpen)requestAnimationFrame(()=>{const target=narrow.value&&key==='groups'&&groupOpen.value?'.group-inspector [aria-label="关闭群详情"]':narrow.value&&key==='requests'&&currentRequest&&s.detail.value?'.request-inspector [aria-label="关闭查询详情"]':'.taki-console .page-panel';document.querySelector(target)?.focus();});if(key==='requests'&&!s.requests.value)search(false);if(['groups','settings'].includes(key)){void s.load(s.model,'model/settings');void s.load(s.trusted,'ops/trusted');}};
  s.navigate=selectTab;
  const toggleTheme=()=>{theme.value=(theme.value||(document.documentElement.classList.contains('dark')?'dark':'light'))==='dark'?'light':'dark';try{localStorage.setItem('taki.console.theme',theme.value);}catch{}};
  const connection=()=>{const health=s.overview.value?.health;return !health?'尚未采集':health.stale?'状态陈旧':health.ready?'已连接':'状态待核对';};
  async function poll() {
    if(!s.logged.value||document.hidden||polling)return;
    polling=true;ticks++;
    try {
      if(ticks%3===1)await s.load(s.overview,'overview');
      if(tab.value==='requests') {
        if(!s.requests.value)search(false);
        if(currentRequest&&!['complete','failed','closed_unsent'].includes(s.detail.value?.sections?.request?.data?.delivery_state))await s.load(s.detail,'requests/detail',currentRequest);
      }
      if(ticks%3===1)await Promise.all([s.load(s.jobs,'ops/jobs/list'),s.load(s.controls,'ops/controls')]);
      if(ticks%3===1&&['settings','groups'].includes(tab.value))await Promise.all([s.load(s.model,'model/settings'),s.load(s.trusted,'ops/trusted')]);
      if(tab.value==='sources'&&s.jobDetail.value&&['queued','running'].includes(s.jobDetail.value.state))await s.load(s.jobDetail,'ops/jobs/detail',s.jobDetail.value.id);
      if(ticks%12===1)await Promise.all([s.load(s.sources,'sources'),s.load(s.metrics,'query-metrics'),s.load(s.groups,'groups/list'),s.load(s.notices,'notices/list'),s.load(s.audit,'ops/audit'),s.load(s.host,'ops/host')]);
    } finally {polling=false;}
  }
  const visible=() => {if(document.hidden)s.invalidate();else{ticks=0;void poll();}};
  const keydown=event=>{
    if(event.key==='Escape'&&drawer.value){closeDrawer();return;}
    const panel=narrow.value&&document.querySelector(drawer.value?'.taki-console .console-sidebar':'.taki-console [data-inspector]');
    if(event.key==='Escape'&&panel){if(tab.value==='requests')closeDetail();else if(tab.value==='groups')closeGroup();return;}
    if(event.key==='Tab'&&panel){const items=[...panel.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex]:not([tabindex="-1"])')].filter(el=>el.getClientRects().length);
      if(items.length&&(!panel.contains(document.activeElement)||(event.shiftKey&&document.activeElement===items[0])||(!event.shiftKey&&document.activeElement===items[items.length-1]))){event.preventDefault();(event.shiftKey?items[items.length-1]:items[0]).focus();}}
  };
  const mediaChanged=event=>{narrow.value=event.matches;};
  const stopDetailWatch=watch(s.detail,()=>{if(narrow.value&&tab.value==='requests'&&currentRequest&&s.detail.value&&focusedRequest!==currentRequest){focusedRequest=currentRequest;requestAnimationFrame(()=>document.querySelector('.request-inspector [aria-label="关闭查询详情"]')?.focus());}});
  const syncRecovery=()=>{if(!storeRecovery(s.captureEdits()))storageAvailable.value=false;};
  const stopRecoveryWatch=watch([s.dirty,s.groupDirty,s.modelDirty,s.draft,s.groupDraft,s.modelDraft,s.modelKey,s.busy,s.message],syncRecovery,{deep:true,flush:'sync'});
  const leaving=event => {const stored=storeRecovery(s.captureEdits());if((!stored&&(s.dirty.value||s.groupDirty.value||s.modelDirty.value))||s.modelKey.value){event.preventDefault();event.returnValue='';}};
  onMounted(() => {try{sessionStorage.setItem(storageKey+'-probe','1');sessionStorage.removeItem(storageKey+'-probe');}catch{storageAvailable.value=false;}
    timer=setInterval(() => void poll(),5000);document.addEventListener('visibilitychange',visible);document.addEventListener('keydown',keydown);media.addEventListener('change',mediaChanged);window.addEventListener('beforeunload',leaving);syncRecovery();});
  onUnmounted(() => {clearInterval(timer);stopRecoveryWatch();stopDetailWatch();if(recovery.current===s)releaseState(s);document.removeEventListener('visibilitychange',visible);document.removeEventListener('keydown',keydown);media.removeEventListener('change',mediaChanged);window.removeEventListener('beforeunload',leaving);});
  return () => h(resolveComponent('k-layout'),{},{default:() => h('main',{class:'taki-console','data-theme':theme.value||undefined},[
    !s.logged.value?h('div',{class:'login-screen'},[
      h('form',{onSubmit:async e => {e.preventDefault();await s.login(password.value);password.value='';const id=new URL(window.location.href).searchParams.get('request_id');if(s.logged.value&&/^[a-f0-9]{64}$/.test(id||''))open(id);ticks=0;void poll();}},[
        portrait('login-portrait'),h('h1','Taki'),h('label',['管理员密码',h('input',{type:'password',autocomplete:'current-password',value:password.value,onInput:e => password.value=e.target.value})]),h('button',{type:'submit',disabled:s.busy.value},s.busy.value?'正在登录…':'登录'),h('small','请输入为此机器人设置的控制台密码。'),s.message.value&&h('p',{role:'status'},s.message.value)])]):h('div',{class:'console-layout'},[
      drawer.value&&h('button',{class:'drawer-scrim','aria-label':'关闭导航',onClick:closeDrawer}),
      h('aside',{class:['console-sidebar',drawer.value&&'is-open'],role:narrow.value&&drawer.value?'dialog':undefined,'aria-modal':narrow.value&&drawer.value?'true':undefined,'aria-label':narrow.value&&drawer.value?'导航':undefined},[
        h('div',{class:'sidebar-brand'},[portrait('brand-image'),h('strong','Taki')]),
        h('nav',{'aria-label':'Taki 工作页面'},navigation.map(([key,label,glyph])=>h('button',{'aria-current':tab.value===key?'page':undefined,onClick:()=>selectTab(key)},[icon(glyph),h('span',label),((key==='settings'&&(s.dirty.value||s.modelDirty.value))||(key==='groups'&&s.groupDirty.value))&&h('i',{class:'draft-dot','aria-label':'有未保存修改'})]))),
        h('div',{class:'sidebar-bottom'},[h('div',{class:'sidebar-instance'},[portrait('small-portrait'),h('div',[h('strong','Taki'),h('small',connection())]),h('i',{class:['status-dot',connection()==='已连接'?'good':'unknown']})]),
          h('button',{class:'sidebar-logout',disabled:s.busy.value,onClick:async()=>{await s.logout();clearRecovery();}},[icon('logout'),h('span','退出登录')])])]),
      h('div',{class:'console-app'},[
        h('header',{class:'topbar'},[h('button',{class:'icon-button menu-button','aria-label':'打开导航','aria-expanded':drawer.value,onClick:()=>drawer.value?closeDrawer():openDrawer()},[icon('menu')]),h('strong',navigation.find(([key])=>key===tab.value)?.[1]||'总览'),h('div',{class:'topbar-actions'},[h('span',{class:'connection-indicator'},connection()),h('button',{class:'icon-button','aria-label':'切换深浅主题',onClick:toggleTheme},[icon('sun')])])]),
        h('div',{class:'page-panel',tabindex:'-1'},[
          s.message.value&&h('p',{role:'status',class:'page-message'},s.message.value),s.readMessage.value&&h('p',{role:'status',class:'page-message'},s.readMessage.value),s.stale.value&&h('p',{role:'alert'},'连接异常：以下保留数据可能已陈旧。'),!storageAvailable.value&&h('p',{role:'alert'},'浏览器无法暂存草稿，刷新页面可能丢失未保存的修改。'),recoveryWarning.value&&h('p',{role:'alert'},'暂存草稿无法读取，请核对服务器设置。'),
          tab.value==='overview'?h('div',[costAlerts(s),overviewView(s,open,()=>{selectTab('requests');search(true);}),attentionView(s,()=>{selectTab('requests');search(true);}),modelOverview(s)]):tab.value==='costs'?costsView(s):tab.value==='requests'?requestsView(s,open,params,search,cursor => s.load(s.requests,'requests/list',{...params,cursor}),id=>{selectTab('groups');openGroup(id);},closeDetail,currentRequest,narrow.value):tab.value==='groups'?groupsView(s,id=>{params.group_id=id;selectTab('requests');search(false);},openGroup,closeGroup,groupOpen.value,narrow.value):tab.value==='sources'?sourcesView(s):h('div',[h('h1','设置'),settingsView(s),modelSettings(s),trustedView(s),operationsSettings(s)]),
          ]),
        h('footer',{class:'status-bar'},[h('span',connection()),button(s.dirty.value||s.groupDirty.value||s.modelDirty.value?'有未保存修改':'设置',()=>selectTab(s.groupDirty.value?'groups':'settings'))])])])
  ])});
}};
export default ctx => ctx.page({path:'/taki',name:'Taki 管理',icon:'settings',order:0,component:Page});

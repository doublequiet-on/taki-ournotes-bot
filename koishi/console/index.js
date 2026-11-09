import { capabilityView } from './chat.js';
import { costsView, costAlerts, budgetEditor } from './costs.js';
import { h, ref, watch, onMounted, onUnmounted, resolveComponent } from './runtime.js';
import { sessionState } from './state.js';
import { button, overviewView, requestsView, groupsView, settingsView } from './views.js';
import { attentionView, sourcesView, operationsSettings } from './ops.js';
import { modelOverview, modelSettings, trustedView } from './model.js';
const navigation=[
  {label:'观测',items:[['overview','总览','home'],['requests','请求记录','list']]},
  {label:'服务配置',items:[['groups','群管理','groups'],['model','模型配置','model'],['costs','费用与预算','cost']]},
  {label:'维护',items:[['sources','数据来源','database'],['operations','运行维护','settings'],['settings','设置','sliders']]},
];
const pages=navigation.flatMap(group=>group.items);
const iconPaths={model:'M5 5h14v14H5zM9 9h6v6H9zM9 2v3m6-3v3M9 19v3m6-3v3M2 9h3m-3 6h3m14-6h3m-3 6h3',
  cost:'M4 20h16M7 16v-4m5 4V8m5 8V4',sliders:'M4 6h16M4 12h16M4 18h16M8 4v4m8 2v4m-5 2v4',
  home:'m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z',list:'M9 5h12M9 12h12M9 19h12M3 5h.01M3 12h.01M3 19h.01',groups:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',database:'M21 5c0 2-4 3-9 3S3 7 3 5s4-3 9-3 9 1 9 3ZM3 5v14c0 2 4 3 9 3s9-1 9-3V5M3 12c0 2 4 3 9 3s9-1 9-3',settings:'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1ZM16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',menu:'M4 6h16M4 12h16M4 18h16',logout:'M9 3H3v18h6M9 12h12m-5-5 5 5-5 5',sun:'M12 2v2M12 20v2M2 12h2M20 12h2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0'};
const icon=name=>h('svg',{viewBox:'0 0 24 24',width:20,height:20,fill:'none',stroke:'currentColor','stroke-width':1.7,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'},[h('path',{d:iconPaths[name]||iconPaths.list})]);
const portrait=className=>h('img',{class:className,src:new URL('./brand/taki.png',import.meta.url).href,alt:''});
const recoveryKey=Symbol.for('taki.console.recovery.v1');
const recovery=globalThis[recoveryKey]||=( {current:null,snapshot:null} );
const storageKey='taki.console.recovery.v1';
let recoveryReadError=false;
function storeRecovery(snapshot){
  try {const hasEdits=snapshot.global||snapshot.group||snapshot.model||snapshot.budget||snapshot.proactive||snapshot.web||snapshot.chat||snapshot.jobs.length||snapshot.controls.length||snapshot.probes.length||snapshot.trusted.length;
    if(!hasEdits){sessionStorage.removeItem(storageKey);return true;}
    const value=JSON.stringify(snapshot);if(value.length>65536)return false;sessionStorage.setItem(storageKey,value);return true;}catch{return false;}
}
function takeRecovery(){
  try {const value=sessionStorage.getItem(storageKey);if(!value)return null;if(value.length>65536)throw Error('recovery too large');
    const snapshot=JSON.parse(value),record=v=>!!v&&typeof v==='object'&&!Array.isArray(v),edit=v=>v===null||(record(v)&&record(v.draft));
    if(!record(snapshot)||snapshot.schema!==1||typeof snapshot.notice!=='string'||!edit(snapshot.global)||!edit(snapshot.group)||!edit(snapshot.model)
      ||snapshot.budget&&(!record(snapshot.budget)||typeof snapshot.budget.daily!=='string'||!record(snapshot.budget.guard)||snapshot.budget.dirty!==true)
      ||snapshot.modelRole!==undefined&&!['primary','fallback','proactive'].includes(snapshot.modelRole)
      ||snapshot.proactive&&(!record(snapshot.proactive)||typeof snapshot.proactive.group_id!=='string'||!record(snapshot.proactive.limits)||!record(snapshot.proactive.guard)||snapshot.proactive.dirty!==true)
      ||snapshot.chat&&(!record(snapshot.chat)||typeof snapshot.chat.group_id!=='string'||!record(snapshot.chat.settings)||!Array.isArray(snapshot.chat.settings.excluded_users)||!record(snapshot.chat.guard)||snapshot.chat.dirty!==true)
      ||snapshot.chatOperations!==undefined&&(!Array.isArray(snapshot.chatOperations)||!snapshot.chatOperations.every(item=>Array.isArray(item)&&item.length===2))
      ||snapshot.web&&(!record(snapshot.web)||!['primary','proactive'].includes(snapshot.web.role)||typeof snapshot.web.enabled!=='boolean'||!Number.isInteger(snapshot.web.max_calls)||snapshot.web.max_calls<1||snapshot.web.max_calls>2||!record(snapshot.web.guard)||snapshot.web.dirty!==true)
      ||snapshot.webOperations!==undefined&&(!Array.isArray(snapshot.webOperations)||!snapshot.webOperations.every(item=>Array.isArray(item)&&item.length===2))
      ||snapshot.proactiveOperations!==undefined&&(!Array.isArray(snapshot.proactiveOperations)||!snapshot.proactiveOperations.every(item=>Array.isArray(item)&&item.length===2))
      ||snapshot.global&&!Array.isArray(snapshot.global.draft.groupAllowlist)
      ||snapshot.group&&typeof snapshot.group.id!=='string'||snapshot.model&&(!record(snapshot.model.draft.price)||snapshot.model.role!==undefined&&!['primary','fallback','proactive'].includes(snapshot.model.role))
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
  const password=ref(''),tab=ref('overview'),drawer=ref(false),groupOpen=ref(false),groupPane=ref('proactive'),groupFilter=ref(''),narrow=ref(media.matches),theme=ref(''),storageAvailable=ref(true),recoveryWarning=ref(recoveryReadError),params={};recoveryReadError=false;
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
  const focusGroup=()=>{if(narrow.value)requestAnimationFrame(()=>document.querySelector('.group-inspector [aria-label="关闭群详情"]')?.focus());};
  const openGroup=id=>{
    if(s.busy.value)return;
    const previous=s.groupId.value;
    inspectorTrigger=document.activeElement;
    s.selectGroup(id);
    if(s.groupId.value!==id)return;
    if(!groupOpen.value||id!==previous){
      groupPane.value=s.groupDirty.value?'policy':s.chatEdit.value?.dirty?'conversation':'proactive';
    }
    groupOpen.value=true;
    void s.refreshProactiveLimits(id);
    focusGroup();
  };
  const selectTab=key=>{
    const wasOpen=drawer.value;
    if(tab.value!==key){
      tab.value=key;
      const layout=document.querySelector('.taki-console')?.parentElement;
      if(layout)layout.scrollTop=0;
    }
    drawer.value=false;
    if(key==='groups'&&(s.groupDirty.value||s.chatEdit.value?.dirty||s.proactiveEdit.value?.dirty)){
      openGroup(s.groupId.value||s.proactiveEdit.value.group_id);
    }
    if(wasOpen)requestAnimationFrame(()=>{
      const target=narrow.value&&key==='groups'&&groupOpen.value?'.group-inspector [aria-label="关闭群详情"]':
        narrow.value&&key==='requests'&&currentRequest&&s.detail.value?'.request-inspector [aria-label="关闭查询详情"]':'.taki-console .page-panel';
      document.querySelector(target)?.focus({preventScroll:true});
    });
    if(key==='sources'&&!s.library.value)void s.refreshLibrary();
    if(key==='requests'&&!s.requests.value)search(false);
    if(['groups','model'].includes(key))void s.refreshModel();
    if(key==='costs')void s.refreshBudget();
    if(['overview','model'].includes(key))void s.load(s.chat,'chat/status');
    if(key==='groups'&&!s.groups.value)void s.load(s.groups,'groups/list');
    if(key==='operations'){void s.load(s.trusted,'ops/trusted');void s.load(s.host,'ops/host');}
  };
  s.navigate=selectTab;
  s.navigateGroup=()=>{
    selectTab('groups');
    if(s.groupId.value)openGroup(s.groupId.value);
    groupPane.value='proactive';
  };
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
      if(ticks%3===1&&!s.busy.value){
        const reads=[];
        if(['groups','model'].includes(tab.value))reads.push(s.refreshModel());
        if(tab.value==='costs')reads.push(s.refreshBudget());
        if(['overview','model'].includes(tab.value))reads.push(s.load(s.chat,'chat/status'));
        if(tab.value==='operations')reads.push(s.load(s.trusted,'ops/trusted'));
        if(['overview','groups','costs'].includes(tab.value)&&s.groupId.value){
          reads.push(s.refreshChatSettings());
          if(tab.value!=='costs'&&(!s.proactiveEdit.value?.dirty||s.proactiveEdit.value.group_id===s.groupId.value))reads.push(s.refreshProactiveLimits(s.groupId.value));
        }
        await Promise.all(reads);
      }
      if(ticks%12===1&&tab.value==='sources')await s.refreshLibrary(s.library.value?.offset||0);
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
  const stopRecoveryWatch=watch([s.dirty,s.groupDirty,s.modelDirty,s.draft,s.groupDraft,s.modelDraft,s.modelKey,s.modelRole,s.budgetEdit,s.proactiveEdit,s.webEdit,s.chatEdit,s.busy,s.message],syncRecovery,{deep:true,flush:'sync'});
  const leaving=event => {const stored=storeRecovery(s.captureEdits());if((!stored&&(s.dirty.value||s.groupDirty.value||s.modelDirty.value||s.budgetEdit.value?.dirty||s.proactiveEdit.value?.dirty||s.webEdit.value?.dirty||s.chatEdit.value?.dirty))||s.modelKey.value){event.preventDefault();event.returnValue='';}};
  onMounted(() => {try{sessionStorage.setItem(storageKey+'-probe','1');sessionStorage.removeItem(storageKey+'-probe');}catch{storageAvailable.value=false;}
    timer=setInterval(() => void poll(),5000);document.addEventListener('visibilitychange',visible);document.addEventListener('keydown',keydown);media.addEventListener('change',mediaChanged);window.addEventListener('beforeunload',leaving);syncRecovery();});
  onUnmounted(() => {clearInterval(timer);stopRecoveryWatch();stopDetailWatch();if(recovery.current===s)releaseState(s);document.removeEventListener('visibilitychange',visible);document.removeEventListener('keydown',keydown);media.removeEventListener('change',mediaChanged);window.removeEventListener('beforeunload',leaving);});
  const hasDraft=key=>({
    settings:s.dirty.value,model:s.modelDirty.value||s.webEdit.value?.dirty,costs:s.budgetEdit.value?.dirty,
    groups:s.groupDirty.value||s.chatEdit.value?.dirty||s.proactiveEdit.value?.dirty,
  })[key];
  const message=()=>{const text=s.pageMessages.value[tab.value]||s.message.value;return text&&h('p',{role:'status',class:'page-message'},text);};
  const pageContent=()=>({
    overview:()=>h('div',[overviewView(s,open,()=>{selectTab('requests');search(true);}),
      h('div',{class:'dashboard-grid'},[attentionView(s,()=>{selectTab('requests');search(true);}),costAlerts(s)]),modelOverview(s)]),
    model:()=>h('div',[h('h1','模型配置'),h('p',{class:'page-intro'},'管理主模型、备用模型和主动接话模型的连接、参数与能力批准。'),modelSettings(s),capabilityView(s.chat.value)]),
    costs:()=>h('div',[costsView(s),budgetEditor(s),costAlerts(s)]),
    requests:()=>requestsView(s,open,params,search,cursor=>s.load(s.requests,'requests/list',{...params,cursor}),id=>{selectTab('groups');openGroup(id);},closeDetail,currentRequest,narrow.value),
    groups:()=>groupsView(s,id=>{params.group_id=id;selectTab('requests');search(false);},openGroup,closeGroup,groupOpen.value,narrow.value,groupPane,groupFilter,message),
    sources:()=>sourcesView(s),
    operations:()=>h('div',[h('h1','运行维护'),h('p',{class:'page-intro'},'查看主机、备份与运行版本，核对维护操作及其回执。'),operationsSettings(s),trustedView(s)]),
    settings:()=>h('div',[h('h1','设置'),h('p',{class:'page-intro'},'控制全局查询、通知和群范围。'),settingsView(s)]),
  })[tab.value]();
  return () => h(resolveComponent('k-layout'),{},{default:() => h('main',{class:'taki-console','data-theme':theme.value||undefined},[
    !s.logged.value?h('div',{class:'login-screen'},[
      h('form',{onSubmit:async e => {e.preventDefault();await s.login(password.value);password.value='';const id=new URL(window.location.href).searchParams.get('request_id');if(s.logged.value&&/^[a-f0-9]{64}$/.test(id||''))open(id);ticks=0;void poll();}},[
        portrait('login-portrait'),h('h1','Taki'),h('label',['管理员密码',h('input',{type:'password',autocomplete:'current-password',value:password.value,onInput:e => password.value=e.target.value})]),h('button',{type:'submit',disabled:s.busy.value},s.busy.value?'正在登录…':'登录'),h('small','请输入为此机器人设置的控制台密码。'),s.message.value&&h('p',{role:'status'},s.message.value)])]):h('div',{class:'console-layout'},[
      drawer.value&&h('button',{class:'drawer-scrim','aria-label':'关闭导航',onClick:closeDrawer}),
      h('aside',{class:['console-sidebar',drawer.value&&'is-open'],role:narrow.value&&drawer.value?'dialog':undefined,'aria-modal':narrow.value&&drawer.value?'true':undefined,'aria-label':narrow.value&&drawer.value?'导航':undefined},[
        h('div',{class:'sidebar-brand'},[portrait('brand-image'),h('div',[h('strong','Taki'),h('small','私有运维控制台')])]),
        h('nav',{'aria-label':'Taki 工作页面'},navigation.map(({label,items})=>h('div',{class:'nav-group'},[
          h('span',{class:'nav-label'},label),...items.map(([key,title,glyph])=>h('button',{key,'aria-current':tab.value===key?'page':undefined,onClick:()=>selectTab(key)},[
            icon(glyph),h('span',title),hasDraft(key)&&h('i',{class:'draft-dot','aria-label':'有未保存修改'}),
          ])),
        ]))),
        h('div',{class:'sidebar-bottom'},[
          h('button',{class:'sidebar-logout',disabled:s.busy.value,onClick:async()=>{await s.logout();clearRecovery();}},[icon('logout'),h('span','退出登录')]),
        ]),
      ]),
      h('div',{class:'console-app'},[
        h('header',{class:'topbar'},[
          h('button',{class:'icon-button menu-button','aria-label':'打开导航','aria-expanded':drawer.value,onClick:()=>drawer.value?closeDrawer():openDrawer()},[icon('menu')]),
          h('div',{class:'topbar-path'},[h('span','TAKI'),h('span',{'aria-hidden':'true'},'/'),h('strong',pages.find(([key])=>key===tab.value)[1])]),
          h('div',{class:'topbar-actions'},[
            h('span',{class:'connection-indicator'},[h('i',{class:['status-dot',connection()==='已连接'?'good':'unknown']}),connection()]),
            h('button',{class:'icon-button','aria-label':'切换深浅主题',onClick:toggleTheme},[icon('sun')]),
          ]),
        ]),
        h('div',{class:'page-panel',tabindex:'-1'},[
          !(tab.value==='groups'&&groupOpen.value&&s.groupDraft.value&&narrow.value)&&message(),
          s.readMessage.value&&h('p',{role:'status',class:'page-message'},s.readMessage.value),
          s.stale.value&&h('p',{role:'alert'},'连接异常：以下保留数据可能已陈旧。'),
          !storageAvailable.value&&h('p',{role:'alert'},'浏览器无法暂存草稿，刷新页面可能丢失未保存的修改。'),
          recoveryWarning.value&&h('p',{role:'alert'},'暂存草稿无法读取，请核对服务器设置。'),
          h('div',{key:tab.value},[pageContent()]),
        ]),
      ]),
    ])

  ])});
}};
export default ctx => ctx.page({path:'/taki',name:'Taki 管理',icon:'settings',order:0,component:Page});

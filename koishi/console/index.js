import { h, ref, onMounted, onUnmounted, resolveComponent } from './runtime.js';
import { sessionState } from './state.js';
import { button, overviewView, requestsView, groupsView, settingsView } from './views.js';
const Page={setup() {
  const s=sessionState(),password=ref(''),tab=ref('overview'),params={};
  let timer,ticks=0,currentRequest=null,polling=false;
  const open=id => {if(currentRequest!==id)s.detail.value=null;currentRequest=id;tab.value='requests';const url=new URL(window.location.href);url.searchParams.set('request_id',id);window.history.replaceState(null,'',url);void s.load(s.detail,'requests/detail',id);};
  const search=attention => {const p={attention:String(attention)};if(params.state)p.state=params.state;if(params.group_id)p.group_id=params.group_id;
    delete params.state;if(p.state)params.state=p.state;params.attention=p.attention;delete params.cursor;void s.load(s.requests,'requests/list',p);};
  async function poll() {
    if(!s.logged.value||document.hidden||polling)return;
    polling=true;ticks++;
    try {
      if(ticks%3===1)await s.load(s.overview,'overview');
      if(tab.value==='requests') {
        if(!s.requests.value)search(false);
        if(currentRequest&&!['complete','failed','closed_unsent'].includes(s.detail.value?.sections?.request?.data?.delivery_state))await s.load(s.detail,'requests/detail',currentRequest);
      }
      if(ticks%12===1)await Promise.all([s.load(s.sources,'sources'),s.load(s.metrics,'query-metrics'),s.load(s.groups,'groups/list'),s.load(s.notices,'notices/list')]);
    } finally {polling=false;}
  }
  const visible=() => {if(document.hidden)s.invalidate();else{ticks=0;void poll();}};
  const leaving=event => {if(s.dirty.value||s.groupDirty.value){event.preventDefault();event.returnValue='';}};
  onMounted(() => {timer=setInterval(() => void poll(),5000);document.addEventListener('visibilitychange',visible);window.addEventListener('beforeunload',leaving);});
  onUnmounted(() => {clearInterval(timer);s.invalidate();document.removeEventListener('visibilitychange',visible);window.removeEventListener('beforeunload',leaving);});
  return () => h(resolveComponent('k-layout'),{},{default:() => h('main',{class:'taki-console'},[
    s.message.value&&h('p',{role:'status'},s.message.value),s.stale.value&&h('p',{role:'alert'},'连接异常：以下保留数据可能已陈旧。'),
    !s.logged.value?h('form',{onSubmit:async e => {e.preventDefault();await s.login(password.value);password.value='';const id=new URL(window.location.href).searchParams.get('request_id');if(s.logged.value&&/^[a-f0-9]{64}$/.test(id||''))open(id);ticks=0;void poll();}},[
      h('span',{class:'eyebrow'},'TAKI · BOT CONSOLE'),h('h1','Taki 管理登录'),h('p','查看机器人状态、排查回复、管理每个群。'),h('label',['管理员密码',h('input',{type:'password',autocomplete:'current-password',value:password.value,onInput:e => password.value=e.target.value})]),h('button',{type:'submit',disabled:s.busy.value},s.busy.value?'正在登录…':'登录'),h('small','请输入为此机器人设置的控制台密码，不是 QQ 密码。')]):h('div',[
      h('header',{class:'console-header'},[h('div',[h('span',{class:'eyebrow'},'TAKI · BOT CONSOLE')]),button('退出登录',s.logout,s.busy.value)]),
      h('nav',{'aria-label':'Taki 工作页面'},['overview','requests','groups'].map((key,i) => h('button',{'aria-current':tab.value===key?'page':undefined,onClick:() => {tab.value=key;if(key==='requests'&&!s.requests.value)search(false);}},['总览','查询诊断','分群管理'][i]))),
      tab.value==='overview'?overviewView(s,open):tab.value==='requests'?requestsView(s,open,params,search,cursor => s.load(s.requests,'requests/list',{...params,cursor}),id=>{tab.value='groups';s.selectGroup(id);}):groupsView(s,id=>{params.group_id=id;tab.value='requests';search(false);}),
      tab.value==='overview'&&settingsView(s),h('footer',[h('small','页面自动更新状态；修改设置后需点击保存。离开页面会暂停轮询。'),h('small','管理员关联数据仅供诊断；不保存问句、用户标识或原消息 ID。')])])
  ])});
}};
export default ctx => ctx.page({path:'/taki',name:'Taki 管理',icon:'settings',order:0,component:Page});

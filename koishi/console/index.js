import { h, ref, onMounted, onUnmounted, resolveComponent } from 'vue';
import { send } from '@koishijs/client';

const Page = {
  setup() {
    const logged = ref(false), password = ref(''), message = ref(''), state = ref(null), draft = ref(null), groups = ref(''), busy = ref(false);
    let timer;
    const load = async (reset = false) => {
      try {
        const result = await send('taki/status');
        state.value = result;
        if (reset || !draft.value) { draft.value = { ...result.settings }; groups.value = result.settings.groupAllowlist.join('\n'); }
        logged.value = true;
      } catch { logged.value = false; state.value = null; draft.value = null; message.value = '请登录；管理会话在 30 分钟后过期，断线后需重新登录。'; }
    };
    const login = async () => {
      busy.value = true;
      try { await send('taki/login', password.value); message.value = ''; await load(true); }
      catch { message.value = '登录失败，请检查密码后稍后重试。'; }
      finally { password.value = ''; busy.value = false; }
    };
    // Keep revision separate from the submitted settings object.
    let revision = 0;
    const refresh = async () => { await load(true); revision = state.value?.revision ?? 0; };
    const submit = async () => {
      busy.value = true;
      try {
        await send('taki/settings', { ...draft.value, groupAllowlist: groups.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean) }, revision);
        message.value = '已保存并生效。在途查询继续完成；已发出的通知仍记录回执。';
        await refresh();
      } catch { message.value = '保存失败，请检查群 ID 格式或刷新设置后重试。'; }
      finally { busy.value = false; }
    };
    onMounted(() => { timer = setInterval(() => { if (logged.value) void load(); }, 5000); });
    onUnmounted(() => clearInterval(timer));
    const button = (text, action) => h('button', { disabled: busy.value, onClick: action, style: 'padding:8px 16px;margin:6px 8px 6px 0;cursor:pointer' }, text);
    const toggle = (key, text) => h('label', { style: 'display:block;margin:16px 0' }, [h('input', { type: 'checkbox', checked: draft.value[key], onChange: e => draft.value[key] = e.target.checked }), ' ' + text]);
    return () => h(resolveComponent('k-layout'), {}, { default: () => h('main', { style: 'max-width:900px;margin:32px auto;padding:0 24px;line-height:1.7;overflow-wrap:anywhere' }, [
      h('h1', 'Taki 管理'), h('p', '管理查询接收范围与主动通知。设置保存后立即生效。'),
      message.value && h('p', { role: 'status' }, message.value),
      !logged.value ? h('form', { onSubmit: async e => { e.preventDefault(); await login(); revision = state.value?.revision ?? 0; } }, [
        h('label', ['管理员密码 ', h('input', { type: 'password', autocomplete: 'current-password', value: password.value, onInput: e => password.value = e.target.value, 'aria-label': '管理员密码' })]),
        h('button', { type: 'submit', disabled: busy.value }, '登录'),
        h('p', '使用部署时设置的独立管理密码。')
      ]) : state.value && draft.value && h('div', [
        h('h2', '运行状态'),
        h('p', `QQ 状态：${state.value.botStatus === 1 ? '在线' : '未在线'} · 后端握手：${state.value.handshake ? '已完成' : '等待中'} · 接收器：${state.value.receiverActive ? '运行' : '未就绪'}`),
        h('p', `版本：${state.value.release} · 进程运行 ${state.value.uptime} 秒`),
        h('h2', '接收设置'), toggle('queriesEnabled', '接收新查询'), toggle('privateEnabled', '允许私聊查询'), toggle('groupsEnabled', '允许群查询与群通知'),
        h('label', { for: 'taki-groups' }, '允许的群 OpenID（每行一个，留空允许所有群）'),
        h('textarea', { id: 'taki-groups', rows: 4, style: 'display:block;width:100%;box-sizing:border-box', value: groups.value, onInput: e => groups.value = e.target.value }),
        toggle('notificationsEnabled', '允许主动通知'),
        h('p', `后端主动通知总开关：${state.value.backendNotices ? '已开启' : '已关闭'}。页面开关不能越过后端开关或 QQ 平台许可。`),
        button('保存设置', submit), button('刷新设置', refresh),
        button('退出登录', async () => { await send('taki/logout'); logged.value = false; state.value = null; draft.value = null; }),
        h('h2', '管理记录'), h('p', '仅显示本进程最近 100 条管理事件，不包含消息正文或密钥。'),
        h('ul', state.value.events.slice().reverse().map(e => h('li', `${e.time} ${e.event}`))),
        h('p', '版本升级、机器人凭据和插件变更通过部署流程管理。')
      ])
    ]) });
  }
};
export default ctx => ctx.page({ path: '/taki', name: 'Taki 管理', icon: 'settings', order: 0, component: Page });

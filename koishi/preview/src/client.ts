// Local-only interaction model. No network, platform adapter, backend token or persistence API.
import { requests, groups as initialGroups, statuses } from "./data";

const $ = <T extends HTMLElement = HTMLElement>(selector: string) =>
  document.querySelector<T>(selector)!;
const $$ = (selector: string) =>
  Array.from(document.querySelectorAll<HTMLElement>(selector));
const escape = (value: unknown) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
let groups = structuredClone(initialGroups),
  filter = "all",
  selectedGroup = "",
  groupDirty = false,
  globalDirty = false;
let settings = {
  queries: true,
  groups: true,
  private: false,
  notices: false,
  scope: "selected",
};
let toastTimer: ReturnType<typeof setTimeout>;
const arrow = '<span aria-hidden="true">↗</span>';
const mobileNav = matchMedia("(max-width: 599px)");
function syncNav() {
  const open =
    document.body.classList.contains("nav-open") && mobileNav.matches;
  $("#sidebar").inert = mobileNav.matches && !open;
  $("#menu").setAttribute("aria-expanded", String(open));
  $("#nav-scrim").hidden = !open;
}
mobileNav.addEventListener("change", () => {
  document.body.classList.remove("nav-open");
  syncNav();
});
const statePill = (state: string) =>
  `<span class="pill ${state === "complete" ? "success" : state === "processing" ? "purple" : state === "failed" ? "danger" : "warm"}"><i class="dot"></i>${statuses[state]}</span>`;
function toast(message: string) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 3200);
}
function navigate() {
  const hash = location.hash.slice(1),
    page = ["overview", "requests", "groups", "sources", "settings"].includes(
      hash,
    )
      ? hash
      : "overview";
  $$("[data-page]").forEach((el) => (el.hidden = el.dataset.page !== page));
  $$("[data-nav]").forEach((el) => {
    if (el.dataset.nav === page) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  $("#page-title").textContent = $(`[data-nav="${page}"] span`).textContent;
  document.title = `${$("#page-title").textContent} · Taki 预览`;
  document.body.classList.remove("nav-open");
  syncNav();
  window.scrollTo({ top: 0 });
}
window.addEventListener("hashchange", navigate);
navigate();
$("#menu").addEventListener("click", () => {
  document.body.classList.toggle("nav-open");
  syncNav();
  if (document.body.classList.contains("nav-open")) $("#sidebar a").focus();
});
$("#nav-scrim").addEventListener("click", () => {
  document.body.classList.remove("nav-open");
  syncNav();
  $("#menu").focus();
});
document.addEventListener("keydown", (event) => {
  if (!document.body.classList.contains("nav-open")) return;
  if (event.key === "Escape") $("#nav-scrim").click();
  if (event.key === "Tab") {
    const links = $$("#sidebar a, #sidebar button"),
      first = links[0],
      last = links.at(-1)!;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
});
function themeToggle() {
  const dark = document.documentElement.dataset.theme !== "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  $("#theme-label").textContent = dark ? "深色" : "浅色";
  $("#theme").setAttribute(
    "aria-label",
    dark ? "切换浅色主题" : "切换深色主题",
  );
}
if (matchMedia("(prefers-color-scheme: dark)").matches) themeToggle();
$("#theme").addEventListener("click", themeToggle);
$("#theme-setting").addEventListener("click", themeToggle);
$("#refresh").addEventListener("click", () => toast("演示快照已刷新"));

function renderRequests() {
  const term = $<HTMLInputElement>("#request-search")
      .value.trim()
      .toLowerCase(),
    state = $<HTMLSelectElement>("#status-filter").value;
  const items = requests.filter(
    (item) =>
      (filter !== "attention" ||
        ["uncertain", "partial", "failed"].includes(item.state)) &&
      (!state || item.state === state) &&
      `${item.command} ${groupName(item.groupId)}`.toLowerCase().includes(term),
  );
  $("#request-table").innerHTML =
    items
      .map(
        (item) =>
          `<tr><td><button class="request-command" data-request="${item.id}">${escape(item.command)}</button><span class="table-kind">${item.kind}</span></td><td>${escape(groupName(item.groupId))}</td><td>${statePill(item.state)}</td><td class="numeric">${item.duration}</td><td class="muted numeric">${item.time}</td><td><button class="icon-button" data-request="${item.id}" aria-label="查看 ${escape(item.command)} 详情">${arrow}</button></td></tr>`,
      )
      .join("") || '<tr><td colspan="6" class="empty">没有匹配的记录</td></tr>';
  $("#request-count").textContent = `${items.length} 条记录`;
}
function groupName(id: string) {
  return groups.find((g) => g.id === id)?.label || "未关联群";
}
function renderRecent() {
  $("#recent-list").innerHTML = requests
    .slice(0, 5)
    .map(
      (item) =>
        `<button class="recent-row" data-request="${item.id}"><span class="command-icon ${item.kind === "卡牌" ? "rose" : item.kind === "歌曲" ? "purple" : "teal"}">${item.kind === "歌曲" ? "♫" : item.kind === "卡牌" ? "▧" : item.kind === "帮助" ? "?" : "▤"}</span><span class="recent-text"><strong>${escape(item.command)}</strong><small>${escape(groupName(item.groupId))} · ${item.time}</small></span>${statePill(item.state)}<span class="row-arrow" aria-hidden="true">›</span></button>`,
    )
    .join("");
}
$$("[data-filter]").forEach((el) =>
  el.addEventListener("click", () => {
    filter = el.dataset.filter!;
    $$("[data-filter]").forEach((b) => {
      b.classList.toggle("active", b.dataset.filter === filter);
      b.setAttribute("aria-pressed", String(b.dataset.filter === filter));
    });
    renderRequests();
  }),
);
$("#request-search").addEventListener("input", renderRequests);
$("#status-filter").addEventListener("change", renderRequests);
$("#attention-link").addEventListener("click", () => {
  location.hash = "requests";
  $('[data-filter="attention"]').click();
});
function showRequest(id: string) {
  const item = requests.find((r) => r.id === id);
  if (!item) return;
  const spans = [
    ["准备", item.prepare],
    ["上传", item.upload],
    ["平台发送", item.send],
  ] as const;
  $("#request-detail").innerHTML =
    `<div class="detail-command"><span class="command-icon purple">${item.kind === "歌曲" ? "♫" : "▤"}</span><h3>${escape(item.command)}</h3>${statePill(item.state)}</div><dl class="key-values"><div><dt>群</dt><dd><button class="text-link" data-related-group="${item.groupId}">${escape(groupName(item.groupId))} ↗</button></dd></div><div><dt>受理时间</dt><dd>今天 ${item.time}</dd></div><div><dt>总耗时</dt><dd>${item.duration}</dd></div><div><dt>片段确认</dt><dd>${item.state === "complete" ? "1 / 1" : item.state === "partial" ? "1 / 2" : "0 / 1"}</dd></div></dl><h3 class="section-label">阶段耗时</h3><div class="timings">${spans.map(([name, time]) => `<div class="timing-row"><span>${name}</span><div class="timing-track">${time !== null ? `<span style="width:${Math.max(2, Math.min(100, time / 70))}%"></span>` : ""}</div><span class="numeric">${time === null ? "尚未采集" : time + " ms"}</span></div>`).join("")}</div>${item.state === "uncertain" ? '<p class="inline-alert">发送结果待确认，请先核对 QQ 群。</p>' : ""}<details><summary>更多记录</summary><dl class="key-values"><div><dt>请求 ID</dt><dd><code>${item.id}</code></dd></div><div><dt>解析来源</dt><dd>${item.kind === "问" ? "本地" : "直接指令"}</dd></div><div><dt>排序等待</dt><dd>尚未采集</dd></div><div><dt>回执提交</dt><dd>尚未采集</dd></div></dl></details>`;
  $<HTMLDialogElement>("#request-dialog").showModal();
}
document.addEventListener("click", (event) => {
  const target = event.target as HTMLElement;
  const request = target.closest<HTMLElement>("[data-request]");
  if (request) showRequest(request.dataset.request!);
  const group = target.closest<HTMLElement>("[data-group]");
  if (group) showGroup(group.dataset.group!);
  const related = target.closest<HTMLElement>("[data-related-group]");
  if (related) {
    $<HTMLDialogElement>("#request-dialog").close();
    location.hash = "groups";
    showGroup(related.dataset.relatedGroup!);
  }
});
function effective(g: (typeof groups)[number]) {
  return {
    query: settings.queries && settings.groups && g.queries !== "pause",
    notice:
      settings.notices &&
      settings.groups &&
      g.notifications !== "pause" &&
      g.permission === "已允许",
    ask:
      g.ask === "inherit"
        ? "本地优先"
        : g.ask === "off"
          ? "关闭"
          : g.ask === "local"
            ? "仅本地"
            : "允许模型",
  };
}
function renderGroups() {
  const term = $<HTMLInputElement>("#group-search").value.trim(),
    items = groups.filter((g) => g.label.includes(term));
  $("#group-grid").innerHTML = items
    .map((g) => {
      const e = effective(g);
      return `<article class="card group-card"><div class="group-card-heading"><span class="avatar ${g.color}">${escape(g.label.trim().slice(0, 1))}</span><span class="pill ${e.query ? "success" : "neutral"}">${e.query ? "接收中" : "已暂停"}</span></div><h2>${escape(g.label)}</h2><small class="muted">最近受理 ${g.last} · ${g.requests} 条请求</small><div class="group-rule-row"><span>新查询</span><strong>${e.query ? "允许" : "暂停"}</strong></div><div class="group-rule-row"><span>主动通知</span><strong>${e.notice ? "允许" : "暂停"}</strong></div><div class="group-rule-row"><span>/问</span><strong>${e.ask}</strong></div><button class="button tonal" data-group="${g.id}">管理群设置 <span aria-hidden="true">↗</span></button></article>`;
    })
    .join("");
  $("#group-empty").hidden = !!items.length;
  $("#scope-groups").innerHTML = groups
    .map(
      (g) =>
        `<span class="group-chip"><i class="avatar tiny ${g.color}">${escape(g.label.slice(0, 1))}</i>${escape(g.label)}</span>`,
    )
    .join("");
}
function showGroup(id: string) {
  const g = groups.find((g) => g.id === id);
  if (!g) return;
  selectedGroup = id;
  groupDirty = false;
  const form = $<HTMLFormElement>("#group-form");
  for (const key of ["label", "queries", "notifications", "ask"] as const)
    (form.elements.namedItem(key) as HTMLInputElement).value = g[key];
  $("#group-detail-title").textContent = g.label;
  $("#group-identifier").textContent = g.id;
  $("#group-dirty").textContent = "未修改";
  $("#group-error").hidden = true;
  const e = effective(g);
  $("#effective-policy").innerHTML =
    `<span class="pill ${e.query ? "success" : "neutral"}">查询 ${e.query ? "允许" : "暂停"}</span><span class="pill ${e.notice ? "success" : "neutral"}">通知 ${e.notice ? "允许" : "暂停"}</span><span class="pill purple">/问 ${e.ask}</span>`;
  $<HTMLDialogElement>("#group-dialog").showModal();
}
$("#group-search").addEventListener("input", renderGroups);
$("#group-form").addEventListener("input", () => {
  groupDirty = true;
  $("#group-dirty").textContent = "未保存";
});
$("#group-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget as HTMLFormElement,
    label = (form.elements.namedItem("label") as HTMLInputElement).value.trim();
  if (!label || Array.from(label).length > 80) {
    $("#group-error").hidden = false;
    $("#group-error").textContent = "群备注需为 1–80 个字";
    return;
  }
  const g = groups.find((g) => g.id === selectedGroup)!;
  for (const key of ["label", "queries", "notifications", "ask"] as const)
    g[key] = (form.elements.namedItem(key) as HTMLInputElement).value;
  g.label = label;
  groupDirty = false;
  renderGroups();
  renderRequests();
  renderRecent();
  $<HTMLDialogElement>("#group-dialog").close();
  toast("群设置已保存至本次预览");
});
$("#group-reset").addEventListener("click", () => {
  groups = structuredClone(initialGroups);
  renderGroups();
  renderRecent();
  renderRequests();
  toast("群设置已还原");
});
function restoreGlobal() {
  const form = $<HTMLFormElement>("#global-form");
  for (const key of ["queries", "groups", "private", "notices"] as const)
    (form.elements.namedItem(key) as HTMLInputElement).checked = settings[key];
  (
    form.querySelector(
      `input[name="scope"][value="${settings.scope}"]`,
    ) as HTMLInputElement
  ).checked = true;
  globalDirty = false;
  $("#global-dirty").hidden = true;
  $("#global-save-status").textContent = "已保存至本次预览";
  updateScope();
}
function updateScope() {
  const scope = $<HTMLInputElement>('input[name="scope"]:checked').value;
  $("#scope-groups").hidden = scope === "all";
}
$("#global-form").addEventListener("input", () => {
  globalDirty = true;
  $("#global-dirty").hidden = false;
  $("#global-save-status").textContent = "未保存";
  updateScope();
});
$("#global-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget as HTMLFormElement;
  for (const key of ["queries", "groups", "private", "notices"] as const)
    settings[key] = (form.elements.namedItem(key) as HTMLInputElement).checked;
  settings.scope = $<HTMLInputElement>('input[name="scope"]:checked').value;
  restoreGlobal();
  renderGroups();
  $("#overview-query").textContent = settings.queries ? "接收中" : "已暂停";
  $("#overview-query").className =
    `pill ${settings.queries ? "success" : "neutral"}`;
  $("#overview-notice").textContent = settings.notices ? "已开启" : "已暂停";
  $("#overview-notice").className =
    `pill ${settings.notices ? "success" : "neutral"}`;
  $("#overview-range").textContent = settings.groups
    ? settings.scope === "all"
      ? "全部群"
      : "3 个群"
    : "已暂停";
  toast("设置已保存至本次预览");
});
$("#global-form").addEventListener("reset", (event) => {
  event.preventDefault();
  restoreGlobal();
  toast("已还原未保存设置");
});
$$(".close-dialog").forEach((el) =>
  el.addEventListener("click", () => {
    const dialog = el.closest("dialog")!;
    if (
      dialog.id === "group-dialog" &&
      groupDirty &&
      !confirm("放弃未保存的群设置？")
    )
      return;
    groupDirty = false;
    dialog.close();
  }),
);
$<HTMLDialogElement>("#group-dialog").addEventListener("cancel", (event) => {
  if (groupDirty && !confirm("放弃未保存的群设置？")) event.preventDefault();
  else groupDirty = false;
});
$("#logout").addEventListener("click", () => {
  if (globalDirty && !confirm("放弃未保存的设置并退出？")) return;
  restoreGlobal();
  $<HTMLDialogElement>("#login-dialog").showModal();
});
$("#profile").addEventListener("click", () => toast("管理员 · 演示实例"));
$("#login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  $<HTMLFormElement>("#login-form").reset();
  $<HTMLDialogElement>("#login-dialog").close();
  location.hash = "overview";
  toast("已进入设计预览");
});
window.addEventListener("beforeunload", (event) => {
  if (globalDirty || groupDirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});
renderRecent();
renderRequests();
renderGroups();

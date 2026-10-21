# Taki Console 设计预览

独立的 Astro 静态预览。参照 Haneoka 的 Material 侧栏、圆角内容面板、卡片与设置行；页面与界面 SVG 为本项目实现。品牌图片来源见 [资源清单](public/brand/README.md)，不引入其他网站的业务或账号系统。当前版本只使用 `src/data.ts` 演示夹具，不接 Console RPC、Python、QQ 或模型，不读取真实配置。生产 Console 仍在 `../console/`。

## 本地查看

Node 24.19.0，在本目录运行：

```sh
npm ci --ignore-scripts
ASTRO_TELEMETRY_DISABLED=1 npm run build
ASTRO_TELEMETRY_DISABLED=1 npm run preview
```

PowerShell 使用 `$env:ASTRO_TELEMETRY_DISABLED='1'`，再分别运行 `npm run build`、`npm run preview`。打开 `http://127.0.0.1:15160`。只监听本机；端口占用时先确认所属进程，不停止别的预览。需要实时开发时用 `npm run dev`；正式视觉验收使用 build 后的 preview。

## 页面与交互

- 总览：演示健康、受理记录、来源和 `/问` 计数；缺失指标显示“尚未采集”。
- 查询记录：范围/状态/文字筛选，详情弹窗及阶段耗时，关联群入口。
- 群管理：搜索、备注和策略编辑、未保存保护、保存和重置。
- 数据来源：快照与折叠采集详情。
- 设置：查询、私聊、群与通知开关、全局范围、还原和保存；顶部/设置页可切换主题。
- 退出预览后可查看登录样式，点击“进入预览”返回；这里没有真实认证。演示数据及设置只在内存中存在，重载还原。

顶部持续标注“设计预览 · 演示数据”。指令文本为虚构夹具，用于展示排版；正式系统仍遵守不采集原问句的边界。CSP 禁止页面网络连接，界面操作不提交表单或请求外部服务。没有重启、部署、更新或发送 QQ 消息的操作。

## 文件

| 文件 | 职责 |
|---|---|
| `src/pages/index.astro` | 五页静态结构、导航、弹窗与资源安全策略 |
| `src/components/Icon.astro` | 内联 SVG 图标 |
| `src/data.ts` | 虚构请求与群数据 |
| `src/client.ts` | 内存交互、筛选、草稿及保存、主题与导航 |
| `src/style.css` | 响应式布局、明暗主题与组件视觉 |

此目录不在现有组合包的复制清单中，不替换 Koishi 插件入口。设计确认后，真实接入需另行实现和验证认证、缺失口径、保存 revision、草稿并发保护及安装包资源路径。

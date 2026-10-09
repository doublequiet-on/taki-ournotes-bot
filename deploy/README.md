# 部署与本地启动

`setup-local.ps1` 和 `start-bot.ps1` 是 Windows 本地准备与启动入口。从仓库根目录运行 `.\deploy\setup-local.ps1` 和 `.\deploy\start-bot.ps1`；脚本会自行定位仓库根目录。操作步骤与凭据配置见根目录 README 和 LOCAL_QQ_TEST.md。

目录职责、启动分流和协作边界见 [Deploy 架构地图](L2.md)；受管启动委托的更新工具见 [Scripts 架构地图](../scripts/L2.md)。

`requirements.txt` 列出运行依赖；项目安装与打包的依赖定义以根目录 `pyproject.toml` 为准。按根目录 README 使用 `python -m pip install -e .` 安装项目；若只需按清单安装依赖，可在仓库根目录运行 `python -m pip install -r .\deploy\requirements.txt`。

## Linux 受管部署

当前服务器使用独立控制器管理的离线发布流程，操作入口见 [Linux 离线发布与更新](LINUX_OFFLINE.md)。main 的 Python 3.10／3.12、安装包及断网安装检查通过后，CI 发布该提交专属安装包；控制器下载、校验、准备独立环境，通过兼容与健康门槛后切换。合并 PR、发布成功和服务器实际更新是三项独立证据。

遇到 `compatibility_review_required`，先核对具体代码／数据变化及旧版本可读性，按协议记录审核；不要清空缓存、额度或失败状态来绕过门槛。应用更新不会替换 `/usr/local/lib/taki-updater` 中的独立控制器。生产配置、控制器安装、手动重启及 QQ 操作按各自授权执行。

## 自行部署与配置

自行部署时创建仅本机可读的 `.env`，填写自己的 QQ 凭据，再按进程管理器的方式运行 `ournotes-bot bot`；不要和受管实例并行启动。切勿把 `.env`、日志或缓存加入 Git。

默认主资料／完整谱面／分数表／榜线已使用 Haneoka；现有显式来源配置优先，不因升级被覆盖。各来源缓存独立，切换前按 [统一来源说明](../docs/HANEOKA_UNIFIED.md)核对配置和缺失字段，勿覆盖其他配置或旧源缓存。修改环境变量后须按所属部署方式重启才生效。

QQ 指令面板使用 `ournotes-bot setup-menu` 单独更新，属于远端 QQ 配置操作；自动更新代码不会代替面板或真实手机验收。

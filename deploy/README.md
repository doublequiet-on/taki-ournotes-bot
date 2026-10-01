# 部署与本地启动

`setup-local.ps1` 和 `start-bot.ps1` 是 Windows 本地准备与启动入口。从仓库根目录运行 `.\deploy\setup-local.ps1` 和 `.\deploy\start-bot.ps1`；脚本会自行定位仓库根目录。操作步骤与凭据配置见根目录 README 和 LOCAL_QQ_TEST.md。

目录职责、启动分流和协作边界见 [Deploy 架构地图](L2.md)；受管启动委托的更新工具见 [Scripts 架构地图](../scripts/L2.md)。

`requirements.txt` 列出运行依赖；项目安装与打包的依赖定义以根目录 `pyproject.toml` 为准。按根目录 README 使用 `python -m pip install -e .` 安装项目；若只需按清单安装依赖，可在仓库根目录运行 `python -m pip install -r .\deploy\requirements.txt`。

以下服务器部署说明仅作模板，不包含登录信息、服务器地址或 QQ 会话。先完成根目录 README 的本地测试，再按环境调整路径和用户。

在服务器部署 Python 项目，创建仅本机可读的 `.env`，填写自己的 `QQ_APP_ID` 和 `QQ_APP_SECRET`，然后运行 `ournotes-bot bot`。可用进程管理器保持运行。切勿把 `.env`、日志或缓存加入 Git。

代码更新后重启官方机器人进程。若需更新 QQ 指令面板，另运行 `ournotes-bot setup-menu`；修改数据源后运行 `ournotes-bot sync` 重建缓存。

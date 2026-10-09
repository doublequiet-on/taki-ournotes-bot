# L3
# Input: 无运行时业务输入。
# Output: 固定机器人介绍文本。
# Pos: Query / Deterministic 的直接介绍命令内容叶子，供 commands 使用；见 query/L2-2.md。
# Effects/Dependencies: 只定义固定文案，无外部 I/O。

"""User-facing introduction shared by help and the introduction command."""

INTRO = ("Taki · BanG Dream! Our Notes 日服资料查询助手\n"
         "查歌曲、颜色与激奏组合，查看谱面、分数表、角色卡、SNAP、活动信息和四服活动歌曲榜线。\n"
         "群内请 @我 后发送指令；/问 支持常见自然语言查询，/帮助 查看示例。\n"
         "默认资料入口为 Haneoka；其活动榜观测来自 MoeNotes tracker。歌曲分析是上游参考结果，非游戏实得分。\n"
         "歌曲榜限 Top 100 观测；不提供配队攻略、账号操作、积分档线或预测。")

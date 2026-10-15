# 现状图片画廊

图片改版前的离线盘点和草图评审工具。调用当前回复准备/绘图函数，保留分页、缺图占位和文字回退；不更改生产主题、查询、发送或部署。

在仓库根目录执行：

```powershell
.venv/Scripts/python.exe -B scripts/export_output_gallery.py --output artifacts/output-gallery
```

默认使用仓库公开Haneoka测试夹具和明确标注的合成边界数据；缺原卡面时导出文字回退。`--cache-dir <公开缓存副本>` 补充真实素材，格式与preview_visuals相同（ournotes-cache.json、asset-cache/、chart-cache/、可选preview-details/support-51.json）。该组是Project Yume旧快照，会标注来源，不代表当前默认Haneoka资料版本。

`--font <CJK字体>` 固定字体并记录哈希，不复制字体文件；默认锁定当前绘图器首个可用字体。`--only <模板或用例ID>` 用于单例复核；省略生成完整登记集合。打开index.html，点击图片看images/实际编码原图，thumbnails/为430px派生图。

输入前后哈希校验，网络由audit hook阻断，不刷新数据、不连接QQ、不调用模型。基础记录每例一次墙钟/CPU总时长（含准备/绘图/编码/落盘），记录原画布/编码尺寸/字节数/页数，不推算p95或线上提速。上传、发送、并发、峰值内存未测。

同时生成[盘点](output-gallery/inventory.md)、[字段表](output-gallery/field-map.md)、[草图表](output-gallery/sketch-worksheet.md)、[主题参考](output-gallery/theme-reference.md)副本。本阶段等待用户草图，不自动进入生产改版。

# 现状图片画廊

图片改版前的离线盘点和草图评审工具。调用当前回复准备/绘图函数，保留分页、缺图占位和文字回退；不更改生产主题、查询、发送或部署。

冻结对照使用 `--observe visual`：拦截旧 `_bytes` 和当前布局的编码入口，保存编码前 PNG、像素摘要、每次编码候选及其最终页关联、实际绘制文字位置、完整有序记录与布局范围。多人留影的展示记录不去重。记录实际使用字体和随包字体摘要，不复制字体。`evidence_class` 区分缺素材验证、合成边界和固定输入对照；生成成功不等于人工视觉验收。未执行模板在清单中单列。

性能使用 `--benchmark-runs 3`（或 5），可组合多个 `--only`：每个用例每次启动独立子进程，使用 `--observe timing`，不保存无损画布或逐字绘图记录。输出各阶段中位数、最小/最大值以及操作系统进程峰值（Windows PeakWorkingSetSize / Unix ru_maxrss）；包含解释器、素材、Pillow及编码器，不等同于文件大小或Python分配量。输入为磁盘缓存，不代表冷网络和生产p95。单进程画廊的峰值不可归因到某一个后续样例。

观测工具在 `scripts/output_observation.py`，只由离线画廊启用，不进入应用安装包。字体/素材缺失的样例不得放行对应模板的保行为重构。活动、卡池和人物新增固定 InfoView 边界；它们是声明输入，并非真实活动数据。

在仓库根目录执行：

```powershell
.venv/Scripts/python.exe -B scripts/export_output_gallery.py --output artifacts/output-gallery
```

默认使用仓库公开Haneoka测试夹具和明确标注的合成边界数据；缺原卡面时导出文字回退。`--cache-dir <公开缓存副本>` 补充真实素材，格式与preview_visuals相同（ournotes-cache.json、asset-cache/、chart-cache/、可选preview-details/support-51.json）。该组是Project Yume旧快照，会标注来源，不代表当前默认Haneoka资料版本。

`--font <CJK字体>` 固定字体并记录哈希，不复制字体文件；默认锁定当前绘图器首个可用字体。`--only <模板或用例ID>` 用于单例复核；省略生成完整登记集合。打开index.html，点击图片看images/实际编码原图，thumbnails/为430px派生图。

输入前后哈希校验，网络由audit hook阻断，不刷新数据、不连接QQ、不调用模型。不加观测参数时仍只记录每例一次墙钟/CPU总时长（含准备/绘图/编码/落盘）及输出尺寸/字节数/页数；完整画布与阶段证据须显式启用观测。上传、发送、并发未测，不推算p95或线上提速。

同时生成[盘点](output-gallery/inventory.md)、[字段表](output-gallery/field-map.md)、[草图表](output-gallery/sketch-worksheet.md)、[主题参考](output-gallery/theme-reference.md)副本。本阶段等待用户草图，不自动进入生产改版。

编码实验仅通过 `--encoding-strategy png-first` 显式开启（默认 `legacy`）；可与 `--observe visual` 或独立 `--benchmark-runs 3` 配合。原画布摘要用于证明布局未变，输出实际格式决定扩展名与 manifest MIME。

活动完整单图实验：`--full-activity-view`，默认关闭。分别运行默认、仅 PNG、仅完整活动、两者组合；`--observe visual` 的 pagination 记录区块高度、范围和几何／偏好高度／编码失败原因，保留所有候选画布并单独映射最终页。

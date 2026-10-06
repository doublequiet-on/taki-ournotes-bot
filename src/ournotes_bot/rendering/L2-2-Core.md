# L2-2 Rendering Core

## Purpose

为所有业务图片提供同一主题、字体、素材处理和完整图片编码。它有多个真实消费者和统一输出约束，值得成子域；不是以“通用工具”命名的杂项集合。

## Position inside parent L2

属于 [Rendering](../../L2-Rendering.md) 的共享基础。这里不维护歌曲/卡牌的业务选择与布局语义，也不拥有所有带下划线的绘图函数。

## Inputs

画布/文字/几何参数、允许的素材 URL、素材尺寸与内部 Pillow Image。字体和编码参数直接由实现维护，不在架构文档复制参数表。

## Outputs

内部画布/绘图对象、字体和可用素材；最终编码的 JPEG `bytes`。预算无法满足时抛错；不返回尺寸元组、不上传。

## Core Flow

创建主题画布 → 业务绘图消费字体/素材/通用原语 → `_bytes` 调统一 `encode_image` → 整图缩放/编码直到满足预算。
素材缓存与业务 JSON 缓存不同；缺图产生占位或无素材结果。编码优先保留内容完整性，不删除业务行来压缩图片。
素材完整解码成功后才通过独立临时文件原子发布；旧坏文件每次调用最多沿既有下载路径重取一次，不把 HTML／截断内容固化为可复用缓存。合法旧素材继续复用，尺寸及透明度语义不变。

## Dependencies

Pillow、字体文件、Application `runtime_data_dir`、允许来源的素材端点和网络客户端。
Current 同一 `visuals.py` 还导入业务结果和来源常量；纯基础文件在物理上尚未隔离。

## Boundary with sibling L2-2 modules

[Song](L2-2-Song.md) 和 [Card](L2-2-Card.md) 决定业务画面；Core 不选择歌曲、不辨识卡牌模式。
`_song_heading/_mission_chip` 等歌曲语义归 Song；稀有度框、详情区块等即使被多种卡图使用，仍归 Card，不机械提升为 Core。

## Files belonging to this subdomain

- [image_output.py](image_output.py)：完整归属 Core。
- [visuals.py](../visuals.py)：主题常量、字体/缩放画笔、背景/画布、基础文字/素材辅助及 `_bytes` 的职责切面；文件的业务入口由 Song/Card 描述。

对应 [image_output.py 文件头 L3](image_output.py) 和 [visuals.py 文件头 L3](../visuals.py)。一份文件仍只有一个 L3，不能因多个职责切面复制三份文件坐标。

## Relevant Tests

[test_image_output.py](../../../tests/test_image_output.py)：完整性、透明度、尺寸/字节预算；
[test_visuals.py](../../../tests/test_visuals.py)：背景、抗锯齿、缩放和源素材细节；
[test_install_paths.py](../../../tests/test_install_paths.py)：缺字体行为。
上传前预算检查仍归 [QQ](../../L2-QQ.md) 的交付边界。

## 当前实现与边界

image_output 位于 rendering/，主题、素材、通用原语及 _bytes 仍在根 visuals.py。素材允许表与来源常量、文件/网络副作用均保留。公开 bytes API 和编码策略不变；编码器 logger 继续使用 ournotes_bot.image_output。

向上阅读：[仓库 L1](../../../L1.md)。源码文件头提供唯一 L3，具体行为与字段以实现为准。

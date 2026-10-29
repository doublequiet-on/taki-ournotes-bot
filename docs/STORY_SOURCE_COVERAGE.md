# K1 全系列剧情来源覆盖记录（DRAFT）

取证日期：2026-10-29。范围是 BanG Dream! 官方作品目录、GBP 与 Our Notes 已公开的剧情类别、公开人物介绍及可追溯的出版入口。此表记录来源覆盖和正文缺口，不代表已取得全系列台词、字幕、漫画或游戏脚本。

本次匿名读取官方 HTML，不登录游戏、不获取账号、不下载视频、字幕或漫画图片，不调用模型。链接核验记录保存在忽略的 `runtime/story-source-link-check.json`；真实短样例和导入读回结果分别为 `runtime/story-real-sample.json`、`runtime/story-real-sample-result.json`。这些运行资料不进入提交或公开发行包。

## 来源层级与工程状态

| 材料 | 引擎 source_kind | 使用与审核边界 |
|---|---|---|
| 已核对出处、获准导入的日文原作对白 | `official_original` | 必须有作品版本、章节、说话者与原始定位；本次没有收集完整对白场景。 |
| 官网梗概、人物介绍、原作宣传摘句 | `official_summary` | 官方介绍不等于完整原作场景；即使包含一句原作台词，也保持简介层级。 |
| 现有译文 | `translation` | 记录译者、原文对应和许可，不能覆盖日文原文。本次没有导入第三方译文。 |
| 玩家评论、研究推断 | `fan_analysis` | 与原作分开，不能作为原作事实；社区只可帮助定位待核对出处。 |
| 离线造例 | `synthetic_fixture` | 必须显式允许，不能用作真实来源或人格批准依据。 |

引擎 `coverage` 只有 `collected/missing_source/pending_validation/unavailable`，见 [Bundle 合同](STORY_ENGINE.md#bundle-合同)。表内“目录已核对”“简介可访问”是取证描述，不是新增枚举。只有已经导入实际场景且内容哈希匹配的来源才能标为 `collected`；尚未导入的已定位来源使用 `pending_validation`，未找到确定来源的项目使用 `missing_source`，确认无法获取时才使用 `unavailable`。

## 游戏剧情：已确认类别，正文待获取

| 项目与官方来源 | 本次可核对内容 | 正文及覆盖缺口 | 建议导入状态 |
|---|---|---|---|
| GBP：[攻略指南](https://bang-dream.bushimo.jp/guide/)、[官方 FAQ](https://bang-dream.bushimo.jp/faq/) | 主线、乐队、活动剧情；区域对话；成员 Episode／Memorial Episode 类别和解锁说明。FAQ 还说明部分成员采用 Memorial Animation。 | 页面是功能说明，不提供逐章完整日文脚本；未完成各季、各服、各活动与各卡牌的逐项清单，未核对卡牌全部解锁或原文获取许可。 | `pending_validation` |
| GBP 往期活动：[官方 FAQ](https://bang-dream.bushimo.jp/faq/) | 结束后的活动剧情在游戏内对应入口读取，未解锁内容可能要求乐队等级。 | 不能据此推断任意活动均已公开、全部可匿名导出，或其他服与日服内容一致。须按活动 ID、章节和版本登记。 | `pending_validation` |
| Our Notes：[发行介绍](https://bang-dream-on.bushimo.jp/news/post-8) | 发行时五支乐队各 20 话、合计 100 话，约 24 小时；介绍额外故事、Another Story 与絆故事。 | 这是发行时口径，不是 2026-10-29 的完整存量。公告没有公开全部文本；活动、成员／卡牌、关系支线和后续增量未逐条盘点。 | `pending_validation` |
| Our Notes 后续活动与卡牌故事 | 需要逐活动、逐卡牌核对游戏内入口及官方公告。 | 目前没有完整 ID 清单、正文、更新时间和获取许可；不能照搬 GBP 的分类或剧情到 ON。 | `missing_source` |

游戏查询数据接口或第三方数据库即使公开可见，也不能据此自动认定拥有原作脚本再使用许可。本次没有从第三方页面导入游戏剧情正文。

## 动画：官方目录逐项覆盖

总入口为 [BanG Dream! 官方动画目录](https://bang-dream.com/anime/)。本次页面列出 11 个 TV 条目、6 个剧场条目和 1 个 Web 条目；合并上下篇的目录项按一项计算，不等于影片数量。下表覆盖这些目录项，不把链接存在写成正文已采集。

| 类别／目录项 | 官方入口 | 已确认可访问类型与正文缺口 |
|---|---|---|
| TV：BanG Dream! 第 1 季 | [作品页](https://anime.bang-dream.com/1st/)、[Story](https://anime.bang-dream.com/1st/story/) | 目录已定位；Story 直连本轮 TLS 中断，正文未导入。 |
| TV：BanG Dream! 2nd Season | [作品页](https://anime.bang-dream.com/2nd/)、[Story](https://anime.bang-dream.com/2nd/story/) | 官网页面／章节简介入口；完整字幕或脚本未取得。 |
| TV：BanG Dream! 3rd Season | [作品页](https://anime.bang-dream.com/3rd/)、[Story](https://anime.bang-dream.com/3rd/story/) | Story 可读取；主页直连本轮 TLS 中断。完整脚本未取得。 |
| TV：ガルパ☆ピコ 第 1 季 | [系列主页](https://anime.bang-dream.com/pico/)、[第 1 季 Story](https://anime.bang-dream.com/pico/story/1st/) | 官方目录与分季简介；未导入完整对白。 |
| TV：ガルパ☆ピコ～大盛り～ | [系列主页](https://anime.bang-dream.com/pico/) | 与另外两季共享目录链接；逐季正文未取得，不能用首页替代第二季场景。 |
| TV：ガルパ☆ピコ ふぃーばー！ | [系列主页](https://anime.bang-dream.com/pico/)、[Story](https://anime.bang-dream.com/pico/story/) | 分集简介与官方观看链接；未访问视频或提取字幕。 |
| TV：Morfonication | [官方页](https://morfonica-anime.bang-dream.com/) | 目录与宣传入口，未导入字幕／对白。 |
| TV：It's MyGO!!!!! | [官方页](https://anime.bang-dream.com/mygo/)、[Story](https://anime.bang-dream.com/mygo/story/) | 13 集标题、简介、选取引句、制作信息与图片；没有完整字幕／脚本。本次短样例来自第 5 集简介。 |
| TV：Ave Mujica | [官方页](https://anime.bang-dream.com/avemujica/)、[Story](https://anime.bang-dream.com/avemujica/story/) | 13 集标题／简介；没有完整脚本。 |
| TV：元祖！バンドリちゃん | [官方页](https://anime.bang-dream.com/bandorichan/)、[Story](https://anime.bang-dream.com/bandorichan/story/) | 官方作品、章节介绍入口；没有导入完整对白，喜剧表现单独保留作品边界。 |
| TV：バンドリ！ ゆめ∞みた | [官方页](https://anime.bang-dream.com/yumemita/)、[Story](https://anime.bang-dream.com/yumemita/story/) | 13 集简介可读取；主页直连本轮 TLS 中断，完整脚本未取得。 |
| 剧场：Ave Mujica prima aurora | [官方页](https://avemujica-movie.bang-dream.com/) | 官方宣传／作品入口；未取得原作对白，不据页面存在推断上映、放送或资料完备状态。 |
| 剧场：MyGO!!!!! 前篇／后篇 | [官方页](https://mygo-movie.bang-dream.com/) | 官方目录合并上下篇；未取得影片字幕或新增场景对白。 |
| 剧场：Episode of Roselia I／II | [官方页](https://roselia-movie.bang-dream.com/) | 官方目录合并两部；未取得影片字幕或对白。 |
| 剧场：ぽっぴん'どりーむ！ | [官方页](https://popipa-movie.bang-dream.com/) | 宣传／作品入口；对白未采集。 |
| 剧场：FILM LIVE | [官方页](https://anime.bang-dream.com/filmlive/) | 演唱会动画作品入口，不自动转换为叙事脚本语料。 |
| 剧场：FILM LIVE 2nd Stage | [官方页](https://anime.bang-dream.com/filmlive2/) | 演唱会动画作品入口；未提取歌词或字幕。 |
| Web：ぱすてるらいふ | [官方目录所链视频](https://www.youtube.com/watch?v=kgvP3pWKQBk) | 已从官方目录定位链接；本次未访问该视频、未确认可用地区／字幕或集数覆盖。 |

上述动画来源的待导入状态为 `pending_validation`；没有已核实正文的条目不生成虚构场景。游戏、TV、剧场重编、漫画与喜剧支线分别设 work／版本，不将相似标题合并为同一剧情事实。

## 小说、漫画与其他载体

总入口：[官方 Media 目录](https://bang-dream.com/media/)。本次核对了目录中的小说、漫画类别及外链；出版方外链的存在不等于授权批量提取图片、OCR 或再分发文本。

| 类别 | 目录所指来源 | 本次状态／缺口 |
|---|---|---|
| 原典小说文库版 | [官方书讯](https://bang-dream.com/media/novel/) | 可核对出版信息和简介，正文未取得；不能据“原典”覆盖动画／游戏的不同设定。 |
| MyGO!!!!! 雨にそよいで晴れを請う | [Comic Growl 出版入口](https://comic-growl.com/series/4c21dc7ce230d) | 官方目录外链已核对；本轮直连 TLS 中断，未抓取图片／正文。 |
| Ave Mujica -manuscriptus- | [Comic Growl 出版入口](https://comic-growl.com/series/3f8e64316138a) | 出版页面可访问；未逐话核对、下载或 OCR，不声称完整可读。 |
| コミック版 BanG Dream! バンドリ | [月刊 Bushiroad 出版入口](https://gekkan-bushi.com/comics/comic-bang-dream-km/) | 官方目录外链已核对；本轮直连 TLS 中断，正文未取得。 |
| バンバンドリドリ | [CoroCoro 出版入口](https://www.corocoro.jp/title/870) | 官方目录外链已核对；本轮直连 TLS 中断，正文未取得。 |
| Roselia Stage | [Comic Gardo 出版入口](https://comic-gardo.com/episode/3269754496572231358) | 出版页面可访问；未逐章取得正文。 |
| GBP The first page | [官方漫画页](https://bang-dream.bushimo.jp/special/comic/) | 公开的分乐队介绍及漫画入口；页面明确禁止二次分发、加工。本轮直连 TLS 中断，官方搜索索引可核对文字说明；不导入漫画正文。 |
| 舞台、广播剧、音声特典、MV 叙事、其他书籍或联动 | 尚未完成逐作品的一手来源盘点 | `missing_source`；本轮目录不证明这些载体或所有历史作品已穷尽。 |

小说／漫画已定位条目为 `pending_validation`，完整日文正文及使用许可仍待确认。没有使用玩家转载全集来补足这些缺口。

## 使用范围与人工确认缺口

已核对 [项目二次创作指南](https://bang-dream.com/bdp-guideline/) 与 [Our Notes 规约入口](https://bang-dream-on.bushimo.jp/rule/)。这些页面不作为剧情全文语料再分发的通行许可；本次没有确认全量脚本、字幕或漫画正文的批量获取与再发布授权。公开网页的极短引用使用 `public_reference`，不是 `licensed_reuse`。

后续每份来源需补：内容提供者／权利来源、允许用途（本地研究／模型上下文／公开展示／发行）、取得日期、完整性范围、可撤回范围。用户确认研究方向与素材处理方式后，按已获准材料导入；人格是否成立仍另行人工审核。剧情正常讨论不额外加入默认防剧透策略，也不把某次展会试玩的发布规则扩大成全系列永久规则。

## 真实小样及校验口径

唯一真实样例是 MyGO TV 第 5 集官方简介的一句选取引句。官网简介明确把它归于立希对爱音的发言；样例不补写对方回答、不构造前后对白、故事内日期或相邻场景，`previous_scene/next_scene/story_time` 均为空。日文正文保留原句，`translation` 明示为本次派生中译而非官方译文。

样例为 `official_summary/public_reference/collected`，`claims/personas` 均为空，不冒称原作完整场景。`source_content_hash(scenes)` 对实际场景记录计算哈希，区别于页面 HTML 快照哈希和台词字符串哈希。校验链为 `parse_bundle`（默认拒绝 fixture）→ `StoryService.import_bundle` → 中文别名检索 → 通过返回 handle 读回 → 确认无已批准人格。

离线读回只证明这一条真实资料可经过本地来源、别名和原文检索链路；不证明大规模检索质量、全系列采集完成或人格表现已验收。候选人格与反例见 [研究记录](STORY_PERSONA_RESEARCH.md)。

## 链接核验结果与下一批资料工作

本轮 42 个匿名 HTTPS GET 中 34 个返回 200，8 个发生 TLS 握手 EOF；失败条目已在表内注明。失败不按 404 或内容不存在处理，也不写成直连成功。搜索索引用于补核 Ave Mujica 人物介绍及 GBP 官方漫画页的公开文字，未替代获取正文。网页 HTML 哈希可能包含动态内容；只作本次获取记录，不能当上游固定版本或全语料哈希。

下一批应先确定获准的正文提供方式，再逐作品／章节建立缺口清单；按完整交流片段导入日文原文及独立译文；保存章节顺序与关系／压力语境；进行中文昵称和情节描述的真实命中评测；最后由人工统一处理人格候选、反例和剧情时点。没有依据的旧喜好继续标 `legacy-derived`。

# R7–R9 固定证据复现

现行结论、版本和接受的成本只在 [性能说明](../../docs/PERFORMANCE_FOUNDATION.md#r7r9-收尾证据2026-10-28) 维护。本目录保存可复用的小脚本；不随机器人运行或发布安装，不接生产配置。

## 取得并检查证据

先从最终 A 的 GitHub Release 下载 `taki-r7-r9-evidence-20261028.zip`，用[已审阅索引](../../docs/performance/r7-r9-evidence-20261028.json)中的 SHA-256 核对。索引的 publication 字段说明是否已经发布；仅有本地 ZIP 不代表已有下载地址。

```text
python -X utf8 -B scripts/performance/verify_evidence.py <archive.zip> <index.sha256> --extract <全新目录>
python -X utf8 -B scripts/performance/r7-r9b/audit-chain.py <全新目录>/r7-r9b/chain-abba-final/all-runs.json
python -X utf8 -B scripts/performance/r7-r9b/audit-chain.py <全新目录>/r7-r9b/card-followup-abba/all-runs.json
```

校验器只校验归档及成员摘要，不执行归档代码。解包目录必须不存在。manifest 为每个文件保留原始与公开内容摘要；操作员目录名、机器名和 BOM 的规范化不改变测量值。原始未规范化材料在本机保留。`baseline.tar` 是重复源码快照，未公开打包；源码由固定 Git 提交取得，实验改动另存为 diff/patch。

## 重新运行的环境与输入

使用独立 Linux clone 和全新输出目录。固定 Python/Node/Pillow/SQLite、依赖锁、项目字体及原生音符素材；逐轮 JSON、`python-freeze.txt`、`node-version.txt`、`kernel.txt`、`limits.json` 和编译/输入指纹是原实验环境记录。原实验为 4 CPU、4 GiB、禁止 swap；这不是当前生产主机的 swap 配置。

所有 benchmark 只使用合成目录、图片和知识文档，平台和模型提供方均为本地桩。清空 QQ、模型、Moenotes 等凭据；不要在生产 checkout、数据目录或有真实配置的环境运行。`prepare_chain.py` 会修改传入的两个实验 clone 中的基准入口，不能对开发或生产工作区执行。

| 脚本 | 输入/用途 |
|---|---|
| `r7/benchmark.py` | `--repo <candidate> --baseline-source <r7/usage_ledger_baseline.py> --output <new.json>`；用量索引合成矩阵，默认 2k/20k/200k 行。基线源摘要在归档。 |
| `r7-r9/run-chain.py` | 同版本依赖的 `--base --new --node --assets --output`；五轮 ABBA，固定 c1/c2/c4。 |
| `r7-r9b/knowledge_benchmark.py` | `--base <59d0e67> --new <c2d9ef2> --output <new-dir>`；5/50/200 文档、1/6 chunk、1/5 part 局部校验。 |
| `r7-r9b/detail_benchmark.py` | `--base <59d0e67> --new <ee2ad4d> --output <new-dir>`；真实 Pillow、多语言/关联数量/冷热详情。 |
| `r7-r9b/prepare_chain.py` | `<实验根目录>` 下必须有 `base`/`integrated` clone 和 `evidence`；应用相同固定模型/知识夹具并输出 harness 指纹。 |
| `r7-r9b/run-card-followup.py` | 参数同 run-chain；只复核 card-typed c4，保留原初轮异常。 |
| `r7-r9b/summarize-chain.py` | `--input <chain-dir> --base-head <SHA> --candidate-head <SHA>`；从逐请求事实重算统计，拒绝输入不一致。 |
| `round2/materials-final/compiled-rollback.cjs` | 复制到独立输出目录，同目录放实际 TS 编译的 `materials-a.js`/`materials-b.js`/`materials-baseline.js`；运行时依次传 A、B、旧版的完整 SHA，报告编译文件摘要。临时合成账本验证格式回退，不使用真实平台。 |
| `round2/verify-material-gate.py` | `--repo --controller --old --a --b`；执行真实指纹/契约判断，POSIX 属主检查使用桩；输出不是可安装的生产策略。 |

第二批整链路 base=`59d0e67d354ee5c0db61fb76cac77d2d2d22734b`，new=`6c57d6b54cb9138189558469f58301fd03e0e7a9`。先运行 prepare_chain，再以如下前缀运行各 Linux 基准：

```text
systemd-run --user --scope -p CPUQuota=400% -p MemoryMax=4G -p MemorySwapMax=0 env QQ_APP_ID= QQ_APP_SECRET= AI_API_KEY= OURNOTES_QUERY_AI_API_KEY= MOENOTES_OPEN_SECRET= BDON_OPENPLATFORM= OURNOTES_AI_PROFILE_FILE= <locked-python> -B <script> <arguments>
```

整链路使用 `--rounds 5 --requests 8 --scenarios light,hot,cold,chart,mixed,ask-mixed,knowledge,knowledge-mixed,card-typed`；c4 定点复核使用 `--scenarios card-typed`。运行结束先检查 audit，再解释汇总。初轮/修正后的数据全部保留，不用反复测量筛选较好结果。

归档里的脚本保持原实验版本；本目录要求 R7 显式指定 repo、把临时库放在输出目录下、拒绝复用整链路输出目录，并让回退报告记录本次版本和编译摘要。局部汇总 `r7-r9b/summarize.py` 复制到解包后的 `r7-r9b` 目录运行，读取相邻的两份 runs 文件。对新代码再测须记录新的代码和脚本指纹，不能沿用历史结果标签。

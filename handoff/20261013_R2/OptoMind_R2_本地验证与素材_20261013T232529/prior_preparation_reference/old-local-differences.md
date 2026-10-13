# 三个旧目录的版本、差异与资产处置

这是只读盘点，不是合并清单。完整路径、存在性、Git blob / 小型源码 SHA、换行差异及处置标签存于 `old-Review-2-files.csv`、`old-Article-1-files.csv`、`old-Article-2-files.csv`。CSV 包含被 Git 忽略的产物名称与元数据，排除 Git、虚拟环境和 Python 缓存；没有复制这些文件。它们不是源码备份。

## 版本事实

| 旧目录 | Git 状态 | VeriTMM / 运行资产 |
|---|---|---|
| `F:\OptoMind-Review-2` | local-upgrade；`612cdce7377868eb1e62bc6b9201599df67b65b2`；dirty | 无嵌套仓库；正式入口 run_review_harness.py；outputs、文献材料与账本真实存在 |
| `F:\OptoMind-Article-1` | main；`b799c79e70601ad6ffbcfa8c555ac010064b1770`；干净 | veritmm 由主仓跟踪，没有嵌套 .git；6 个正式 TMM E2E 目录，约 2.92 GB / 10327 文件 |
| `F:\OptoMind-Article-2` | 递归未找到 .git；branch/commit/remote/dirty 均无法由该目录认证 | 没有 veritmm、正式 outputs、accepted_examples 或 article_memory；保留六候选历史实验 |

Review fetch remote 为 `https://github.com/Lihonggang-scnu/OptoMInd-Review.git`，push URL 为 localhost 的 GITHUB_PUSH_DISABLED 地址。旧说明与实际 remote 状态不一致，已记录；本轮保留实际状态，不修改 remote/hook。Article-1 origin 为 `https://github.com/Lihonggang-scnu/OptoMind-Article.git`。未切换任何旧目录分支。

目录编号不能决定版本。Article-2 与 Article-1 的 206 个共有源码/配置文件字节相同，缺少 Article-1 另 164 个 code 文件；Article-1 与云端锁定 bee5fe11 也不相同。Review 旧 HEAD 与锁定 37a482cc 有大范围归档/整理差异，不能整体搬运或视作待合入增量。

## 与锁定基线的逐文件比较

在排除输出/归档后的代码配置分类中，Review 有 25 个内容不同路径，另有 287 个本地独有代码/配置/测试路径；Article-1 有 38 个不同路径，Article-2 有 12 个不同路径。这里的“不同”表示原字节及仅 CRLF→LF 后仍不同，不表示旧侧更好或更新。仅换行相同的文件单列 `crlf_only_equal`，未据此放宽补丁检查。

Review 不同路径集中在阅读卡片/批量、按需阅读、规划、编排、单元作者、编辑、delivery、编号、LaTeX 和主入口。287 个独有路径包括 57 个 scripts、216 个 tests、3 个 config、2 个 benchmarks、7 个 replay JSON 及 2 个根文件；完整名单在 `old-source-selected-differences.json`。三个独有配置是 `R6_TOPIC_MANIFEST.json`、`publication_metadata_optomind_e2e.json`、`query_plan_tuning_set.json`，都与旧题或历史评估有关，不能直接作为新题配置。

Article 不同路径涉及 model_policy、研究循环、路线规划/反思、评分、策略、任务编译、veritmm_adapter、主入口及内含 VeriTMM 的协议/执行/梯度等。不存在可据目录编号自动覆盖云端基线的依据。当前只完成调用链阅读和字节/对象差异盘点，未对每个差异作合并取舍或实施修复。

## 必须保留来源

- 两个 Review 历史缺件及其真实引用链：DELIVERY_MANIFEST、UPDATED_WRITER_PACKET；已记录大小、SHA-256，分别 12/12 编排引用、24/24 卡片、24/24 阅读目录存在。
- 585 行 INPUT_POOL 与全部 585 个唯一卡片路径（574 主批次＋11 补充），选定 200 的 ID 映射、batch state、原始阅读材料、已接受 BODY/首尾/校验和出版产物。保留原题、版本、来源及预算身份，不用新造 fixture 替代。
- Article-1 的真实运行记录、证书、排名、accepted_examples（518 文件）与 article_memory（4 文件）；只在后续明确需要时选择单个来源副本。
- 所有旧目录及原凭据留原处。没有读取或搬运密钥，没有批量搬运数据库、全文、环境、缓存或大型输出。

## 需要取舍

Review 的旧版生产模块差异、57 个独有脚本、216 个独有测试及配置；Article 的 38/12 个源码配置差异；旧提示模板、内含 VeriTMM 版本、静态回放和材料 CSV。下一阶段若出现具体消费者缺口，按函数、输入合同和证据选择，禁止整目录覆盖。Article 内含 veritmm 只能由 Article.patch 处理，独立 VeriTMM.patch 只用于独立仓库。

## 仅作历史记录

Article-2 `research/experiments/2026-09-22` 的 C1–C6、foundation_smoke、source_snapshots、成本审计与负结果报告（390 个非 pycache 文件）；它们没有产生可接入主链的已证实创新或赢家。旧工单、advisor/archive_export、临时报告、静态站点和诊断样例也仅保留为历史，不自动进入生产。

## 真实运行链与证据入口

Review 的问题→子问题→查询→取得/转换→卡片→池→规划→编排/写作→checker→04编号→05出版，函数与基线/补丁行号见 `review-audit.md`。Article 的任务配置→文献路线→研究循环→VeriTMM→结果消费→恢复→继续写作/出版及 science stage-7 接缝见 `article-audit.md`。

Article-1 默认 TMM 产物在 `code/outputs/tmm_research_harness/<run_id>`；旧文章入口的 ExperimentStore 使用当前工作目录下 `runs/<problem_id>/<run_id>`，不能只按文件名猜产物。Article-2 的独有产物在 `research/experiments/2026-09-22/<candidate>/artifacts/...`。Review 真实材料的详细来源、卡片与正文引用核对见 `historical-assets.md/json`。

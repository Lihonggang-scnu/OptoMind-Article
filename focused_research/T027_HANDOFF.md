# T027 本地有界真实搜索与模型决策联测

状态：待本地真实联测。仅按配套 ready 工单给出的固定实现 SHA 领取；ready 不等于真实性能验收。

## 目标与范围

T026 已证实接线，但次轮真实干预未改善。T027 检验：既有结构族实际数值优化、已认证优秀结构保留、物理诊断进入模型、模型选择具体父结构/参数化后再次数值搜索。

云端负责核心代码；本地仅做测试与简单环境/启动接线修复。不要修改科学答案、替模型选厚度、放宽失败的物理验收，也不要重检索/PDF解析、开新题、安装依赖或额外跑消融实验。历史 T022–T026、原副本、所有账本和 main 均保留。

## 固定输入与预算

- 基准 SHA：`5786e08575b19020a6aece338436406884d3542d`。
- T026 实测证据：`c3f35a9cb0af4bf393c6d4a497055324ec27727c` 下 `handoff/R3-T026-PHYSICAL-FEEDBACK-20261016`。
- 从 T026 留存读取原 T025 第二轮草案、任务单、证据、设计约束、材料映射与波长网格，不重新生成第一模块。
- 仍用 T026 的 RII TiO2/Siefke421、SiO2/Gao410、fused_silica/Malitson835；数据库与材料前检复核，不换成常数折射率避开失败。450–1000 nm、1 nm 网格，45°，原评分 s/p 等权；原空气与半无限基底假设保留。
- **本任务付费上限3元，最多1次实际模型 HTTP请求**（无自动传输重试、无格式修复追加）。沿用本地原计费配置及模型，纳入原15元链、20元子池、140元总预算。先核累计及 pending/held/uncertain；T026最近累计17.8040334元、占额0仅作待核对基线。
- **两阶段合计最多8192个搜索前向设计行、128次新认证评价，最多2个认证进程。** 第一阶段4096搜索行/64认证；第二阶段4096/64。两者分开统计；不能把优化内部数千次求解称为1次。数值差分与失败均计入，实际计数不足时不得擅自重置预算重试。
- 默认额外保留 s/p 通道用于诊断，原非偏振目标不变；第一次 T027 的源码身份和通道集合不同，T026 光谱缓存会失效，这是正常重算。仅同 T027 完整物理输入/源码/材料身份的缓存可复用。

## A：领取、独立目录、零费用针对性测试

先按 message_id/task_id 去重并留 claimed。核对 bundle、基准与实现 SHA，放入独立短路径，不覆盖其他开发目录。复用原 tensor 环境，确认 torch/scipy/tmm 可导入；不得为本任务私自安装。

在仓库根执行：

```text
python -m pytest -q focused_research/tests/test_family_optimization.py focused_research/tests/test_design_archive.py focused_research/tests/test_physics_diagnostics.py focused_research/tests/test_search_stage.py focused_research/tests/test_execution_failure_diagnostics.py
```

云端最终450 passed、3 skipped、6 subtests passed，禁网执行。

本地必须实际运行 PyTorch专项：与 NumPy 每目标一致、共享变量解析梯度与有限差分一致、失败预算计数。云端无 torch 的 skipped 不代表这部分已验证。若本地 torch 路径失败，保留证据回传，勿悄悄切换计算方式后称原路径通过。

## B：原结构族有界数值搜索

以下大写路径由启动器填为真实本地文件，不是要求创建新的科学输入。

```text
python -m focused_research.search_stage search --task-sheet TASK_SHEET.json --evidence-file EVIDENCE_ITEMS.json --constraints-file DESIGN_CONSTRAINTS.json --routes-file T025_ROUND2_RAW.json --optical-media OPTICAL_MEDIA.json --wavelengths WAVELENGTHS.json --cache-dir NEW_T027_CACHE --max-forward-evaluations 4096 --max-new-evals 64 --max-workers 2 --backend torch --parameterization family --seed 42 --execute --output NEW_T027_SEARCH
```

只使用真实数值结果。输出 partial 不一定需要重跑：数值候选超过认证额度时，已认证档案仍有效；报告未认证数量即可。所有种子或全部数值搜索失败时停止，不浪费模型请求。保留每个原目标，评价正式档案而不是未认证搜索分数。

## C：一次模型决策

使用本地已有 audited planner 注入：

```python
from focused_research.search_stage import main
main(['revise', '--previous-search', 'NEW_T027_SEARCH',
      '--execute', '--output', 'NEW_T027_REVISION'], planner=existing_audited_planner)
```

启动器仅接入原生计费和一次 HTTP 限制，不替模型编计划。模型选择已认证父结构和 refine_family/refine_layers，解释具体观察与预期。原响应必须保存。部分计划格式有问题时可采用程序认可的其余计划，但不得替模型补造物理理由。

核对实际请求确实含父结构实际层厚、原目标指标、物理诊断、原路线依据。不要只核对“字段存在”，检查它有没有引用有意义的变化，并保留原请求/响应。

## D：按模型计划再次搜索并认证

```text
python -m focused_research.search_stage refine --previous-search NEW_T027_SEARCH --revision-dir NEW_T027_REVISION --cache-dir NEW_T027_CACHE --max-forward-evaluations 4096 --max-new-evals 64 --max-workers 2 --backend torch --seed 43 --execute --output NEW_T027_REFINED
```

如果无有效计划，保存原因并停止，不追加第二次模型调用。此轮从明确父设计启动；原档案继续保留。逐层细化只能解绑原可变层并沿用原范围，材料/层数/固定层不变。实际收益以认证光谱重算目标为准。

## 验收与报告

至少给出以下四组事实，分别判定，不用一个 completed 掩盖性能未达标：

1. **工程与预算：** 搜索所有前向设计行/通道行、反向传播次数、认证评价、缓存命中、失败数、CPU耗时、模型实际HTTP/tokens/费用；两个数值预算及付费限制均遵守。
2. **性能：** 原种子、第一阶段正式非支配设计、第二阶段正式非支配设计的 G1/G2。报告双目标同时改善的实际父子对，也保留权衡与退步；旧好解没有消失。
3. **决策：** 选一条实际执行链，列父结构ID→物理观察→模型理由→所选参数化→数值搜索→认证结果。新结构是否实现预期、物理解释是否有证据分别评价。s/p诊断不是内场机理证明。
4. **能力边界：** 搜索未认证候选不能当最终成果；没有制造、公差或连续频谱保证。若没有优秀结果就如实partial，不用更多轮数刷掉失败记录。

保存普通报告、必要JSON、紧凑模型上下文、原模型输出、选中及对照光谱、证书和数值搜索日志。不要上传密钥、账本数据库、RII数据库、PDF或权重。上传与回读核验后给 completed/partial/blocked 回执并停止，不自动开展第三阶段。

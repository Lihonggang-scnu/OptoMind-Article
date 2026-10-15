# T030 本地交接：方向 → 具体分支 → 数值结果 → 模型反思

## 启动门槛与范围

先等待 T029 完成并核对终态、实际费用及未结算请求；不要与 T029 争用同一费用预留或本地进程。以配套 ready 工单绑定的固定实现 SHA 为准。T028 固定结果提交为 cead25388c4fd28468d51ff70361e897214eb911；本文件不假定本地完整档案路径。不得直接把下列大写占位符当作真实路径。

本轮只继续原 RGB/NIR 案例：使用本地 **T028 完整搜索档案**及先前保存的 **完整粗计划**，不重新检索、生成原路线或重新执行基线。任务是验证有意义的假设如何变成实际搜索分支、结果如何影响下一步判断；不要求分数提升，不做正式对照或消融。

流程最多两次实际 Qwen HTTP：一次具体分支展开，一次搜索后的分支反思。中间执行总计不超过 **8192 个搜索前向设计行、128 次新增认证评价**，搜索进程最多 2、认证进程最多 2、backend=torch。反思只保存下一步粗计划，**不再执行**。

## 费用与审计，先于任何付费请求

- 使用本地已有 audited planner、原 Qwen 接口、原密钥加载方式和原持久账本。这里的 API 只接受 planner 注入，不构造客户端。不要新增密钥、客户端或账本池；不要套用仅服务 T029 初始三阶段的 CampaignPlannerGuard 来冒充本轮计费控制。
- 本案例累计额度 **8 CNY**，本轮可用额度须扣除该案例所有先前实际支出和有效预留。项目累计上限 **220 CNY**。C2L-R3-0044-BUDGET 已把本次获授权案例活动的旧 15/20 元任务池限额更新为上述新口径；保留原账本及历史归属，不另外创建可重复消费的池。不是给本轮额外增加 8 元。 有效可用额度取 min（任务 8 元、同案例 8 元减全部历史支出和有效负债/预留、项目 220 元减累计和有效负债/预留）；历史始终保留，不重置。
- 每次调用前读取原账本，核对 paid、held、pending、uncertain 及并发占用，在原账本中预留足以覆盖该调用的保守上界。任一层不足或存在不能判明的请求结果，暂停依赖步骤并报告。
- 整个任务最多 **2 次真实 HTTP 请求**，不是 2 次 Python 方法调用。原 transport 必须关闭所有自动重试、超时重发、格式修复重试和 fallback 请求；失败也计次数与费用。不因为程序返回失败就重建 planner 重试。
- 展开后按实际 usage/transport 和账本回读结算，再为反思检查和预留剩余额度。若反思额度不足，保留已经完成的搜索档案，报告 partial，不追加调用。

## 路径与来源核验

由本地执行者选定并记录以下实际路径及摘要：

- `T028_FULL_SEARCH`：原案例完整搜索输出，含 manifest、context、design_archive、design_envelopes、physics_diagnostics，不能用紧凑上传导出代替。
- `PRIOR_COARSE_REVISION`：先前粗计划的原始 revision 目录及其 refinement_plans.json、原响应、实际 prompt 和 manifest。
- `EXISTING_CACHE`：该案例原认证缓存。是否命中由当前执行器的输入/源码/材料身份检查决定，不删除身份字段。
- `NEW_T030_EXPANSION`、`NEW_T030_SEARCH`、`NEW_T030_REFLECTION`：三个全新短目录，不覆盖任何旧目录。

先用 `search_stage.read_search(T028_FULL_SEARCH)` 验证全部绑定。对原粗计划目录逐项校验 manifest 的 JSON 摘要，记录原 revision 的父搜索绑定及原响应摘要；确认每个选用的 parent_design_id 存在于当前 T028 档案，且上下文、全目标和物理条件相同。原粗计划若来自更早快照，不伪改其原绑定，也不声称模型当时已看过 T028 新结果；记录“旧计划由本轮展开模型结合当前父结构具体化”。别名只能使用原 revision 的映射还原真实 ID，不能套用新快照的 P 编号。

独立保留原基线、历史档案和来源记录，不能仅依赖新输出中的合并档案。缺失完整原件、身份不一致或无法确定真实父结构时停止并回传缺项，不手造 manifest、不改模型理由。

## 先做本地零费用测试

复用已有 tensor 环境，不安装依赖或改材料。先确认 `python -c "import torch; print(torch.__version__)"` 成功，再运行：

```sh
python -m pytest -q focused_research/tests/test_family_optimization.py focused_research/tests/test_direction_expansion.py focused_research/tests/test_directional_stage.py focused_research/tests/test_directional_reflection.py
```

Torch 梯度、失败计数及 `test_torch_provided_only_narrow_bounds_when_available` 必须实际通过，不能把缺 Torch 的 skip 当作本地验证通过。定向测试通过即可，不要求重跑全部历史测试。若出现错误，保存原证据后停止付费步骤。数值进程采用 spawn，实际执行使用下面的模块 CLI；不要从 stdin 启动多进程搜索。

## A：一次 Qwen 展开

在本地现有审计启动器中调用以下真实 API。`existing_audited_planner` 是该启动器已经初始化并落实上述 HTTP/费用限制的对象，不是本文件定义的新变量构造器。路径须先替换成已核验的实际值。

```python
from pathlib import Path
from focused_research import search_stage as st
from focused_research.direction_expansion import expand_directions

plans = st.load(Path('PRIOR_COARSE_REVISION') / 'refinement_plans.json')
result = expand_directions(
    Path('T028_FULL_SEARCH'), plans,
    output=Path('NEW_T030_EXPANSION'),
    planner=existing_audited_planner,
    max_forward_evaluations=8192,
)
```

完整保留 prompt、raw_response、parsed response、context_bindings、validation report 和计费记录。模型选择具体变量赋值和可选搜索窗口；程序只校验原边界、结构、父身份与全目标，不把自然语言通过固定规则替模型翻译。允许部分分支无效而其余继续；无可执行分支则保存原因并停止，不再付费修复或硬凑分支。

## B：真实并行分支搜索，随后统一认证

仅当展开产生通过校验的可执行分支时运行：

```sh
python -m focused_research.directional_stage execute --previous-search T028_FULL_SEARCH --expansion-dir NEW_T030_EXPANSION --cache-dir EXISTING_CACHE --output NEW_T030_SEARCH --max-forward-evaluations 8192 --max-new-evals 128 --search-workers 2 --max-workers 2 --backend torch --seed 45 --execute
```

CLI 重新校验展开 manifest 和父快照后执行。8192 行在启动所有分支前预分配，不是每分支 8192；失败、差分及数值继续搜索均计入。初始化为 provided_only，从模型给出的具体种子开始，遵守可选窄窗口；窗口是搜索策略，不是新增物理约束。全部搜索结束后，先零新增额度探测实际缓存，再进行一批共享 128 上限的认证。不能把优化多步算作一次认证成本。

退出码 2 或 partial 不等于需要重跑：读取 summary、input_failures、认证结果与 branch_feedback。额度造成的 deferred_unvalidated 不是科学失败；未经认证的数值候选不算成果。保留旧好解、所有原目标、真实 parent → expanded seed → numerical candidate 归因，缓存重发现不算独立新证据。

## C：一次带分支反馈的 Qwen 反思，然后停止

搜索档案通过 `read_search`，branch_feedback.json 已绑定 manifest，且有可解释的结果或失败记录时，使用下列专用 API，而不是直接调用普通 revise。先再次完成原账本结算/预留检查；全部搜索因环境或输入错误失败时不浪费反思请求。

```python
from pathlib import Path
from focused_research.directional_reflection import reflect_on_branches

reflection = reflect_on_branches(
    Path('NEW_T030_SEARCH'),
    output=Path('NEW_T030_REFLECTION'),
    planner=existing_audited_planner,
)
```

该调用把紧凑分支反馈注入普通 revise，保留实际转发的 directional_reflection_prompt.json 及绑定。反馈含原粗解释、分支理由、实际种子、窗口、父指标、真实认证设计的完整目标向量与变化、缓存/新证据/延期/失败计数。每目标极值必须是实际设计，不能拼出不存在的共同最优点。完整原始数值搜索结果留在外部档案，不塞进 prompt。

反思返回的下一步粗计划仅保存，无论是否建议继续均不执行下一轮。不因小格式问题或不同意见额外请求模型；保留程序接受/拒绝报告及原始响应。

## 交付与评价

- 给出一条或数条真实链：原父结构与观察 → 粗假设 → Qwen 具体分支与理由 → 实际数值搜索 → 认证结果 → Qwen 如何修正下一步。
- 评价假设是否有意义、动作是否对应理由、反思是否读对事实；允许探索、权衡、负结果及不同解释。局部导数不证明有限步必提升，有限预算失败不证伪机制。错误符号或虚构观察如实单列，勿把它们伪装成有价值的科学失败。
- 工程、科学推理、目标表现分开报告。全目标同时改善不作为通过条件；保留 unchanged、tradeoff、all_worse 和未认证结果。
- 报告固定实现 SHA、输入/模型请求响应摘要、实际模型 HTTP/tokens/费用、原账本回读摘要、全局与案例累计/预留、数值行/通道/反向次数、实际缓存、认证、失败、CPU与墙钟时间。不能将进程 CPU 重复相加。
- 交付简明报告、必要 JSON、完整本地档案引用、代表真实父子曲线及证书。不要上传密钥、账本数据库、材料数据库、PDF或权重。按 ready 工单渠道上传并回读核验后给 completed/partial/blocked 回执；未回读不得声称交接完成。

# VeriTMM 下一阶段改进方案（v3.1 · 正式冻结）

> **状态：终版评审 9.5/10，Approved for implementation。本稿为正式冻结版，
> 实施自 M0 起；实施顺序：M0 hash convergence → VerificationEvidence 抽层 →
> verify-run，MCP 最后。**
>
> **v3 修订（全部经代码核实后采纳）**：
> ① `verify-run` 信任语义拆分为 integrity / certification / replay / authenticity
> 四态，并诚实声明 1.1 无外部 trust anchor 的能力边界；
> ② M0 升级为**全库 hash convergence**——已核实 `acceptance.py:47` 的
> `_stable_hash` 缺 `ensure_ascii=False` 且承担 `certificate_id`、
> `certificate.task_sha256`、`material_provenance_sha256` 的计算，库内目前
> **并存两种规范化约定**；
> ③ 引入 **VerificationEvidence 抽层**与 legacy v1.0 工件的核验路径；
> ④ MCP 的 task 引用限定 inline / resource / server-owned task root；
> ⑤ Analytic oracle 明确定位为 **CI validation oracle**，不进运行时证书。
>
> **范围**：信任闭环、agent 原生分发、规模化与出处、实验性物理扩展、社区。
> **不在本稿范围**：评测外部效度（真实 LLM AgentBench 与公开轨迹数据集，另行推进中）。
> **基线**：当前工作区代码，对照已发布 PyPI `veritmm 1.0.0`。

---

## 发布主线

```text
VeriTMM 1.1 — Trust + Agent-native
    canonical identity → portable evidence → deterministic verification
    → agent-safe MCP → reproducibility
VeriTMM 1.2 — Scale + Provenance
    并行、成本估计、DSSE 签名、Streamable HTTP、静态查看器
Experimental track（独立，不进 1.1/1.2 发布承诺）
    Berreman 4×4、材料不确定度框架、更大物理范围
```

1.1 的发布故事：**"VeriTMM 1.1 makes verified multilayer-optics computation
directly usable by AI agents and independently auditable after execution."**

---

## 0. M0：治理前置与全库 hash convergence（1.1 硬前提，1–1.5 周）

| 缺口 | 证据 | 动作 |
|---|---|---|
| 工作区不是 git 仓库 | 无 `.git` | git 化并与已发布 v1.0.0 历史对齐；近期修复单独成 commit |
| 版本谱系混乱 | pyproject 标 1.0.0；`docs/ARCHITECTURE.md` 开头仍写 "Version 0.6"；报告线写到 v0.5.1 | 以 PyPI 1.0.0 为唯一基线，v0.x 报告归档进 CHANGELOG；修正过时版本句；下一版本 **1.1.0** |
| **库内并存两种哈希约定** | `acceptance.py:47` `_stable_hash` 用 `json.dumps(..., sort_keys=True, separators=..., default=str)` **无 `ensure_ascii=False`**；而 `run_artifacts.py:135`、`experiment_store.py:58` 已是 UTF-8 约定 | 见 §0.1，M0 的核心工程项 |
| `hashing.py` 是死代码 | 全库无 import | 接线并成为唯一哈希入口（见 §0.1） |
| 新改动无变更记录 | CHANGELOG/WORKLOG 与 v1.0.0 逐字节相同 | 为符号修复、材料候选排序、`SpectralMetricManifest` 补条目 |

### 0.1 全库 hash convergence（v3 升级，评审认定 v2 最重要遗漏）

**目标态**：业务模块不得自行实现 stable hash。全库唯一允许的哈希入口：

```python
hashing.canonical_json_bytes(value)   # canonical JSON → bytes
hashing.stable_sha256(value)          # canonical JSON SHA256
hashing.file_sha256(path)             # 文件 SHA256
```

**必须统一的既有实现清单（已核实）**：

| 位置 | 现状 | 承担的身份 |
|---|---|---|
| `acceptance.py:47` `_stable_hash` | **无 `ensure_ascii=False`** | `certificate_id`（:67/:362/:477/:483）、`certificate.task_sha256`（:346）、`material_provenance_sha256`（:467） |
| `run_artifacts.py` | 已是 UTF-8 约定（`canonical_json_bytes`/`file_sha256`） | 工件索引哈希 |
| `task_io` / `experiment_store` | 已是 UTF-8 约定 | 任务身份、缓存身份 |
| cache identity / certificate identity | 分散 | 随上表统一 |

**配套 CI architecture test**：AST 检查业务模块不得直接调用
`hashlib.sha256(json.dumps(...))`（白名单仅 `hashing.py`），防止约定再次分叉。

**语义影响与归因**：证书/任务身份对含非 ASCII 字符的 payload 会改变（纯 ASCII
payload 不变）→ 与 §0.2 的 scheme 字段联动，一次性失效、可机读归因。

### 0.2 全局 identity scheme（v3.1 评审修正，采纳）

scheme **不只覆盖任务身份**——M0 的 hash convergence 改变的是全部 canonical-JSON
派生身份（`task_sha256`、`certificate_id`、`material_provenance_sha256`、缓存身份、
工件索引）。若只持久化 `task_identity_scheme`，未来看到一个旧 `certificate_id`
仍需按版本号猜其规范化方式。因此提升为全局字段，随 run envelope 与缓存身份持久化：

```text
identity_scheme: "veritmm-canonical-json-v1"
```

语义：该 artifact 集内**所有** canonical-JSON 派生身份均使用此 scheme。
未来 canonicalization 演进时，旧/新身份的对应关系可机读解释。接线副作用不变：
含 Unicode 任务哈希变化 → 一次性缓存全量失效，版本化 + CHANGELOG 记录。

**验收**：`version-identity` CI 通过；architecture test 上线并拦截自定义哈希；
全量 pytest/ruff；`benchmark --offline` 85/85、零假接受不回退；Unicode 任务
回归用例进 `test_run_identity`。

---

## 1. VeriTMM 1.1 — Trust + Agent-native（4–5 周）

### 1.1 `verify-run`：四态信任语义 + 证据重放（1.1 核心新功能）

**信任模型（诚实边界，v3 明确）**：1.1 的自包含目录只能证明
"内部自洽 + 相对 `RUN_RESULT` 索引无局部篡改"。若攻击者整体重写目录并重新生成
全部哈希，1.1 **无外部 trust anchor，不可检测**——该能力由 1.2 的 DSSE 签名补上
（签名回答"谁签发了 artifact"）。方案按此边界设计，不过度承诺。

**四态输出（v3 评审设计，采纳）**：

```text
integrity_status:       valid | invalid
                        工件哈希 vs RUN_RESULT 索引的内部自洽
certification_status:   certified | uncertified
                        skip_certificate 产生的 run 恒为 uncertified，
                        不得呈现为已认证结果
replay_status:          passed | failed | unavailable
                        对已持久化 VerificationEvidence 重新执行 evaluate_evidence()
authenticity_status:    unsigned | valid | invalid
                        1.1 恒为 unsigned；1.2 DSSE 后变 valid/invalid
```

**证据重放 vs 求解器重算（v3 新增的架构抽层）**：

现状 `certify_simulation()`（`acceptance.py:334`）把求解执行、convergence 重跑、
独立求解器重跑、acceptance、证书生成绑在一个函数里，`verifier/` 包仅含
`challenge.py`。1.1 抽出证据层：

```text
solve() → VerificationEvidence → evaluate_evidence() → Certificate
```

- `verify-run RUN_DIR` 默认路径：验工件哈希 → 读取已持久化 evidence →
  重新 `evaluate_evidence()`。**不重跑谱线**，秒级完成。
- `verify-run RUN_DIR --recompute`（1.1 后半或 1.2）：完整重跑
  solver / cross-solver / convergence，检查 `numerically_equivalent`。
- 架构语：**solver produces evidence; verifier evaluates evidence; signer attests
  the verified artifact.**

**legacy v1.0 工件核验（v3 新增）**：

```text
identity_scheme 存在             → 按该 scheme 验证
缺失 + artifact schema/version 可识别（v1.0.0 RUN_RESULT v1）
                                 → legacy 路径（见下）
无法识别                          → typed identity_scheme_unknown，不是"坏工件"
```

**LegacyEvidenceAdapter（v3.1 评审修正，采纳）**：`VerificationEvidence` 是 1.1
新增的持久化结构，v1.0 run 没有该 artifact。legacy 路径明确为：

```text
v1.0 legacy artifacts
  → LegacyEvidenceAdapter（冻结、只读、可测试的数据映射层）
  → VerificationEvidence 兼容的内存对象
  → evaluate_evidence()
```

约束：adapter **只映射 v1.0 工件中已存在的证据**；1.1 新增而 v1.0 缺失的字段一律
标记 `unavailable`，**不推断、不补造**。由此全库只维护一套 `evaluate_evidence()`
判定逻辑，legacy 层是冻结的数据适配器——与风险登记"只读、冻结实现、不新增特性"
一致。verify-run 发布当天即可核验 VeriTMM 过去产生的结果。

**验收**：AgentBench 全部已执行案例产物 verify 全绿（含 v1.0 legacy 路径样例）；
对抗测试集（改一个谱线值 / 改一个证书字段 / 删一个文件 / 替换工件字节）必须失败
并定位；`skip_certificate` 资格标记、evidence 缺失时 `replay_status: unavailable`
的诚实降级、`identity_scheme_unknown` 各有专属测试。

### 1.2 跨平台复现语义（两级定义）

| 等级 | 覆盖内容 | 保证 |
|---|---|---|
| `identity_reproducible` | 归一化任务、schema、canonical 元数据、全部身份哈希 | 跨平台逐字节一致（由 canonical JSON 方案保证） |
| `numerically_equivalent` | R/T/A、场、优化结果、verifier 判定输入 | 跨平台在声明容差内等价 |

- **范围限定（v3 补句）**：identity 保证 scoped to the same `task_identity_scheme`
  与协议语义；不跨 VeriTMM 大版本承诺 byte-identical。
- `canonical_json_dumps()` 只保证"相同 Python value → 相同序列化"，不保证数值求解器
  逐位一致；若未来声明 solver 输出 bit-identical，前提是完整环境锁定
  （Python/NumPy/BLAS 版本 + CPU 体系结构），单独立项，本版不承诺。
- `RUN_RESULT` 溯源块增加环境指纹与 `reproducibility_level`；CI 增
  `windows-latest`、`macos-latest` core 子集 job（规范化任务哈希跨平台一致 +
  固定任务集谱线在 `cross_solver_tolerance` 内一致）。

### 1.3 MCP stdio：agent-safe managed API projection

**设计定位**：

```text
            CLI
          /     \
  operator/debug   常规科学用户
          \         /
       Managed Execution
             ↑
     MCP agent-safe API（显式 allowlist）
```

**依据（已核实）**：`run` 的 `--skip-certificate` 会关闭
`require_spectral_convergence` 与 `require_independent_solver`（`execution.py:96-97`）；
`--physics-python` 可指定任意解释器；收敛容差可调。这些属于 operator/debug 面，
**一律不进 MCP**。

**MCP `run` 工具 allowlist（白名单之外一律 typed 拒绝）**：

| 允许 | 说明 |
|---|---|
| `task` | **inline JSON 或 `veritmm://task/...` resource**；若支持文件引用，必须限定 server-owned task root，resolve 后 containment 检查（拒绝 `..`、绝对路径越界、symlink 逃逸） |
| `detail` | compact（默认）/ standard / full |
| `tag` / `hypothesis` / `change_reason` / `experiment_id` / `parent_run_id` | 实验血缘元数据（provenance-only，本就不进证书） |
| `cache` | 缓存开关 |
| `resume` | 仅 sweep 合法，否则 typed error |

**MCP 不暴露**：`output_dir`（server-owned artifact root）、`store_dir`、
`skip_certificate`、`physics_python`、`convergence_*`、`device`、
`child_timeout_seconds`、`portfolio_max_candidates`、`user_metadata_json`（v1 不开放）。

**其余工具面**：`describe / schema / examples / preflight / history / inspect /
lineage / compare` 按 allowlist 收敛后暴露；大产物走 MCP Resources
（`veritmm://run/{run_id}/{ARTIFACT}`），不内联谱线。

**实现**：`[mcp] = ["mcp>=2,<3"]`（官方 SDK 当前稳定线 v2，已核实）；适配层收敛于
`tmm_engine/mcp_server.py` 单文件；stdio 先行，Streamable HTTP 移至 1.2；核心包
零 MCP 依赖。

**验收**：黄金一致性测试（同一安全任务经 CLI 默认参数与 MCP 产生相同 normalized
task 哈希、相同证书身份、相同科学结果）；allowlist 外逐参数拒绝测试；task 引用
containment 测试（`../`、绝对路径、symlink 逃逸用例）；`agentbench-interactive`
不回退；`agent_harness` 预留 `exposure: "mcp"` 钩子。

### 1.4 Agent Skill 成品化 + 可发现性

- `skills/veritmm-tmm/SKILL.md`（frontmatter 符合 agentskills.io 规范）+
  `references/` + `scripts/`；渐进式披露。
- 发现路径：宿主扫描约定 skill 目录，wheel 内 site-packages 不会被自动发现 →
  随包提供 `veritmm skill-path`（打印打包内技能目录）与
  `veritmm install-skill TARGET`（安装到用户级 skill 目录，冲突需显式覆盖）。

**验收**：frontmatter 通过规范校验；`install-skill` 后宿主可发现并完成盲任务
preflight（盲测归外部效度线）。

### 1.5 Solver handoff（typed，1–2 天）

unsupported-physics 的 typed failure 增加可选块：`suggested_solver_family` +
`handoff_hints`（光栅→周期/级次/RCWA 参数形态；各向异性→张量形式与 4×4 提示；
有限光束→束腰参数）。`informational_only: true`，不执行、不背书、不构成证书。

### 1.6 Analytic validation oracle suite（v3 定位明确，3–5 天）

- **定名与定位**：1.1 的解析闭式套件是 **CI validation oracle**——只用于
  pytest/CI/solver validation，**不进入运行时证书路径**。
- 内容：单界面 Fresnel、单层膜 R/T 闭式、Fabry–Pérot 谐振条件、四分之一波堆
  Bragg 边，作为 verifier 的精确锚点组，进 `test_high_precision_referee` 锚点层；
  mpmath 裁判保持第二意见。
- 运行时证书调用 analytic oracle（若未来需要）须另行定义契约：触发条件、覆盖的
  task 类、对 `accepted` 的影响、写入证书的字段。**明确不在 1.1。**

### 1.7 随 1.1 的社区轻量项

- 文档站（MkDocs Material 双语 + mkdocstrings，GitHub Pages）——3–4 天；
- Zenodo DOI（CITATION.cff 联动）——0.5 天；
- `Development Status :: 4 - Beta`（Stable 等 MCP 实际采用、跨平台运行时间、
  一轮兼容升级后再升）；
- Notebook cookbook 主体（必须含 verify-run 演示）——3–5 天。

---

## 2. VeriTMM 1.2 — Scale + Provenance（3–4 周）

### 2.1 并行执行（CPU 先行）

- `--workers N` 默认 **1**；sweep 子任务、tolerance Monte Carlo、verified batch
  生效；`ProcessPoolExecutor` 进程级隔离。
- **CPU 限定第一版**：Torch/CUDA 并发另立语义。
- **确定性验收**：比较科学内容指纹（normalized child task、科学结果、证书内容
  身份、确定性种子、child 逻辑索引），排除 run_id、时间戳、路径、执行顺序——与
  AgentBench 现行标准一致。科学负载要求逐字节一致，个别组件仅达
  `numerically_equivalent` 时必须显式报告。
- 种子派生 `child_seed = derive(parent_seed, child_index)`；worker 内
  `OMP/MKL_NUM_THREADS=1`；Windows spawn 约束（可 pickle、传路径不传对象）。

### 2.2 成本估计（不做秒数承诺）

preflight 增加信息性字段：

```json
{
  "estimated_solver_evaluations": 1200,
  "estimated_work_units": 480000,
  "complexity": "medium",
  "estimate_confidence": "medium"
}
```

成本受 refinement、optimizer iterations、multistart、MC 样本数支配；
`confidence` 随标定数据覆盖率标注。

### 2.3 吞吐回归门

>20% 回退**告警**；连续 N 次（默认 3）才硬失败；或改用固定 self-hosted runner
后直接硬门。

### 2.4 DSSE 签名（后移；定位诚实化）

- in-toto Attestation（Predicate → Statement → Envelope/DSSE，Ed25519）：
  subject = run 工件（sha256），predicate
  `https://veritmm.org/attestations/physics-acceptance/v1`；侧车
  `CERTIFICATE_ATTESTATION.dsse`，证书本体字节不变；`[sign]` extra。
- 签名回答"谁签发"，不回答"换台机器能否等价复现"（后者由 §1.2 承担）；
  无 trust root 的现实下定位为组织内场景先行。`authenticity_status` 自此从
  `unsigned` 变为 `valid/invalid`（§1.1 四态闭合）。
- `verify-run --public-key` 验签；组织级 PKI / Sigstore keyless 为远期选项。

### 2.5 Streamable HTTP 传输（安全最小集）

`mcp>=2` SDK 的 Streamable HTTP：**Origin 校验（防 DNS rebinding）+ 默认绑定
127.0.0.1 + bearer/OAuth 鉴权 + server-owned artifact root**。未配置鉴权时拒绝
远程绑定。task 引用的 containment 规则自 stdio 起生效，升级 HTTP 无需返工。

### 2.6 CLI 旗标卫生（评估项）

`--skip-certificate` 等在 CLI 层的暴露方式评估：移入 debug 子命令或要求
env-var 显式确认；配合 verify-run 的 `certification_status: uncertified` 标记
闭环。

### 2.7 静态结果查看器（可选）

`veritmm export-viewer RUN_DIR` 输出单文件自包含 HTML，无服务进程。

---

## 3. Experimental track（独立，不进发布承诺）

### 3.1 Berreman 4×4 各向异性

- 模块 `tmm_engine/anisotropic/`；独立 schema 命名空间（`anisotropic-simulation`）；
  manifest 显式 `experimental: true`；独立证书类型。普通 simulation schema +
  anisotropy → **继续 reject**。
- 自实现稳定 4×4（特征分解 + Berreman 指数，scipy）；第三方库（PyLlama GPL 系等）
  仅作 `[crosscheck]` 测试依赖，不进运行时与分发。
- 关键验收：等向极限测试与现有 smatrix/Byrnes 容差内一致；被动性套件
  （参数化于全部声明求解器）自动延伸覆盖。
- 2–4 周；1.2 之后的 experimental track。

### 3.2 材料不确定度框架

来源分级，禁止自动产生统计含义：

```text
material_uncertainty:
  source: reported | user_assumed | inferred_heuristic | unknown
```

- `reported`：数据集真实报告的测量不确定度 → 可参与正式传播；
- `user_assumed`：用户显式声明的先验 → 标记为 prior；
- `inferred_heuristic`：引擎从元数据推断 → **只允许** sensitivity/scenario 分析，
  永不进入正式 UncertaintyBudget 数值；
- `unknown`：**不给数字**。

红线不变：只进证据/预算层，永不自动放宽验收门或外推光学常数。

### 3.3 继续 reserved

变量层数/拓扑优化、非线性、热输运、扩散散射维持 reserved。

---

## 4. 里程碑与依赖

```text
M0 治理 + 全库 hash convergence（1–1.5 周）
 └─> 1.1 Trust + Agent-native（4–5 周）
       evidence 抽层 → verify-run(四态+legacy) → 复现语义/CI 矩阵
       → MCP stdio(allowlist) → Skill+install → handoff → analytic oracle
       → 文档/DOI/Beta
 └─> 1.2 Scale + Provenance（3–4 周，1.1 后）
       workers(CPU) → cost estimate → 吞吐门 → DSSE → Streamable HTTP
       → 旗标卫生 → viewer
Experimental（独立轨）
       Berreman 4×4 → 材料不确定度框架
```

**统一退出条件**：全量 pytest/ruff；`benchmark --offline` 85/85、零假接受；
`agentbench-interactive` 零假接受 + 安全拒绝率不降；新增能力专属回归门
（M0：architecture test 拦截自定义哈希；1.1：verify 对抗集 + legacy 路径 +
CLI/MCP 黄金一致性 + allowlist/task-containment 拒绝集 + 解析锚点；
1.2：并行科学指纹等价、吞吐告警/硬门、签名往返）。

---

## 5. 全局红线

1. AI/优化器/研究元数据永不签发或升级物理证书；证书语义只属于 verifier。
2. 不为换取运行成功而放宽任何 gate、启用外推、替换材料数据集或删除请求的输出。
3. MCP 是 agent-safe managed API projection，不是 CLI parity：allowlist 之外一律
   拒绝；`skip_certificate`、`physics_python`、可调收敛容差永不进入 agent 面；
   task 引用限定 inline / resource / server-owned task root（containment 检查）。
4. 不确定度声明必须带来源类型；heuristic 永不冒充测量不确定度。
5. 复现声明保守：identity 层逐字节（scoped to same identity scheme/协议语义）、
   numerical 层带容差，不做无环境锁定的 bit-identical 求解器承诺。
6. 信任声明保守：1.1 的 verify-run 只主张内部自洽与证据重放；防整体重写由 1.2
   DSSE 承担，方案文本不得超前能力。
7. 实验轨道字段显式标记实验性/信息性；analytic oracle 不进运行时证书路径。
8. 每次发布：`benchmark --offline` ≥80 案例、不支持物理假接受恰为零。

---

## 6. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| CLI operator/debug 旗标（`--skip-certificate` 等）被脚本误用 | 削弱验证的产物流出 | verify-run `certification_status: uncertified` 标记；1.2 旗标卫生 |
| 双哈希约定并存期间产生混合身份 | 身份不可解释 | M0 一次性统一 + scheme 字段归因；architecture test 防再分叉 |
| evidence 层抽离改动 `certify_simulation()` | 触碰验收核心路径 | 抽层等价测试：evidence 路径与原路径对同一任务产生逐字节相同证书 |
| legacy verifier 路径长期维护成本 | 双实现漂移 | LegacyEvidenceAdapter 只做数据映射，判定逻辑仅 `evaluate_evidence()` 一套；冻结、只读、不新增特性 |
| MCP 工具面膨胀 | 侵蚀 verifier-first 卖点 | allowlist + 拒绝测试 + 黄金一致性测试 |
| hashing 接线引发缓存全量失效 | 用户困惑 | 一次性、版本化、scheme 字段使失效可归因 |
| 并行破坏确定性 | 溯源主张受损 | 默认 workers=1；科学指纹等价验收；种子按索引派生；CPU 先行 |
| hosted runner 抖动误报吞吐回退 | CI 门失信 | 告警 + 连续 N 次才硬失败，或 self-hosted runner |
| DSSE 缺 trust root | 签名实用价值有限 | 定位诚实化（组织内场景先行）；复现语义承担"机器无关"主张 |
| 4×4 范围蔓延 | 拖垮主线 | experimental 命名空间 + 独立证书 + 不进发布承诺 |
| GPL 第三方 4×4 库混入分发 | 许可污染 | 仅 `[crosscheck]` 测试依赖，运行时自实现 |
| 跨平台 CI 揭示真实数值差异 | 需修容差声明 | VALIDATION.md 预先定义 numerically_equivalent 语义，失败按诊断处理 |

---

## 7. 参考资料

- MCP 官方 Python SDK（v2 当前稳定线）：<https://py.sdk.modelcontextprotocol.io/>；规范仓库：<https://github.com/modelcontextprotocol/python-sdk>；传输规范：<https://modelcontextprotocol.io/specification/2025-06-18/basic/transports>
- Agent Skills 开放规范：<https://agentskills.io/specification>；客户端接入：<https://agentskills.io/client-implementation/adding-skills-support>
- in-toto attestation 规范（Predicate → Statement → Envelope/DSSE）：<https://github.com/in-toto/attestation>；in-toto 与 SLSA：<https://slsa.dev/blog/2023/05/in-toto-and-slsa>
- 4×4/Berreman 参考：[Berreman4x4](https://github.com/Berreman4x4/Berreman4x4)、[PyLlama（CPC）](https://www.sciencedirect.com/science/article/abs/pii/S0010465521003684)、[pyGTM](https://github.com/pyMatJ/pyGTM)、[TFSolver（Opt. Express）](https://opg.optica.org/oe/abstract.cfm?uri=oe-33-25-52061)

---

*v3 冻结。实施顺序：M0（git 化 → 全库 hash convergence + architecture test →
版本/CHANGELOG 治理）→ 1.1（evidence 抽层起步）。*

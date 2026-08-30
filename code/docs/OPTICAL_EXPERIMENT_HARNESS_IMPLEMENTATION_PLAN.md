# OptoMind TMM 光学实验 Harness：权威实施说明

状态：开发集与工程链路验收通过，等待用户选择一道保留题进行最终盲测。  
范围：只使用传输矩阵法（TMM），不接入 RCWA、FDTD 或 FEM。  
模型约束：所有语言模型节点只允许 `qwen3.7-flash`，禁止模型回退；密钥可以轮换。

## 1. 系统目标

本系统不是一次性的薄膜优化脚本，而是可复用的光学实验运行环境：

```text
自然语言问题
  → QwenTMMTaskCompiler（受限任务编译）
  → OpticalDesignTask（不可变标准协议）
  → 能力与材料检查
  → 正向 TMM + 优化器组合
  → 确定性物理验收
  → 稳健性与材料不确定性分析
  → 候选组合排序
  → 产物追溯、成本记录和隔离复算
```

核心原则：

1. Qwen 可以填写任务协议或从动作白名单选择策略，不能生成光谱、修改验收结果或签发物理证书。
2. 优化器只能提出候选，不能验收自己。
3. 性能目标只产生连续评分和排名，绝不成为硬性通过线。
4. 物理接纳只检查能力边界、材料波段、无源性、能量守恒、数值收敛和独立 TMM 一致性。
5. 多样性只是一项可选加分。如果一个厚度方案同时最好、最稳健和最易制造，可以只交付该方案。
6. 达不到理想性能时交付真实的最佳结果和差距，不进入没有出口的无限优化。
7. 任何失败都有结构化原因；中断恢复不能突破原任务总预算。

## 2. 能力边界

支持：

- 平面分层结构；
- 各向同性材料；
- 频域平面波；
- 相干和混合相干计算；
- 波长、角度、s/p/非偏振扫描；
- 反射率、透射率、吸收率；
- 复振幅、相位、群时延和群时延色散；
- 层吸收与由吸收率得到的系统发射率；
- 椭偏量；
- 固定材料拓扑下的连续厚度优化、量化和稳健性分析。

不支持：

- 横向图案和衍射级次；
- 各向异性、非线性和时域问题；
- 有限尺寸边缘和近场；
- RCWA、FDTD、FEM 或其他全波求解器。

超出边界时必须返回 `needs_higher_fidelity`（需要更高保真求解器），不能错误运行 TMM。

## 3. 标准任务协议

权威实现：`optomind_optics/harness/design_task.py`。

`OpticalDesignTask` 至少包含：

- 原始用户问题和英文规范化问题；
- 一个或多个 `TMMExperimentSpec`；
- 仿真或优化任务；
- 只用于排名的 `ObjectivePreference`；
- 确定性物理验证策略；
- 候选组合策略；
- 厚度、角度和材料数据集不确定性；
- 时间、正向计算、优化器和 Qwen 预算。

协议为不可变 Pydantic 模型，额外字段和非法枚举关闭失败。启用“禁止材料外推”后，任何实验都不能绕过该约束。

## 4. 自然语言编译

权威实现：

- `optomind_optics/harness/task_compiler.py`
- `prompts/optical_harness/TMM Task Compiler.txt`

编译器只允许使用 `qwen3.7-flash`，最多尝试两次。输出分为：

- `compiled`：形成合法标准任务；
- `needs_clarification`：必要输入确实不足；
- `needs_higher_fidelity`：问题超出 TMM；
- `invalid`：两次均未通过确定性协议验证。

模型最多生成 3 个实验。顶层物理验证、预算、多样性和模型约束由程序固定，模型不能覆盖。

真实 DEV01 验收：一次 Qwen 调用完成编译，随后 TMM Harness 成功运行，得到 3 个物理有效候选。

## 5. 确定性实验内核

主要模块：

| 模块 | 作用 |
|---|---|
| `orchestrator.py` | 确定性主编排和故障恢复 |
| `solver_registry.py` | TMM 求解器能力注册 |
| `optimizer_registry.py` | 梯度与差分进化优化器组合 |
| `material_service.py` | 材料解析和材料来源清单 |
| `material_scenarios.py` | 同等级材料数据集的不确定性场景 |
| `evaluator.py` | 光谱特征和科学量分析 |
| `objectives.py` | 连续软评分和稳健性分析 |
| `portfolio.py` | 候选组合、Pareto 和可选差异方案 |
| `failure_diagnoser.py` | 结构化失败与允许恢复动作 |
| `budget.py` | 预留—提交式预算账本 |
| `stop_controller.py` | 前沿稳定、策略耗尽和预算停止 |
| `state_machine.py` | 不可逆状态与哈希历史 |
| `provenance.py` | 追加式产物来源链 |
| `replay.py` | 隔离环境科学产物复算 |
| `runtime_fingerprint.py` | 源代码树和依赖版本指纹 |

## 6. 优化与候选交付

当前优化器组合：

1. 基准设计；
2. 梯度厚度优化；
3. 差分进化全局厚度优化；
4. 量化厚度候选；
5. 厚度、角度和材料数据集扰动评估。

候选角色：

- 目标软评分最高；
- 最稳健；
- 最易制造；
- Pareto 前沿候选；
- 可选的结构特色候选。

角色可以指向同一个候选。只有性能仍有价值且结构差异超过数值噪声时，才增加特色候选。系统会记录未交付但已评估的合法候选数量，不因输出上限丢失审计信息。

## 7. 验收证据

### 7.1 开发题

冻结的五道开发题：

- DEV01：单层减反膜逆向设计；
- DEV02：多角度、多偏振 DBR；
- DEV03：缺陷腔、相位、群时延和色散；
- DEV04：有损选择性吸收器多目标优化；
- DEV05：相干薄膜与非相干厚基底混合计算。

五题均已正式运行并隔离复算，机器报告位于：

`outputs/tmm_harness_dev_acceptance_v4/DEV_ACCEPTANCE.json`

### 7.2 自动测试

```powershell
py -3.11 -m pytest -q <全部 test_tmm*.py 与 test_optical*.py>
```

当前最近一次结果：162 项通过。测试覆盖协议、TMM 交叉验证、随机压力测试、材料范围、材料不确定性、优化器预算、故障恢复、停止条件、Qwen 模型锁、代码与依赖指纹、产物防篡改和隔离复算。

### 7.3 运行产物

每次正式运行至少形成：

- `TASK.json`：不可变任务；
- `HARNESS_CONFIG.json`：运行配置；
- `RUNTIME_LOCK.json`：求解器、优化器、材料库、代码和依赖指纹；
- `EXPERIMENT_GRAPH.sqlite/.json`：实验图；
- `EVENTS.jsonl`：阶段事件；
- `BUDGET.json` 与 `COST.json`：预算和成本；
- `MATERIAL_MANIFESTS.json`：材料来源；
- `PHYSICS_ACCEPTANCE_CERTIFICATE.json`：物理验收证书；
- `DESIGN_PORTFOLIO.json`：候选组合；
- `ARTIFACT_MANIFEST.json`：哈希来源链；
- `FINAL_RESULT.json`：最终机器结果；
- `REPLAY_MANIFEST.json`：隔离复算比对。

## 8. 保留题与最终验收

保留题编号为 `HOLDOUT06` 至 `HOLDOUT10`。题目不得在用户选择前运行或用于调参。

访问机制：

1. 用户随机指定一个编号；
2. 命令行必须提供固定授权文本；
3. 环境变量必须设置 `OPTOMIND_ALLOW_TMM_HOLDOUT=1`；
4. 打开真实保留文件前，程序先写入 `outputs/tmm_harness_holdout_audit/HOLDOUT_ACCESS.jsonl`；
5. 中断恢复读取已保存的选题和任务，不重复打开保留文件；
6. 自然语言由通用编译器处理，禁止临时编写题目专用夹具；
7. 运行后进行隔离复算，并输出 `HOLDOUT_ACCEPTANCE.json`。

运行入口：`scripts/run_tmm_harness_holdout_acceptance.py`。

当前唯一未完成项：等待用户从五个编号中随机选择一道并执行最终盲测。盲测通过前不得宣布整个目标完成。

## 9. 历史访问说明

访问审计安装前，旧结构测试曾机械解析真实保留集 JSON，以检查数量和字段，但没有执行题目，也没有将问题正文或结果用于调参。该测试现已改为临时生成的假保留集；自审计启用以来真实保留读取次数为 0。报告必须区分：

- 文件读取；
- 题目执行；
- 结果是否用于调参。

不得再用一个硬编码的 `holdout_accessed=false` 代替事实审计。

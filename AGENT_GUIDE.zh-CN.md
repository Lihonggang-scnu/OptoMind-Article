# OptoMind-Article Agent 快速评估指南

> 本文件是仓库中唯一面向 AI Agent 的运行说明，服务于评委或维护者的快速摸底。普通使用者请先阅读 [README.md](README.md)。

## 1. 评估边界

OptoMind-Article 的主对象是 `code/` 下的 TMM research harness。`veritmm/` 是它调用的物理执行组件，单独跑 VeriTMM 只能证明执行器可用，不能替代整条 harness 评估。

评估时遵守以下边界：

- 不执行 Git 操作，不修改六组正式原始记录；新的测试写入新的输出目录。
- 不读取、打印、复制或上传任何密钥内容；只检查密钥文件是否存在以及字节长度。
- 不把 `--force-mock` 的结果当作真实科研结果。该模式用于检查模型不可用时是否按协议停止。
- 不把 `FINAL_ANSWER.md` 单独当作证据；数值结论必须回到迭代产物和物理证书。
- 不因单个候选失败就断言引擎故障，先检查 `RESEARCH_EVENTS.jsonl`、编译状态、TMM 运行状态和失败分类。

## 2. 目录与入口

从仓库根目录执行：

```powershell
Set-Location -LiteralPath .\code
```

主要入口和资产如下：

| 路径 | 用途 |
|---|---|
| `scripts/run_tmm_research_harness.py` | 完整研究链路入口 |
| `prompts/optical_harness/` | 运行时提示模板 |
| `tests/` | 单元、协议、物理链路和回归测试 |
| `api_keys/qwen-api-key.txt` | Qwen 空密钥模板 |
| `api_keys/semantic-scholar-api-key.txt` | Semantic Scholar 空密钥模板 |
| `outputs/tmm_research_harness/` | 新运行的默认输出位置 |
| `..\veritmm\scripts\run_tmm_task.py` | 独立 VeriTMM 任务入口 |

主 harness 会优先使用仓库根目录同级的 `veritmm/`，因此不应把它改回原开发机的绝对路径。

## 3. 离线摸底

先运行不需要在线密钥的最小回归集：

```powershell
python -m pytest -q `
  tests/test_run_harness_smoke.py `
  tests/test_tmm_material_registry.py `
  tests/test_tmm_task_compiler.py
```

如需检查 CLI 是否可启动：

```powershell
python -u scripts/run_tmm_research_harness.py --help
```

如需检查模型不可用时的关闭行为：

```powershell
python -u scripts/run_tmm_research_harness.py `
  '测试一个双波段薄膜设计需求' `
  --force-mock `
  --no-online-method-research `
  --no-qwen-method-synthesis `
  --maximum-iterations 1 `
  --maximum-initial-routes 1 `
  --route-planning-maximum-routes 1 `
  --wall-time-seconds 60
```

预期是得到一个带 `analysis_failed` 或其他诚实终止状态的结果，而不是虚构候选。这个命令不验证在线 Qwen 规划和真实 TMM 结果。

## 4. 真实轻量运行

先在 `api_keys/` 填入使用者自己的密钥，不要把密钥写进命令行参数。然后设置超时并运行一条路线一轮迭代的真实测试：

```powershell
$env:QWEN_API_KEY_FILE = (Resolve-Path .\api_keys\qwen-api-key.txt).Path
$env:QWEN_HTTP_TIMEOUT_SEC = '45'
$env:QWEN_MAX_KEY_CANDIDATES = '1'
$env:QWEN_MAX_TRANSPORT_KEY_CANDIDATES = '1'

python -u scripts/run_tmm_research_harness.py `
  '我需要一个双功能多层膜：在近红外 800-1500nm 波段透射率尽可能高，同时在紫外 200-400nm 波段反射率尽可能高。衬底是熔融石英，总层数控制在 30 层以内。' `
  --output-dir .\outputs\tmm_research_harness\agent-smoke-<date> `
  --no-online-method-research `
  --no-qwen-method-synthesis `
  --maximum-iterations 1 `
  --maximum-initial-routes 1 `
  --route-planning-maximum-routes 1 `
  --max-rounds-per-route 1 `
  --minimum-rounds-before-llm-stop 1 `
  --no-control-route `
  --maximum-refinement-rounds 0 `
  --maximum-method-research-rounds 1 `
  --wall-time-seconds 900 `
  --task-compiler-tier turbo
```

评估结果时，至少确认以下阶段是否按顺序出现：

1. `problem_analyzed`；
2. `scoring_standard_fixed`；
3. `routes_planned_from_literature` 或明确的路线不可用状态；
4. `strategy_planned`；
5. 迭代目录中的 `COMPILED_TASK.json` 和 `TASK_COMPILATION.json`；
6. `tmm_run` 中的执行状态、候选验证和物理证书；
7. `SCORING_RANKING.json`、`TOURNAMENT_SUMMARY.json` 和 `RESEARCH_RESULT.json`。

真实链路的前置模型调用可能比 TMM 计算更慢。若在预算内只完成问题分析、评分和路线规划，应据 `RESEARCH_EVENTS.jsonl` 如实记录为“前置阶段完成、尚未进入候选执行”，不能将其误报为物理引擎失败。

## 5. 完整结果判定

一份可用于研究回放的完整运行通常应同时具备：

- 原始题面和问题分析；
- 运行开始前固定的评分标准；
- 路线来源及文献/对照标记；
- 每轮编译任务、观测和反馈；
- 至少一个候选对应的物理接受证书；
- 冻结评分排名和竞赛汇总；
- 运行事件、预算和终止审计。

检查候选时，优先查看 `PHYSICS_ACCEPTANCE_CERTIFICATE.json`、`RESULT_SUMMARY.json`、`FINAL_RESULT.json`（若该迭代产生）以及 `SCORING_RANKING.json`。只有通过物理验证且满足冻结评分字段映射的候选，才进入可比较排名。

对照实验只有在文献规划路线与记忆路线都产生可比的冻结标准结果时才可下跨来源结论。若某一臂没有可评分候选，结果文件中的缺失状态是正式结论，不应人工补齐。

## 6. 六组历史记录

六组记录是随仓库分发的原始证据资产，不是 Agent 的默认输入，也不是待自动修复的缓存：

```text
outputs/tmm_research_harness/e2e-methane-swir-window-20260828-default4-w10800
outputs/tmm_research_harness/e2e-uav-swir-window-20260829-default4-w10800
outputs/tmm_research_harness/e2e-space-qkd-cband-window-20260829-default4-w10800
outputs/tmm_research_harness/e2e-solarblind-uv-window-20260829-default4-w10800
outputs/tmm_research_harness/e2e-fifth-dualgas-mwir-20260829-default4-w10800
outputs/tmm_research_harness/e2e-sixth-combustion-co-20260829-default4-w10800
```

评估或展示时只读这些目录；新问题、新参数和新修复必须写入新 `run_id` 目录。

## 7. 常见状态解释

- `completed`：运行完成，仍需检查候选和物理证书，不能只看终态字符串。
- `completed_best_effort_no_verified_candidate`：流程完成到预算允许的阶段，但没有可验证候选。
- `analysis_failed`、`compilation_failed`、`unavailable`：相应阶段未通过协议，属于真实运行状态。
- `rejected_physics`：执行器完成了计算，但物理验收拒绝了该任务或候选；应读取证书中的失败分类和下一步动作。
- `--force-mock`：离线诊断开关，不产生可发表的实验结果。

## 8. 评估记录建议

每次摸底至少保存以下信息：运行题面、命令参数、`RESEARCH_EVENTS.jsonl`、`RESEARCH_RESULT.json`、评分标准、路线规划、迭代目录、物理证书、终止审计和最终回答。不要用手工摘要替换原始 JSON，也不要在公开记录中包含密钥、临时绝对路径或未验证的模型陈述。

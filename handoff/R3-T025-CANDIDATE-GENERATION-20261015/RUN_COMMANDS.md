# T025 实际运行记录

工作目录 `D:\OptoMind-Article-3\r3t025a`，Python沿用 `D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe`。

```powershell
$root = 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T025'
$python = 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe'
& $python -B -X utf8 "$root\prepare_inputs.py"
& $python -B -X utf8 "$root\launch_t025.py" preflight
& $python -B -X utf8 "$root\launch_t025.py" generate --execute
& $python -B -X utf8 "$root\finalize_accounting.py"
& $python -B -X utf8 "$root\diagnose_drafts.py"
```

以上分别退出0、0、2、0、0。生成只启动一次，生产入口为 `route_stage.main(['generate', ...], planner=QueryPlannerAgent(max_tokens=16384))`，负责原生两轮循环；本地仅注入T022已有计费适配器与工单/链限额门禁，没有新模型循环或科学答案补丁。

原任务：T022/runs/routes/task_sheet.json、design_constraints.json。原12条 evidence_items 原样保留在本轮 inputs/evidence_items.json；五份材料来自已验收T024/reexport/exports。现有material_evidence索引/选块接口证实CORE整块超预算，未加入最终payload。无重新检索/导出/解析。

原请求/响应在本轮 private/audit/0eedc8f17630f7458272/calls，逐调用保存 REQUEST.json、RESPONSE.json及raw/*.raw。预留0.744364和0.985804元是保守占额，已按原生usage结算为0.088944和0.0910616元；无未知费用。

复现本轮失败无需付费：读取 runs/routes/generation_outcome.json 两轮原解析对象及 QUOTE_DIAGNOSTICS.json，对照原证据可看到D3/D4精确片段校验失败。完整生成命令有持久STARTED/输出保护，禁止重复执行；新付费运行需另发工单。

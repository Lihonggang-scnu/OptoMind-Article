# T023 实际命令与解释器

工作目录：`D:\OptoMind-Article-3\r3t023a`。以下为 PowerShell；`offline_guard.py` 是任务目录中的外部零网络测试保护，未修改源码。Review 子进程使用固定解释器，由保护层继承相同网络限制。

```powershell
$taskRoot = 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T023'
$old = 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022'
$python = 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe'
$reviewPython = 'D:\OptoMind-Article-3\local-preparation\envs\review\Scripts\python.exe'
$article = 'D:\OptoMind-Article-3\r3t023a'
& $python -B -X utf8 "$taskRoot\cache_preflight.py" before
& $python -B -X utf8 "$taskRoot\offline_guard.py" "$article\focused_research\fulltext_stage.py" --reexport-result "$old\runs\fulltext\FULLTEXT_RESULT.json" --output-root "$taskRoot\reexport-final" --review-root D:\OptoMind-Article-3\r3t022r3 --review-python $reviewPython
& $python -B -X utf8 "$taskRoot\offline_guard.py" "$article\focused_research\route_stage.py" prompt --task-sheet "$old\runs\routes\task_sheet.json" --evidence-file "$old\runs\routes\evidence_items.json" --constraints-file "$old\runs\routes\design_constraints.json" --output "$taskRoot\prompt"
& $python -B -X utf8 "$taskRoot\offline_guard.py" "$article\focused_research\route_stage.py" validate --task-sheet "$old\runs\routes\task_sheet.json" --evidence-file "$old\runs\routes\evidence_items.json" --constraints-file "$old\runs\routes\design_constraints.json" --routes-file "$old\runs\routes\research_routes.json" --output "$taskRoot\routes-check"
& $python -B -X utf8 "$taskRoot\offline_guard.py" "$taskRoot\audit_cached_results.py"
& $reviewPython -B -X utf8 "$taskRoot\offline_guard.py" "$taskRoot\audit_native_snapshots.py"
& $python -B -X utf8 "$taskRoot\cache_preflight.py" after
```

实际出口：重导出 CLI 0（结果 partial，3/5），prompt CLI 0，validate CLI 0，三项本地断言脚本均 0。输出目录不得重用；复现应另起空目录。`cache_preflight.py before` 要求源提交仍为固定实现，因此提交结果后复现要从固定实现新建副本。

最初测试保护失败目录 `reexport` / `reexport-confirmation` 保留：前者 TypeError 来自把 subprocess.Popen 类替换成函数；后者 RuntimeError 来自过严地禁止本进程内部 socketpair 和 SDK 导入。最终保护保留 Popen 类语义，只许可本进程已绑定监听器的 socketpair，禁止 HTTP/DNS 外连与新增子命令。没有供应商调用或原始输入修改。

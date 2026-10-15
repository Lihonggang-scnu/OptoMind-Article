# T024 实际执行命令

工作目录 `D:\OptoMind-Article-3\r3t024a`；全部输出在全新 T024 任务目录，原 T022/T023 只读。

```powershell
$root = 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T024'
$python = 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe'
$reviewPython = 'D:\OptoMind-Article-3\local-preparation\envs\review\Scripts\python.exe'
& $python -B -X utf8 "$root\preserve.py" before
& $python -B -X utf8 "$root\offline_guard.py" D:\OptoMind-Article-3\r3t024a\focused_research\fulltext_stage.py --reexport-result D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022\runs\fulltext\FULLTEXT_RESULT.json --output-root "$root\reexport" --review-root D:\OptoMind-Article-3\r3t022r3 --review-python $reviewPython
& $reviewPython -B -X utf8 "$root\offline_guard.py" "$root\check_exports.py"
& $python -B -X utf8 "$root\preserve.py" after
```

四项退出均为 0。缓存导出仅执行一次，没有第二轮或参数调整。保护层沿用 T023 已修正版本，仅更新本轮路径与检查器；保留 Windows 内部 socketpair，禁止外部网络/HTTP及新增子命令。检查器只读取五份原快照并逐对象、逐哈希比较，不启动解析。

这些命令记录实际执行；勿对已存在输出目录再次运行。原始完整新导出、操作 stdout/stderr 与快照/账本留本地，交接只含必要摘要、哈希、调用保护记录和检查代码。

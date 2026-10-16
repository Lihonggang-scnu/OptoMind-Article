实际解释器：D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe，均加 -B -X utf8。

工作目录：D:\OptoMind-Article-3\r3t042a。

真实启动（仅一次，退出2）：
`python -B -X utf8 D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T042\run_t042.py case --case e2e-12-lwir-ge-window --execute`

指定6文件的完整参数见 preflight/tests-attempt02/full/argv.txt；未扩大完整回归。
费用合成检查：`python -B -X utf8 -m pytest -q D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T042\local_tools\test_t042_money.py --disable-socket`（实际cwd为任务local_tools，5通过）。
回调修复前后：`python -B -X utf8 -m pytest -q D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T042\local_tools\test_launcher_callback.py --disable-socket`（各一次，失败/通过日志分开保留）。

禁止直接重跑上面的真实启动：原CASE目录已非空，原1次请求必须计入任何新授权恢复。

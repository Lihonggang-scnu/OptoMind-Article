# 实际运行入口

工作根：`D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022`。

- Article：`D:\OptoMind-Article-3\r3t022a`，固定`5ef850832c221e7f6444195ac8b948117ec1c8aa`。
- Review：`D:\OptoMind-Article-3\r3t022r3`，固定`d2deb4dbe18b23837d949822ad731afbaba9414b`，稀疏检出运行源码，通过只读Git对象引用使用原F盘对象；未修改F盘。
- 主解释器：`D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe`。
- 独立来源/Review：`D:\OptoMind-Article-3\local-preparation\envs\review\Scripts\python.exe`。
- Docling：`F:\OptoMind-Review-2\outputs\e2e_optics_20261012_80cny\parser_repair\venv_docling\Scripts\python.exe`。

以下命令为已执行记录，不是要求重复调用。`launch_t022.py`保留STARTED保护和原账本限制，已有产物不得覆盖。

```powershell
$main = 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe'
$review = 'D:\OptoMind-Article-3\local-preparation\envs\review\Scripts\python.exe'
$task = 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022'
$source = 'D:\OptoMind-Article-3\r3t022a\focused_research'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
& $main -B -X utf8 "$task\launch_t022.py" preflight
& $main -B -X utf8 "$task\launch_t022.py" plan --execute
$env:SEMANTIC_SCHOLAR_API_KEYS_FILE = 'F:\OptoMind-Review-2\api_keys\semantic-scholar-api-key.txt'
& $main -B -X utf8 "$source\run.py" retrieve --plan-file "$task\runs\plan\query_plan.json" --output "$task\runs\s2" --per-query-limit 10 --retrieval-seconds 300
& $review -B -X utf8 "$task\discover_fulltext_sources.py"
& $main -B -X utf8 "$task\run_fulltext.py"
& $main -B -X utf8 "$task\launch_t022.py" routes --execute --evidence-file "$task\runs\material-view\selected_evidence_items.json" --constraints-file "$task\CHECKED_CONSTRAINTS.json"
& $main -B -X utf8 "$task\prepare_inputs.py"
& $main -B -X utf8 "$task\finalize_evidence.py"
```

材料导入实际调用`material_evidence.py --s2 ... --export <四个FULLTEXT_RESULT中的真实导出> --output-dir ...`；第一次保存完整材料归档，第二次使用`S2_SELECTED_VIEW.json`和`MATERIAL_SELECTION.json`进入新view目录。9个完整块和3条原始S2片段未截断。`ROUTE_PROMPT_PREFLIGHT.json`及实际HTTP REQUEST保存模型收到的文本。

全文来源独立发现先完成一次，再由操作者按真实元数据选5篇；`fulltext_stage.run_fulltext_stage(records, [], ...)`接收这些选中记录，不重复独立查询。调用固定Review、max_papers=5、deadline_seconds=180，基础三进程profile只改本地缓存路径，未启用公式参数。该源码调用详见`run_fulltext.py`，实际请求见`FULLTEXT_REQUEST.json`。

必要环境处理：首次Review浅取对象打包较慢，第二个包含历史docs的检出较慢，仅停止核实归属的本轮Git进程；两个未完成新目录保留。最终用第三个独立稀疏运行副本；未reset/clean/删除历史或改变全局Git配置。材料统计脚本一次将list误当dict，修正了纯摘要元数据，未重跑模型、检索或解析。原失败记录与运行产物保留本地。

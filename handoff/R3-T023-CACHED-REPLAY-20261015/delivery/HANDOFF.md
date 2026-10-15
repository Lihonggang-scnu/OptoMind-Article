# T023 本地定点复验（零费用；复用 T022 产物）

## 工单目的

确认 CORE 原生正文接缝与精简证据视图可用，并验证候选重复诊断和契约修复记录。T022验收仍为partial；本工单不将科学生成质量自动改成passed。

- 基准：`5ef850832c221e7f6444195ac8b948117ec1c8aa`
- Review：`d2deb4dbe18b23837d949822ad731afbaba9414b`
- T022证据：`eab06932b96fe73563044f0175a43865580be123/handoff/R3-T022-QUICK-PARSE-20261015`
- 实现SHA：以随包ready消息为准，必须固定checkout，不在旧T022目录覆盖修改。
- **本任务费用上限0元；模型HTTP调用0。** 不新检索，不下载PDF，不重新Docling，不公式增强，不仿真，不运行整套测试或启动新案例。
- 已知最新聚合支出17.5310854元；这是T022供应商usage估算口径，非账单核对。此处记录不授权新增付费，也不新建预算池。

## 执行前

1. 读取固定根START_HERE与本工单ready，检查回执去重；未处理先留下claimed。
2. 使用独立工作目录、原有Python环境。保留T022全部文件、旧项目、主分支和账本数据库。
3. 先检查本地T022的FULLTEXT_RESULT、5份快照、原selected_evidence_items、路线task_sheet/evidence_items/design_constraints及generation_outcome都存在。不存在就报告缺失，不重新付费或自动重取。

## A. 复用现有全文快照导出

使用新增`fulltext_stage.py --reexport-result`，指定T022原FULLTEXT_RESULT、固定Review目录、已有Review Python和**全新输出目录**。该入口只验证和重导出已有快照，不进入发现、获取、Docling或公式分支。

```text
<ArticlePython> focused_research/fulltext_stage.py --reexport-result <T022/FULLTEXT_RESULT.json> --output-root <T023/reexport> --review-root <Review固定副本> --review-python <原ReviewPython>
```

检查：
- 原来4份可用全文仍为可用，不把中间fitz替代最终Docling。
- CORE快照若满足原生选中来源绑定，可进入导出；仍保留input_attributed与全部使用限制，不称独立身份核实。
- 显式选中来源冲突、损坏或策略不允许应给出清楚原因，而非返回“没有文献”。
- 所有输入哈希不变；调用统计为0。

## B. 精简模型输入（不调用模型）

复用T022路线阶段的task_sheet、evidence_items、design_constraints：

```text
<ArticlePython> focused_research/route_stage.py prompt --task-sheet <原task_sheet.json> --evidence-file <原evidence_items.json> --constraints-file <原design_constraints.json> --output <T023/prompt>
```

对比旧ROUTE_PROMPT_PREFLIGHT中的evidence_items与新route_prompt：
- 12项evidence_id及顺序一致；每项text逐字相同，原文总计12,487字符。
- source_ref/snapshot_ref都能解析；来源身份与使用限制可读取；表格/公式等所选科学结构保留。
- JSON视图字符数减少；报告旧/新字数，不能将字符比例直接称token费用节省比例。
- 原始归档和selected材料文件不变。

CORE新增正文可以在另一个新选择文件中加入；保持全块选择和原48,000字符正文预算，超限就报告，不静默截取、不替换原选择。

## C. 复验已生成候选（不重生成）

```text
<ArticlePython> focused_research/route_stage.py validate --task-sheet <原task_sheet.json> --evidence-file <原evidence_items.json> --constraints-file <原design_constraints.json> --routes-file <原research_routes.json> --output <T023/routes-check>
```

预期：最终原候选合同仍通过；route_quality_report识别R1/R2为相同结构/干预模式的参数变体，而非数值完全相同；不删除它们、不宣布机制多样性或科学通过。

对generation_outcome首轮已解析对象离线调用validate，应明确指出基准与候选实际参数无变化；对两轮对象调用contract_change_audit，应记录实际对象变化路径。保留原始响应，不手工修写来过关。

## D. 报告后停止

只回传简短报告、尺寸/哈希统计、CORE来源限制、路线上述诊断及具体异常。无需上传原PDF、模型权重、私钥、账本数据库或含凭据进程日志。

验收拆分：
- 程序接缝与输入投影：按实际结果passed/partial/blocked。
- 科学解释、候选多样性生成效果：仍待新模型调用验证。
- Docling效率：本工单未重跑；21.8分钟问题仍为诊断待验证，不声称提速。
- 仿真与目标性能：未执行。

有同范围简单路径/环境问题可修并记录；核心逻辑问题回传云端，不增设自己的生成流程。完成回执后停止，后续付费重生成另发有明确限额的工单。

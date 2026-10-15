# T023 零费用缓存复验

**结论：partial；完整重导出受阻，已停止并回传。** Article 实际执行 `812a4bfad559a1522e973a3e68e7481293e2242a`，独立目录 `D:\OptoMind-Article-3\r3t023a`；Review 固定 `d2deb4dbe18b23837d949822ad731afbaba9414b`。无生产代码修改。

- **CORE 接缝通过**：原生 loader 实际加载旧快照，`core_text_jats` 可导出，材料与 loader 输出完全一致；仍为 `input_attributed`，使用限制与缺口完整保留，不称独立身份核实。
- **证据投影通过**：12 项 ID、顺序及 12,487 字符原文逐字不变；所有来源/快照引用可解析，所选科学结构、身份及使用限制保留。视图 171,222→51,315 字符，减少 70.03%；这是字符统计，不是 token/费用节省证明。
- **旧候选诊断通过**：最终合同仍接受；R1/R2 被识别为同结构、同干预模式的参数变体，未删除候选。首轮实际无参数变化被明确拒绝；对两轮原解析对象记录 28 处变化路径，和云端记录一致。未修改原响应。
- **完整导出受阻**：最终 3/5 成功（CORE 与两份 TEI），两份旧 Docling 导出报 `existing_export_source_binding_mismatch`。两份 export 的 `source_record` 比 FULLTEXT_RESULT 对应 record 多出 `_index`、`label`、`local_pdf`；其他字段相同。5 份原生快照均可加载，不能把这次拒绝解释为旧 PDF 不可用或缺文献。

复现：按 RUN_COMMANDS.md 的重导出入口，用原 T022 FULLTEXT_RESULT、固定 Review 与全新输出目录；详见 `DOC_REFERENCE_BINDING_DIAGNOSIS.json`。建议云端区分来源身份字段与批处理运行字段，保留快照/原文哈希、身份冲突和目录保护；本地未放宽检查或改写旧数据。

操作记录：最初两次测试保护分别与 Windows asyncio 的 Popen 继承、内部 socketpair/SDK 导入冲突；只修正外部测试保护后完成上述复验，原失败均保留。未把操作器问题算成生产缺陷。

**费用与边界**：新增模型 HTTP、检索、Docling、公式增强、仿真均为 0；未跑回归套件。两份原账本字节不变，累计供应商 usage 估算仍为 **17.5310854 元**，占额 0；不是供应商账单核对。T022 全部 261 个文件哈希/清单不变。T022 科学质量仍 partial，R2 偏振解释及候选生成多样性未由新模型验证；Docling 21.8 分钟问题未复测，目标性能未计算。

原始记录留在 `D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T023`；此前 T022 证据固定在 `eab06932b96fe73563044f0175a43865580be123/handoff/R3-T022-QUICK-PARSE-20261015`。本轮不追加工作。

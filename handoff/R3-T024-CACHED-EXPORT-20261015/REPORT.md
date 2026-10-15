# T024 五份缓存导出复验：passed

Article 实际执行 `3eb51424985402a3e9e79bdb872a002d9f5b6e45`，独立目录 `D:\OptoMind-Article-3\r3t024a`；Review 固定 `d2deb4dbe18b23837d949822ad731afbaba9414b`，沿用原有解释器。仅执行一次缓存重导出；没有本地生产代码修改。

| 原来源 ID | 缓存类型 | 结果 |
| --- | --- | --- |
| corpus:119291481 | Docling | passed |
| 10.4302/plp.v17i4.1361 | Docling | passed |
| core:4662953 | core_text_jats | passed |
| 10.3390/coatings15060695 | TEI | passed |
| 10.1364/ao.53.006983 | TEI | passed |

5/5 原生快照加载及导出通过，CLI 退出 0，结果 `complete`。两份 Docling 不再因 `_index`、`label`、`local_pdf` 批处理附加字段被拒绝。五份新导出 `source_record` 均严格等于原 T022 FULLTEXT_RESULT 对应记录，无新增批处理字段；完整 material（包括原文、快照 ID、身份、使用限制、缺口）逐对象等于原生 loader 输出，其规范 JSON SHA256 逐份一致。详见 `EXPORT_CHECK.json`。

CORE 保持 `input_attributed`；原身份、使用限制与缺口完整保留，未升级为独立核实。T022 的 261 个文件、T023 的 88 个文件及两份原账本的哈希/清单不变。

新增费用 **0 元**；累计供应商 usage 估算仍为 **17.5310854 元**，占额 0。未调用模型、检索、下载资料、解析 PDF、运行 Docling/公式增强或仿真；未跑回归套件，未重跑证据投影和候选诊断。

本轮确认旧缓存导出兼容修复实际生效。新 PDF 解析生成路径未执行；候选科学解释、生成多样性、Docling 速度和目标性能不在本轮验收范围，T022 科学质量仍为 partial。完成回传后停止。

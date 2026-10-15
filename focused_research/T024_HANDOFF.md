# T024：旧 Docling 缓存来源记录兼容复验

本轮仅修复 T023 缓存重导出中的批处理附加字段比较问题。T023 已通过的 CORE、证据投影和候选诊断无需重跑。

- 基准 SHA：812a4bfad559a1522e973a3e68e7481293e2242a。
- 实现 SHA：以 ready 消息和 bundle 分支为准。
- Review 固定 SHA：d2deb4dbe18b23837d949822ad731afbaba9414b。
- 失败证据固定 SHA：a0108cbe7d605ef0727969a4e88fd8b975268e56，handoff/R3-T023-CACHED-REPLAY-20261015。
- 本任务限额 0 元，模型 HTTP 0；不新检索、下载、解析 PDF、增强公式或仿真，不跑整套回归。
- 最新回传累计估算 17.5310854 元，占额 0；不因此授权新增费用。

## 执行

先读 START_HERE，按任务和消息 ID 去重，未领取先写 claimed。在独立目录检出实现 SHA，使用既有环境，保留 T022/T023 全部产物和原账本。

只重做缓存导出 A：使用原 T022 FULLTEXT_RESULT 和五份原生快照，指定全新 T024 输出目录。

```text
<ArticlePython> focused_research/fulltext_stage.py --reexport-result <原T022/FULLTEXT_RESULT.json> --output-root <新T024/reexport> --review-root <固定Review副本> --review-python <既有ReviewPython>
```

## 验收

1. 原有五份原生快照均能加载，导出 5/5；两份 Docling 不再因批处理附加字段拒绝。若仍失败，保留具体异常并停止，不改旧数据来通过检查。
2. 新导出 source_record 使用原 FULLTEXT_RESULT 对应来源记录，不携带额外批处理运行字段；快照 ID、材料原文及来源限制与原生 loader 一致。
3. CORE 保持 input_attributed，原身份、使用限制和缺口不升级为独立核实。
4. T022/T023 文件、账本不变；新增模型、检索、解析和仿真调用为零。

回传简短报告、固定提交、5 条状态和必要哈希摘要，勿上传原 PDF、权重、密钥或账本数据库。完成后写回执并停止。

本轮仅验证缓存导出接缝；候选科学解释、生成多样性、Docling速度和目标性能不在本轮验收范围内。

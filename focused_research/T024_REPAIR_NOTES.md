# T024 修复说明

T023 固定结果 a0108cbe7d605ef0727969a4e88fd8b975268e56 证实：两个 Docling 导出在 source_record 中附加了 _index、label、local_pdf；原始 FULLTEXT_RESULT 的来源记录不含这些字段。五份原生快照均可加载，失败发生在导出信封比较阶段。

根因：PDF 批处理临时记录被直接保存在导出 source_record 中；随后缓存复验按整个字典逐字比较。

修改：
- 新 Docling 导出改为保存原始 row.record，批处理仍使用临时字段。
- 历史兼容仅用于存在最终 Docling acquisition 的记录，允许导出一侧增加已知三个运行字段；验证索引、标签对应及路径字符串形状。
- 原记录已有字段必须严格一致，包括原本提供的 local_pdf；任意未知额外字段、身份和题录修改仍拒绝。
- Review 固定提交、snapshot ID、原生 loader 哈希及身份验证、来源使用限制、旧目录防覆盖均不变。

测试：针对全文接缝 42 项通过；focused_research 离线全套 271 项通过及 6 subtests，通过 --disable-socket 禁用网络。新增测试为合成资料，覆盖历史附加字段、原字段冲突、未知字段、非 Docling 及新导出保留原记录等情况。git diff --check 通过。

限制：云端没有本地五份原生快照，未声称真实 5/5 重导出已通过。下一步仅做 T024 零费用缓存复验。没有模型调用、检索、PDF 解析或仿真。

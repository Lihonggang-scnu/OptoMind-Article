# T037 / C2L-R3-0052 本地验收与 UV 续接

结论：Windows 材料来源哈希修复通过本地定向验收；原 UV 案例在真实任务单合同校验处受阻，未进入物理闭环。工单状态为 blocked，不将草案标成通过。

固定实现：`49861e169084976069d9ef1008e4fafc84b86bb8`；基准：`408fef514d4ed156ffde03c868a26cea3aba9dfa`。独立副本 `D:\OptoMind-Article-3\r3t037a`，分支 `codex/r3-t037-uv-20261016`。本地没有修改生产算法、提示词、科研流程、校验规则或科学输入。

- 8 项交付哈希一致；实际 Python/Review 组件/VeriTMM 导入均来自新 D 盘副本。
- 指定四组离线检查 **131 项通过**。CSV、RII 全元数据签名不变的回归及实际 Windows 同长度、恢复 mtime 的独立 toy 均返回新内容的正确 SHA256；旧 T036 失败证据保留。每次绑定重新读取字节，不宣称原子快照或性能提升。
- 七种原有膜层记录及 fused silica835、空气边界的当前字节、精确身份和 255–1100 nm / 1 nm / 846 点覆盖通过；无外推。原始宽泛无机介质类别与有限 execution_materials 分开，ZrO2 原覆盖不足记录仍未启用，835 缺 k 所采用的供应商零 k 约定明确保留。这是材料输入验收，不是设计的物理结果。
- 两项合成账本测试通过。保留原双账本、历史归属、原案例及原批次累计额度；每次真实请求在同时锁定原账本和子账本后原子预留，无新预算池。

实际启动命令：

```text
D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe -B -X utf8 D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T037\run_t037.py case --case e2e-04-solarblind-uv-triple --execute
```

运行结果：Qwen3.5-plus 的首次任务单及唯一共享格式修复均返回实际响应；两轮 `ok=false`，生产错误均为 `task sheet subgoal.original_span: must be an exact span of original_question`。最终 planning.status=invalid，case.engineering_status=blocked。原始任务、两轮回答、原始及实际转发提示、usage、格式修复原因和验收错误均保留。没有第三次请求，也未重跑已完成案例。最小复现及逐字段说明见 quality-review。

请求后续阶段：检索 0、路线生成 0、搜索行 0、新认证 0、反馈修订 0、细化 0。因此没有可报告的 UV 双/三目标数值变化、父子结构或材料机制结论，也没有修复提升完成率的对照证据。

失败具体位置：两轮 `subgoals[2].original_span` 都写成“尽可能抑制 700–1100 nm 近红外杂散光”；原题该并列项是“700–1100 nm 近红外杂散光”，前方通过“和”承接另一项，共享的动词没有在这一项重复出现。现有生产校验器用保存的实际回答复现同一错误。人工检查目标频段、指标和主要约束基本对应原题，但这不构成合同通过或物理验证。

费用按原始账本实际结算：本任务 **2 HTTP / 0.0371824 元**（0.0174056 + 0.0197768）；原 case04 累计同值。原 T032 三例批次累计 **8 HTTP / 0.3016536 元**；项目累计 **19.2660590000000000674 元**；pending/held/uncertain 均为 0。案例总额 8 元、批次 24 元及项目 220 元仍生效；原案例最多 4 HTTP，已用 2，后续不能因工单换号重置。

下一步：云端针对保存的两个实际回答和失败位置处理任务单原文片段合同问题，并决定剩余额度内的受控续接方式。本地不重写回答、不放宽校验、不自动补跑。

原始文件全部保留本地 `D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T037`。交接导出为普通文件；不包含密钥、认证头、环境文件、账本数据库、材料数据库、PDF 或权重。局部启动接线的首次失败及修正记录见 PARENT_WIRING_ATTEMPTS.json、WORKLOG.md，未掩盖历史失败。

# 本地修复与真实验证：云端审核入口

本次经用户授权推送审核分支 `codex/cloud-repair-validation-20261013`，不修改 main。
**不向 `Lihonggang-scnu/OptoMInd-Review` 推送任何提交。** Review 仅作为本地组件来源，其本地修改以文件和补丁放在本目录供审核。

## 阅读位置

1. [完整本地报告](LOCAL_REPAIR_REPORT.md)：结论、失败原因、修复、命令定位、验证边界和费用。
2. [实际运行记录](RUN_SUMMARY.json)、[费用汇总](COST_SUMMARY.json)、[本地交付版本](DELIVERY_COMMITS.json)。报告中的“未推送”是上传前本地交付状态；本次上传授权发生在该次验收之后。
3. Article 的 `runs/formal-01` 至 `formal-04`、`runs/pause-05`、`runs/resume-05`；请求/响应在 `calls/`，受影响测试日志在 `checks/`。
4. [小型同题前后对照](runs/MATCHED_SMALL_COMPARISON.json)、[恢复验收](runs/RECOVERY_ACCEPTANCE.json)。
5. [Review 本地差异](review-components/LOCAL_CHANGES.patch) 及 `review-components/changed-files/` 的准确已提交源码和测试；原有验证材料在 `review-components/validation/`。
6. 独立 VeriTMM 的同名分支，提交 `681dcb8293a01cdac8c1cb07f8d52896b0f09385`，目录 `local-validation/20261013`。

## 版本与事实

- 云端包目标：Article `491d7ddddfbdddbdbd11edffafc3299f242ab8d2`、Review `0cf7bc1f86e242cd7466243ff99c3d614d5f2aee`、VeriTMM `86de13615d98a3adfc0d7a026e28915570b8111d`。
- Article 真实两轮与恢复使用源码 `f39d433a30edb9ea49d9779f2fd2ac9eeed85b2b`；Article 后续提交增加验证记录和本审核入口，没有再改算法。
- Review 补丁基准为 `0cf7bc1f86e242cd7466243ff99c3d614d5f2aee`，源码目标为 `bef359f02c8a0a5f81cb4b10713a4b2be91790ad`。本地包含验证记录的交付为 `f30319eaefb7625081ee8a8a6a53df1208352899`，此提交没有推送到 Review。不要假定 GitHub Review HEAD 已包含这些对象。
- Review 最后一次真实模型 CLI 使用 `f234580a57c7c8ed2118de252b22422a94182180`。其后终止恢复修复通过 49 项受影响检查和真实检查点副本重放；导出修复通过 37 项检查并对真实成功计划重新导出，没有再整轮付费运行。
- Article 真实两轮设计评估为 53、50；较好候选的算术平均反射率为 `0.0353624086564206`，目标 `<0.03` 未达到。模型调整后第二轮更差，最终保留第一轮有效候选。没有证明全局最优或目标不可达。
- 累计估算费用 `7.7280928` 元，包含先前 `0.4619124` 元；总上限 80 元，未决预留为零。未上传凭据或账本数据库。
- Review 仅验证拆解/检索/解析/精读及有限规划交接；不在 Article 中引入完整综述写作或出版流程。
- 历史缺失响应未补造；没有完整 BODY、RCWA/FDFD、硬件或整篇论文生成。本轮没有新增实时外部 S2、PDF 或独立积分均值真实任务的验证。
- 同题前后仅一组，模型响应不同；不据此宣称一般收益提升。

请基于上述源码、原始请求/响应及产物审核，区分已证实生产缺陷、环境/模型输出问题和历史缺件；需要修复时给出复现依据。Review 上游继续保持只读。

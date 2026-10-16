# T041 零费用候选准入验收

工单 C2L-R3-0056 / R3-T041。固定实现 `84b050d53e5d07207c032f7d1fc4c150d47dcf1f`，基准 `0437ba26003d7920c6d37b3d9437351eecf42392`；独立目录 D:\OptoMind-Article-3\r3t041a。T040 已完成后才领取，没有热换 T040 源码、重跑或借用其额度。

指定4个文件测试 **114 passed / 0 failed**，sockets disabled；真实旧响应重放单次执行、退出0。固定源码/导入、7个实现文件哈希和4份原始输入字节哈希均匹配。完整云端派单SHA256校验、实际命令/退出记录、原日志和实际replay均保留。完整回归未重跑。

候选准备结果为 **10 条 ready、18 个合法实例、2 个被隔离的非法实例**。R2/R3/R6/R7/R8新增9个实例ID，但仅6种不同候选配方。唯一性按入射介质、衬底、实际层/厚度、全部目标的canonical JSON定义，不是认证次数、仿真缓存键或材料来源验证。所有 `execution_authorized=false`。

云端变更只把candidate模式下非空自由文字 `open_requirements` 从无条件否决改为明确警告。每条原文保留，warning字段仍为 `code=open_requirement_unresolved`、`original=原条目`、`status=unresolved`、`execution_scope=existing_legal_structures_only`。未把科学假设标为已解决；硬条件、typed compatibility、材料/目标/来源身份、范围检查保留。逐项原文与准备结构见 ROUTE_AUDIT.json。

严格路径的实测区别：R6的candidate-ready未解决契约在strict validation被拒绝（must be needs_revision）；标为needs_revision后strict validation可以通过，但strict materialization仍拒绝执行。不能笼统说所有未解决strict契约都无效。指定回归还覆盖缺family/instance、越界、未知材料/目标/来源、错误指标和冲突typed条件。

- R6/no_ar 的dAR=0、dAR2=0越出原family范围，仍隔离；没有裁剪或替代。
- R10/wide_pass 的dCav=260越出原family范围，仍隔离；R10仅single_peak合法参考，比较缺失、来源相关性未验证。
- R3的两个placeholder结构相同，不能当成不同层数对照。
- R7的29_layer标签实际仍是15层，不能宣称执行29层结构。
- R8的sio2_variant保留了原材料栈，不能宣称完成材料替换实验。

上述标签/解释问题如实记录，不把合法参考种子因为科学意见或描述不足而丢弃，也不编造未做的干预。所有原输入、合法结构、三原目标的波段/方向/积分归约保持不变；候选ready不证明文献语义支持、物理可用性或性能改善。将来执行仍需真实材料来源/覆盖、数值前检与单独工单。

本轮 **0 模型HTTP / 0 检索 / 0 solver或搜索行 / 0 梯度 / 0 新认证 / 0 元 / 0 账本写入**。原两个账本字节哈希与领取前相同。当前项目累计 **19.7726754000000000674 元**、pending/held/uncertain=0，引用真实T040终态；T040的case04累计6HTTP与原批次12HTTP均保留，无额外调用额度。

T040实际终态回执 `RCP-LOCAL-T040-COMPLETED-20261016T044624Z`，Drive `1F7UYUkfqtqEd29nHP_l05ttJ7-jHqsej`，结果提交 `5c453cdff95c6a9ec5dd609681be9a473e562790`。T040源码/交接包工作树干净、三个已封存快照manifest与完成时审计一致。原始 LOCAL_ACCEPTANCE.json 中名为t040_terminal_reference的字段实际指早期离线preflight；该原件保留，本报告与 PARENT_FINAL_ACCOUNTING_AND_T040_REFERENCE.json 提供真实终态和当前费用，避免把T040真实执行误读成零调用。

辅助脚本曾有引号、路径和strict probe选材问题，失败记录保留在 HARNESS_ATTEMPTS.json 与 logs/；指定测试和真实重放各执行一次。strict独立探针改用有精确来源的R6，未删除断言或改生产保护；原失败未覆盖。没有本地核心代码修改。

Drive claimed上传两次失败后，已用同ID在 codex/r3-handoff-relay 提交并回读 `79df5854aa25c494685ca397b52d5e6ee2d9a61c`，才开始验收。结果使用普通文件，数据库、凭据、PDF和缓存未上传。交接后停止；本轮新准入路线仍未执行。

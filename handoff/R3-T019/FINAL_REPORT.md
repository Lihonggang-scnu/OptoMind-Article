C2L-R3-0032 / R3-T019 本地验收结果

终态：blocked（预算入口未覆盖），零付费。离线验证通过；未生成真实 Qwen 任务单。

Article 基准 59e10e64794f431e6de7d7656af9f5307710f4a9，实际测试提交 713799fb55899b78427aed56cb643bc72526c19d，目录 D:\OptoMind-Article-3\r3t019a。Review 保持 775329cee02d4dacd108bf41969b1d21d26750e0。4 份下载材料的 SHA256、bundle 前置对象与祖先关系全部核对通过。源代码、提示词和依赖均未修改，旧 Article/Review 暂存状态及历史产物保留。

已验证：
- Python 3.12.13，已安装 ftfy 6.3.1、PyYAML 6.0.3、pytest 9.1.1、pytest-socket 0.8.1、numpy 2.3.5；没有安装依赖。
- 全部 76 项 focused 定向离线检查通过，包含一个独立进程执行的 4 项指标消费者检查；子进程只使用合成数组做通道选择和均值归约，不执行物理求解。
- run.py --help 正常退出。task_sheet、config、llm 和 optomind_research 均来自新 focused_research/vendor/review，未串入 F 盘或旧 Review 包。
- 示例和契约测试保留 G1/G2、450–650 / 700–1000 nm、45 度、unpolarized 和 wavelength_integral_mean，以及材料、基底与层数约束。历史 v1 不被覆盖；不支持的要求保持 needs_revision 并拒绝执行。检索关联检查使用注入的离线夹具，没有访问检索服务。

预算阻塞证据：
- 原包装器 budgeted_entry.py SHA256=1a6832ade3f4b05d24a31102d106894d97e8e1f4a7af24883349783c4f88d512，与原批准版本一致。其 run() 仅路由到旧 scripts.run_tmm_research_harness 和 scripts.upgrade3.progressive_review_plan；保护点是 ArticlePlusQwenClient、QwenFlashOnlyClient 和 QwenDirectClient。
- 新路径为 focused_research/run.py → task_sheet.generate → vendored QueryPlannerAgent._call_query_plan_model → llm.qwen_chat_client.call_qwen_chat → _open_qwen_request。原包装器没有该独立入口路由或上述函数的费用预留接线；不能证明 2 元单项上限和原账本在此生效。
- 离线注入“连接重置→无效任务单→连接重置→无效任务单”，实际生产重试/格式修复代码运行 2 轮、每轮 2 次，共 4 次合成传输尝试。这个探针不是经原包装器的正式执行，也没有真实网络请求；它证明 stock 重试设置本身不能限制为全任务最多 3 次。
- 公开配置中主模型为 qwen3.5-plus，回退链含 qwen3.7-flash 和 qwen-flash；后者不在原包装器的两模型白名单内，后续接线须明确约束，不得默默扩展。未加载真实凭据或调用这些模型。

费用：原账本只读复核 322 条预留均 settled，累计 17.329267 元，pending/held/uncertain=0，可用 122.670733 元。T019 新增费用 0、真实 HTTP 尝试 0；原账本文件哈希未变。未上传账本数据库、凭据、环境文件或 ZIP。

尚未验证：真实 Qwen 响应的语义完整性、任务单一次生成成功率及检索式相关性；没有真实模型产物。非偏振 s/p 等权物理计算、波段端点/采样覆盖和材料/层数的下游执行均未运行，不能由本轮离线指标夹具推出科学通过。

云端下一步：为此独立入口接入原同一账本的调用前预留、2 元任务总额、包括传输重试/格式修复/模型和密钥回退的全任务最多 3 次尝试、未知费用占额与原模型白名单。保留独立模块来源保护，不改科学目标或核心契约。给出固定提交及最小离线覆盖证明后，下发新工单再进行受控真实 plan。按本工单未在本地重写计费或核心生产代码。

运行命令（输出和解释器记录在 offline/RESULTS.json）：
1. D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe -X utf8 -m pytest -q --disable-socket focused_research/tests --basetemp <本任务新建临时目录> -o cache_dir=<本任务缓存目录>
2. 同解释器 -X utf8 focused_research/run.py --help
3. 同解释器 -X utf8 probe_budget_coverage_offline.py（仅离线合成传输；未开网络、未打开账本、未读取凭据）

通信：Drive 读取正常；upload_file 三次在 MCP 连接层失败，未收到 Drive 服务端文件/权限错误。已核对目标文件、UTF-8 JSON、文件路径和目录 ID，并复查目录没有重复上传文件；浏览器控制也不可用。用户明确允许临时 GitHub 接头，领取回执先在 codex/r3-t019-relay-20261015 以 3af928787545da7e5d28256c2f4feed701a69e40 发布并核对远端，随后才开始源码接收和测试。终态消息与回执沿用此分支，保留 Drive 待补传标记。旧 C2L-R3-0031/T018 未启动排程继续暂停，未自动跑旧案例。

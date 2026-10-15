T019 独立20元批次：真实 Qwen 运行结果

Qwen API 与费用接线均可用；正式任务单验收未通过。终态 blocked，分类 model_output_contract / original_question_echo_changed。

用户直接授权“新开一个单独账本，预算20元”。新批次 R3-T019-DEDICATED20 与原0032 blocked消息分开登记；原账本与旧记录保留。实际账本位置：D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T019-DEDICATED20\private\budget.sqlite，限额20元、全任务最多3次请求，原预算库文件 SHA256 未变。

运行证据：
- 冻结 Article 源码 713799fb55899b78427aed56cb643bc72526c19d，工作副本 D:\OptoMind-Article-3\r3t019a；Review运行时来源775329cee02d4dacd108bf41969b1d21d26750e0。
- 独立费用桥接和准入提交31cb967960144cac36ad3e75928c8bd7852a3fc5，正式生产 run.main 的 plan 子命令、完整原题、模型与科学提示词没有改写。预算适配器只替换 planner.call_qwen_chat 的传输接缝，复用原 AuditedClient / TaskBudgetGuard / GlobalBudgetLedger；未修改原140元入口。
- 本批次7项费用接线检查通过：费用预留发生于HTTP之前；余额不足拒发；第4次请求拒发；格式修复计费；超时和缺失usage保留占额；独立模块来源及plan-only门禁。前轮76项冻结源码检查未重复运行。
- 真实模型qwen3.5-plus、temperature=0.1、max_output_tokens=7000、JSON、thinking=false、单密钥、传输自动重试0。两次真实请求有不同provider request_id，均finish_reason=stop、complete=true、usage完整、replayed=false。第二次由原有一次格式修复流程自动发起，未使用第三次请求。
- 首轮输入/输出token=4120/2560，费用0.015584元；修复轮=6796/2560，费用0.0177248元。合计0.0333088元，已全部settled，pending/held/uncertain=0，独立余额19.9666912元。费用来自provider usage按原运行时费率结算估算，未进行供应商账单对账。

真实失败与定位：
1. 两个实际发送的user原题都与v2原题逐字相同，第二轮还带有原校验错误和首轮原始回复。
2. 两轮原始provider回复都把原题开头“可见-近红外”改为“可见 - 近红外”：在原文字符偏移17和18处各插入一个ASCII空格。错误已存在于原始HTTP响应内容中，JSON解析器没有引入此改动。
3. 两轮校验错误相同：task sheet original_question: must preserve the full input exactly。修复轮内容与首轮完全相同，未修复问题。生产入口退出2，planning.status=invalid、summary.status=failed，正式task_sheet/query_plan保持空对象，没有检索或物理执行。
4. 保留草案的G1/G2、450–650/700–1000 nm、45度、非偏振s/p等权、wavelength_integral_mean、熔融石英基底、仅TiO2/SiO2、平面膜和<=24层均与请求一致；9项约束原文span保留。5条检索式及其G/R/M关联在草案中存在，正式可执行计划尚未产出，检索相关性未测试。
5. 两份草案除original_question的两个新增空格外，与冻结提示词中的同题worked example完全相同。本次证明真实传输、费用耦合和同题草案生成；不构成未见任务泛化、检索质量或物理设计达标证明。

没有放宽逐字校验，没有替换原始回复或把草案填入正式task_sheet.json。草案供审阅，位于rejected-drafts；原始请求、provider回复、usage、预算状态及native raw文件位于model-audit。云端负责核心修复，本地未修改任务单核心/提示词或新增科学答案。

复现与下一步：
- 离线使用rejected-drafts/round-1.task_sheet.json或round-2，对原v2问题执行task_sheet.validate(sheet, question=original_question)，可复现逐字保真错误，无需付费。
- 云端需处理原题保真与一次修复无效的生产接缝，并保留真实原始回显、完整约束校验及来源身份。是否将可信请求原文与模型回显分别存储，由核心维护者评估；不应删断言、静默忽略空白或把本轮改成通过。
- 发布固定修复提交后可使用本批次剩余额度复验。当前没有自动付费重跑、没有启动旧T018或新增案例。

实际命令：
Python=D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe
离线：python -X utf8 verify_transport.py（内部pytest --disable-socket；7通过）
准备：python -X utf8 run_launcher.py（0请求，来源与20元账本核对）
真实：python -X utf8 run_launcher.py --execute（正式入口参数见RUN_STARTED.json和logs/live-entry.command.json）
分析：python -X utf8 audit_task_sheet.py（只读复核被拒草案；审计v2修正了空计划的关联判定，没有重跑模型）

原140元账本累计17.329267元保持不变；独立本批次0.0333088元。数据库、凭据、环境文件均留本地，GitHub只上传代码接缝与脱敏完整文件，没有ZIP，没有向Review上游推送，没有修改main。

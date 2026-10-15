# T026 本地联动测试

## 固定版本与边界

基准：e2e3a54aa6bd5b3fd1757b82b718d44c24391417。实现SHA见ready。独立目录，不覆盖T022–T025、母项目、main或原账本。

读取START_HERE，消息/任务去重后留下claimed。本任务总付费上限3元，计入既有15元链、20元子池、140元总预算；最多2次模型HTTP，含合同修复，传输重试0。最近累计17.711091元，占额0只是历史回传；派发前用原计费配置核对settled/pending/held/uncertain与当前价格并预留。

数值任务全程最多64个唯一新候选认证评价，最多2个进程；每个评价内部还会有物理核验，必须单独报告Workbench调用口径。两轮合计预算，不是每轮各64。不得重检索、下载或解析PDF、重做题面、运行消融/全套回归、安装依赖或升级模型。核心问题回云端。

## A. 零费重编译旧草案

复用T025第二轮原始research_routes、task_sheet、evidence_items、design_constraints。使用新版route_stage validate/materialize读取，不手工改原响应。

```text
<ArticlePython> focused_research/route_stage.py validate --task-sheet <原task_sheet> --evidence-file <原evidence_items> --constraints-file <原design_constraints> --routes-file <T025第二轮原始routes> --output <新T026/compile>
```

检查candidate_report：引文不匹配为未核实转述，逐路线拒绝原因明确；预计7条可执行、14实例，但不得为了吻合数字改数据。原始响应哈希不变。

## B. 第一轮真实计算与观察

使用本地已经核实的真实材料映射与光学数据库，记录数据集身份、覆盖范围和基底假设；不要为了跑通把缺失材料换成虚构常数。所有目标、45度及偏振按原任务单继承。

波长网格显式提供JSON数值列表，包含目标波段端点及内部样本。建议450至1000nm、1nm间隔；这是展示测试采样设置，不是连续频谱或制造验收保证。

```text
<ArticlePython> focused_research/scientific_stage.py run --task-sheet <原task_sheet> --evidence-file <原evidence_items> --constraints-file <原design_constraints> --routes-file <T025第二轮原始routes> --optical-media <已核实映射JSON> --wavelengths <网格JSON> --cache-dir <新T026/cache> --max-new-evals 14 --max-workers 2 --execute --output <新T026/round1>
```

该命令内部逐路线编译，先保存原预测，再执行原生求解和核验，返回G1/G2及局部光谱变化。个别路线失败如实保留，其余继续。若材料或物理核验导致全部无有效观察，停止，不进入模型修订。

可用同一输入、新输出目录、max-new-evals=0做一次缓存复用检查，确认新Workbench调用为0；这是缓存功能验收，不重新做付费工作。归档旧文件不变。

## C. 用真实观察修订一次候选

沿用原本地计费planner接线；模块不自建客户端。只调整测试启动器到 scientific_stage.main，传入以下参数和原planner：

```python
scientific_stage.main([
  'revise', '--task-sheet', '<原task_sheet>',
  '--evidence-file', '<原evidence_items>',
  '--constraints-file', '<原design_constraints>',
  '--previous-run', '<新T026/round1>',
  '--execute', '--output', '<新T026/revision>'
], planner=原计费planner)
```

候选数量不限，鼓励约10条及以上；沿用T025已确认可用的模型/输出容量，不能临时更换模型。检查实际请求确实包含previous_physical_iteration。修订可保留好结构、改变参数或提出新方向，但未计算的新方案不能标成已改善。

## D. 显式计算下一版本后停止

如果有可执行新合同，重复B命令，将routes-file改为revision/research_routes.json，输出全新round2，沿用同一个缓存；max-new-evals为64减去本轮已经启动的新认证评价数，不能重置预算。没有余额就部分回传；不要为凑齐候选擅自扩支。

## 快速报告

- 原草案保留/拒绝数、原因；依据标记及参数diff是否合理。
- 第一轮与下一轮每条路线G1/G2、有效谱线及物理核验状态；没有明确谱系对应时，不把同名路线自动当作同一结构的改善。
- 选一条最有内容的轨迹：原理由/预期→实际响应→模型下一动作→第二轮表现。允许解释错误、性能下降和路线停止；不伪造理想改善。
- 缓存新评价/命中、Workbench调用口径、时间、模型HTTP与费用/占额；不把Workbench计数称全部内部求解。
- 哪些观察真正被模型引用，以及是否存在错误物理解释；注入上下文不等于模型正确理解。

只上传普通报告、必要JSON/光谱数据、固定提交与回执。不要上传密钥、账本数据库、原PDF或权重。完成后停止，禁止自行开展后续实验。

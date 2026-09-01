# OptoMind-Article 研究控制台开发交接（面向 Luna）

更新日期：2026-09-01
工作目录：`F:\OptoMind-Article-1`
目标仓库：`https://github.com/Lihonggang-scnu/OptoMind-Article`
当前分支：`main`
当前基线提交：`9be701a Merge GitHub repository initialization`
当前状态：本轮实现、最终验证和白名单提交已经完成；GitHub 推送待本机恢复到 github.com:443 的网络连接。

## 1. 用户本轮最终目标

把现有项目补成一个可公开使用的“自主光学研究控制台”，同时满足以下要求：

1. 前端不只是回放历史结果，还必须允许使用者直接输入真实中文光学薄膜需求，启动现有完整 harness，并持续查看研究阶段、事件、迭代、候选和最终结果。
2. 同一个前端继续支持六组正式端到端运行的静态证据回放，能够从最终结果返回题面、冻结评分标准、路线、逐轮反馈和原始文件。
3. 不能只在原开发机上工作。需要补齐 Windows、macOS、Linux 及容器/云端运行方式，处理路径、端口、输出持久化、密钥和远程访问控制。
4. 不得制造“伪真实结果”。没有 Qwen 密钥时必须明确拒绝启动真实研究；模拟 runner 只用于界面和接口联调。
5. 完成代码、测试、公开说明后，将改动推送到现有 GitHub 仓库。

用户在上一轮暂停了工作并要求交接；本轮已在此基础上完成实现并提交为本地提交 `70eea62`，但截至本次交接更新时仍**尚未推送**。

## 2. 已完成的实现

### 2.1 六组静态证据回放

已新增：

- `code/optomind_optics/harness/static_replay.py`
- `code/replay_ui/index.html`
- `code/replay_ui/assets/styles.css`
- `code/replay_ui/assets/app.js`
- `code/scripts/run_static_replay_ui.py`
- `code/tests/test_static_replay.py`

功能状态：

- 自动扫描 `code/outputs/tmm_research_harness/`，不另建手工摘要数据源。
- 已正确识别六组固化运行。
- 页面可查看原始用户题面、冻结评分标准、路线来源、逐轮冻结得分、反馈状态、最终排名、代表候选和原始 JSON/JSONL/Markdown 文件。
- 已验证的六组汇总统计为：6 组运行、24 条路线、126 轮记录、105 次完成执行、926 个有效候选、101,711 次前向计算。
- 静态回放是只读的，不调用 Qwen、Semantic Scholar、优化器或 VeriTMM，也不会修改六组正式资产。

### 2.2 真实提问与运行后端

已新增：

- `code/optomind_optics/harness/live_research.py`
- `code/optomind_optics/harness/research_console.py`
- `code/scripts/run_research_console.py`

`LiveRunManager` 已实现：

- 使用固定脚本 `scripts/run_tmm_research_harness.py` 启动完整 harness。
- 用户中文题面写入每次运行目录下的私有问题文件，再交给子进程；不会把题面和密钥暴露在命令行进程列表中。
- 使用 `subprocess` 参数数组并设置 `shell=False`，浏览器不能注入任意命令或任意 runner 路径。
- 前端参数全部有本地边界：迭代上限、初始路线数、路线规划上限、单路线轮次、最少探索轮次、墙钟时间、编译模型档位等。
- 默认一次只允许一个真实研究进程；服务端可用 `OPTOMIND_MAX_CONCURRENT_RUNS` 调整，但内部仍限制在 1–4。
- 支持 `starting / running / stopping / stopped / completed / failed / interrupted` 状态。
- 支持主动停止任务。
- 服务重启后可根据 `LIVE_RUN_REQUEST.json` 和结果目录恢复已完成任务、识别被中断任务。
- 从 `RESEARCH_EVENTS.jsonl`、`iterations/` 和最终结果中投影进度，不修改 harness 原始产物格式。
- 启动前要求 Qwen 密钥存在；Semantic Scholar 密钥为可选。密钥内容不会进入 API 响应。

`ResearchConsoleServer` 已实现以下接口：

- `GET /healthz`
- `GET /api/catalog`
- `GET /api/runs/{run_id}`
- `GET /artifacts/{run_id}/{path}`
- `GET /api/live/readiness`
- `GET /api/live/runs`
- `GET /api/live/runs/{run_id}`
- `POST /api/live/runs`
- `POST /api/live/runs/{run_id}/stop`

安全边界：

- 请求体限制为 64 KiB。
- 远程监听时，启动/停止任务需要 `OPTOMIND_UI_ACCESS_TOKEN` 对应的 Bearer Token。
- 本机回环监听可无 token 使用。
- 如果服务绑定远程地址但没有配置 token，读取接口仍可用，所有状态修改接口会拒绝请求。
- 只有服务端环境变量 `OPTOMIND_RUNNER_PATH` 可覆盖 runner，前端请求不能指定可执行文件。该能力主要用于测试。

### 2.3 前端双模式控制台

`code/replay_ui/` 已从纯静态回放扩展为双模式：

- “发起真实研究”页签：中文问题输入、示例题面、基础/高级参数、服务就绪状态、研究启动、停止、阶段时间线、事件流、运行历史、完成后进入回放。
- “静态证据回放”页签：保留六组既有回放能力。
- 云端需要访问口令时，前端显示口令输入框；口令只保存在当前 JavaScript 内存，不写入 `localStorage` 或 URL。
- 如果部署环境没有任何历史运行，界面会自动进入真实研究页，回放页签暂时禁用；第一条运行完成后可直接切换到回放。
- 如果 Qwen 密钥为空，页面显示“待配置”并禁用开始按钮。

### 2.4 测试夹具

已新增：

- `code/tests/test_live_research.py`
- `code/tests/fixtures/fake_live_runner.py`

假 runner 只用于自动化测试和浏览器联调，会生成很小的确定性事件与结果；它不属于真实研究结果，不得在公开说明中称为科研实验。

## 3. 已完成的验证

已执行并通过：

```powershell
Set-Location -LiteralPath F:\OptoMind-Article-1\code
node --check replay_ui\assets\app.js
python -m py_compile `
  optomind_optics\harness\live_research.py `
  optomind_optics\harness\research_console.py `
  optomind_optics\harness\static_replay.py `
  scripts\run_research_console.py `
  scripts\run_static_replay_ui.py
python -m pytest tests\test_live_research.py tests\test_static_replay.py -q
```

最近一次结果：`8 passed in 1.32s`；新增测试覆盖了 HTTP 健康检查、就绪状态、本机无口令启动、远程无口令拒绝和正确 Bearer Token 启动。

浏览器联调已覆盖：

1. 在真实项目目录启动控制台，六组历史运行全部正常呈现。
2. Qwen 密钥文件为空时，服务端 readiness 返回未就绪，真实启动请求返回 HTTP 422，且没有生成假结果。
3. 使用假 runner 和临时输出目录启动空白控制台，从“输入问题 → 启动 → 事件更新 → 完成 → 进入回放”全流程成功。
4. 浏览器控制台在最近一次检查中无错误日志。
5. 测试中发现并修正了墙钟时间输入框的 HTML 步长/最小值不一致问题；当前小时输入最小值为 `0.25`。

最近一次真实目录浏览器联调使用临时端口 `52301`，六组回放正常呈现，真实研究页在 Qwen 密钥为空时正确显示未就绪并禁用启动按钮，浏览器无错误日志。随后命令行接口复核使用临时端口 `51806`，六组回放、健康检查、前端首页、JavaScript 资源和就绪状态均通过，两个临时端口均已释放。

## 4. 本轮已补齐与仍需收尾的工作

### 4.1 跨设备与云端封装（已完成）

已新增并核对：

- `code/requirements-runtime.txt`：实时容器所需依赖，包含运行时实际需要的数值计算、优化、TMM、Qwen 客户端和文本处理依赖。
- 根目录 `Dockerfile`：Python 3.11、非 root 用户、`/app/code` 与同级 `/app/veritmm`、`/healthz` 健康检查、`PORT` 和 `/data/runs` 配置。
- 根目录 `.dockerignore`：排除 Git 元数据、密钥、缓存、测试文件和约 2.9 GB 的六组历史输出，避免默认云镜像过大。
- 根目录 `compose.yaml` 与 `.env.example`：本地容器启动、环境变量注入和持久卷配置。
- 根目录 `render.yaml`：可选的 Render 单实例 Docker 服务、健康检查、持久磁盘和环境变量声明。
- `README.md`、`AGENT_GUIDE.zh-CN.md`：统一启动入口、Windows/macOS/Linux、局域网访问、容器和云端部署说明。

六组历史记录仍保留在源代码工作树的 `code/outputs/tmm_research_harness/` 中供 Python 静态回放；默认 Docker 镜像不复制它们，新研究写入独立的持久目录 `/data/runs`。如果要在容器中展示历史记录，应通过单独只读归档卷挂载，不能与新运行的可写目录混用。

本机 Docker 客户端可调用，但 Docker Desktop 的 Linux 引擎当前未启动，因此没有虚构镜像构建结果；已完成 Dockerfile、Compose 和 Render YAML 的静态检查，并完成 Python、JavaScript、HTTP API 和浏览器联调。

### 4.2 仍需收尾的事项

- 恢复本机到 `github.com:443` 的 TCP 连接后，执行 `git push origin main`。
- 推送完成后用 `git ls-remote origin refs/heads/main` 核对远端是否为 `70eea62`。
- 若仍无法连接，不要重建提交或改写历史；本地 `70eea62` 已是完整可推送提交。

### 4.3 真实在线轻量测试

此前用户要求项目公开前清空密钥文件，因此当前项目中的 Qwen/S2 密钥模板应为空。本轮没有启动付费真实研究。没有重新配置密钥时只能做 readiness、拒绝路径和假 runner 联调，不能声称完成真实在线测试。

## 5. 建议的继续顺序

1. 恢复到 GitHub 的网络连接。
2. 推送现有本地提交 `70eea62`，不要重复创建内容相同的提交。
3. 用远端只读查询确认 `origin/main` 已更新。

## 6. Git 与文件边界（非常重要）

当前 `git status --short` 包含两份与本轮控制台开发无关的未跟踪 Word 文档：

- `OptoMind-AI-for-Optics评审白皮书-Claude-Opus-5独立版.docx`
- `OptoMind-AI-for-Optics评审白皮书-Claude-Opus-5独立版-优化.docx`

这两份文件是用户资产，**不得加入本次代码提交，不得删除，不得移动**。

提交时不要使用 `git add .`。必须显式暂存目标文件，例如：

```powershell
git add -- `
  README.md `
  AGENT_GUIDE.zh-CN.md `
  HANDOFF_LUNA.zh-CN.md `
  Dockerfile `
  .dockerignore `
  compose.yaml `
  .env.example `
  code/requirements-runtime.txt `
  code/requirements-handoff.txt `
  code/optomind_optics/harness/live_research.py `
  code/optomind_optics/harness/research_console.py `
  code/optomind_optics/harness/static_replay.py `
  code/replay_ui `
  code/scripts/run_research_console.py `
  code/scripts/run_static_replay_ui.py `
  code/tests/test_live_research.py `
  code/tests/test_static_replay.py `
  code/tests/fixtures/fake_live_runner.py
```

暂存后必须执行：

```powershell
git diff --cached --stat
git diff --cached --check
git status --short
```

确认没有 `.docx`、真实密钥、临时输出、缓存和假 runner 生成结果进入提交。假 runner 的源码测试夹具可以提交，但它生成的临时运行目录不能提交。

## 7. 密钥与隐私约束

- `code/api_keys/qwen-api-key.txt` 和 `semantic-scholar-api-key.txt` 是公开空模板；不要填入真实值后提交。
- 推荐线上使用环境变量：`QWEN_API_KEY`、`SEMANTIC_SCHOLAR_API_KEY`。
- 云端写操作必须配置高强度随机 `OPTOMIND_UI_ACCESS_TOKEN`。
- 不要把 token 放在 URL、README 示例值、日志、截图或 Git 历史中。
- readiness 只能返回“是否配置”，不能返回密钥内容、长度、前后缀。

## 8. 关键环境变量

当前实现识别：

- `QWEN_API_KEY`
- `SEMANTIC_SCHOLAR_API_KEY`
- `OPTOMIND_UI_ACCESS_TOKEN`
- `OPTOMIND_ALLOW_UNAUTHENTICATED_RUNS`
- `OPTOMIND_MAX_CONCURRENT_RUNS`
- `OPTOMIND_RUNNER_PATH`（仅服务部署者/测试使用）
- `OPTOMIND_HOST`
- `OPTOMIND_OUTPUT_ROOT`
- `PORT`

远程部署不建议设置 `OPTOMIND_ALLOW_UNAUTHENTICATED_RUNS=1`。

## 9. 本地启动与联调命令

真实项目控制台：

```powershell
Set-Location -LiteralPath F:\OptoMind-Article-1\code
python scripts\run_research_console.py
```

只读兼容入口：

```powershell
python scripts\run_static_replay_ui.py
```

无浏览器自动打开：

```powershell
python scripts\run_research_console.py --no-open --host 127.0.0.1 --port 8765
```

跨设备局域网监听（务必先设置访问口令）：

```powershell
$env:OPTOMIND_UI_ACCESS_TOKEN = '<由部署者生成的随机强口令>'
python scripts\run_research_console.py --no-open --host 0.0.0.0 --port 8765
```

不要把上面的占位符替换成真实口令后写入文档或提交。

## 10. 当前工作树概要

本轮目标文件已经提交到本地 `main`，提交为 `70eea62 Add live research console and portable deployment`。工作树中只剩两份与本轮无关、未跟踪的 Word 用户资产；它们没有进入提交。当前没有活动的本地研究控制台进程；最终定向验证已完成，但 GitHub 推送因当前 TCP 网络不可达尚未完成。

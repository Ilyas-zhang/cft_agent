# GLM-5.1 集成与验收记录

本文件记录对上游 `verialabs/ctf-agent` 的改造、实际测试结果、以及尚未验证的部分。
**真实 GLM-5.1 API 已于 2026-10-27 验证**：通过 Anthropic Messages 兼容端点完成了
1 次最小工具调用检查、1 道本地 HTTP 题、1 道本地 TCP 多轮题、1 次真实 coordinator
无人值守测试（自动发现并并发解出上述两题），以及 1 次 MEA 2026 Qual 本地复现
（6 题中 2 题确认，`passed=false`）。Key 仅存在于本地 `.env`，未写入源码、日志或
本文件。脚本化模型（`FunctionModel`）仍用于确定性 E2E 回归。

## 1. 模型接入

- 统一通过 Pydantic AI 的 `AnthropicModel` + `AnthropicProvider(api_key, base_url)` 调用
  Anthropic Messages 兼容接口（`backend/models.py`）。
- 也支持智谱原生 `ZaiModel`/`ZaiProvider`（`zai/<model>` spec）。
- `claude-agent-sdk` 与 `codex` 后端无法运行 GLM，已从求解路径移除；所有 spec 都路由到
  通用 Pydantic AI Solver。
- `QUOTA_FALLBACK` 置空：不会在未授权时自动切换到其他付费模型。
- 模型名不写死：`MODEL_NAME` + `--models` 可配置，默认 `anthropic/GLM-5.1`。

### 配置项（`.env` / 环境变量）

| 变量 | 含义 | 默认 |
|---|---|---|
| `ANTHROPIC_BASE_URL` | Anthropic Messages 兼容接口 base URL；智谱示例 `https://open.bigmodel.cn/api/anthropic`（不要用 OpenAI 兼容的 `/api/paas/v4`） | 空（SDK 默认） |
| `ANTHROPIC_API_KEY` | API Key（不要提交；本项目读取此变量） | 空 |
| `MODEL_NAME` | 模型 ID，填账号/网关给出的准确值 | `GLM-5.1` |
| `MODEL_TIMEOUT` | 单请求超时（秒） | 300 |
| `MAX_OUTPUT_TOKENS` | 单次最大输出 token | 128000 |
| `MAX_RETRIES` | 模型请求重试次数 | 3 |
| `MAX_CONCURRENCY` | 预留；当前并发由 `MAX_CONCURRENT_CHALLENGES` × 模型数决定 | 5 |
| `API_RETRY_DELAY` | 预留；实际退避由 provider SDK 管理 | 1.0 |
| `MAX_COST_PER_CHALLENGE` | 单题预算（USD） | 10 |
| `MAX_COST_TOTAL` | 全局预算（USD），达到后停止所有 swarm | 500 |
| `MAX_ATTEMPTS_PER_CHALLENGE` | 单题最大 bump 次数 | 10 |
| `MODEL_INPUT_PRICE_PER_MTOK` 等 | 自定义单价（USD/1M tokens） | 0 |
| `WORKSPACE_ROOT` / `SANDBOX_BIND_ROOT` | DinD 路径映射 | 空 |

超时/重试已写入 `ModelSettings(request_timeout, timeout, max_retries)`，对所有 provider 生效。

凭据放在项目根目录 `.env`（已在 `.gitignore` 中），不要把 Key 发到聊天或写进源码。注意
Claude Code 自身常用 `ANTHROPIC_AUTH_TOKEN`，本项目读取的是 `ANTHROPIC_API_KEY`；
若只在 Claude Code 的 settings `env` 中配置，`docker exec` 不会自动继承，需要显式转发。

## 2. 运行方式

```powershell
# 主机（Windows PowerShell）
copy .env.example .env   # 填入 ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / MODEL_NAME

# 构建 agent 镜像与最小沙箱镜像
.\scripts\run-agent.ps1 -BuildAgent -BuildSandbox -Challenge /work/testbed/challenges/http-easy

# 真实模型最小检查（一次请求 + 一次工具调用）
docker run --rm -v "${PWD}:/work" -w /work --entrypoint python \
  ctf-agent:latest scripts/check_glm.py
# .env 位于挂载的项目目录中；-w /work 使 Settings 读取 /work/.env
# --entrypoint python 覆盖镜像默认入口 python -m backend.cli
```

`run-agent.ps1` 只挂载项目目录与 Docker socket，密钥从 `.env`/环境变量读取，不会出现在
命令行或日志中。沙箱容器由 agent 通过宿主 Docker 创建；`WORKSPACE_ROOT=/work` 与
`SANDBOX_BIND_ROOT=<宿主路径>` 解决 DinD 绑定路径问题。

## 3. 安全修复：禁止伪造 flag

原实现有两处会把模型编造的 flag 当成已确认：

1. `do_submit_flag` 把 CTFd 的 `already_solved` 也视为确认。真实 CTFd 对**已解出题目的任意
   提交**都返回 `already_solved`，因此模型可借此把任意字符串报告为 flag。
2. Swarm 在已有 `confirmed_flag` 时，对任意新 flag 直接返回 `True`；同时 Solver 优先采用
   模型最终输出而非 CTFd 确认值。

修复后：

- 只有 CTFd 返回 `correct` 才算确认；`already_solved` 明确标注 “NOT verified”。
- Swarm 仅在提交 flag 与已确认 flag **完全一致**时才返回确认。
- Solver 的最终 flag 以 `deps.confirmed_flag` 为准，绝不采用未确认的模型输出。
- `CORRECT_MARKERS` 改为前缀匹配，避免 `INCORRECT` 中包含 `CORRECT` 的误判。

回归测试：`tests/test_flag_safety.py`；E2E 中 `fabrication-check` 与
`already-solved-fabrication` 两个场景均必须为 `gave_up`/无 flag。

## 4. 在线交互能力

- `backend/tools/interactive.py` 向 solver 注册 13 个有状态工具：
  `http_request`（cookie/重定向/JSON/表单/原始 body）、`http_diff`、`http_history`、
  `http_close`；`tcp_connect`、`tcp_send_recv`、`tcp_send`、`tcp_recv`、`tcp_recv_until`、
  `tcp_send_bytes`、`tcp_interactive`、`tcp_disconnect`、`tcp_status`。
- 会话按 solver 隔离（scope = 题目:模型:实例），并发题目不会共享 cookie 或 socket。
- 每个 solver 停止时清理自己的会话。

## 5. 状态持久化与预算

- `backend/state.py` 原子写入 `runtime/coordinator_state.json`（tmp + replace），包含：
  已确认结果、已解题目、累计费用。
- coordinator 启动时恢复结果/费用/已解集合，避免重启后重复消耗或重复解题。
- 单题预算按该题 agent 费用前缀统计；全局预算达到后杀死所有 swarm 并拒绝新 swarm。
- `CTFdPoller.get_event` 不再吞掉 `CancelledError`（此前会导致 Ctrl+C/取消后进程挂死）。

## 6. 实际测试结果

环境：Docker Desktop (WSL2)；agent 容器 Python 3.14.9；沙箱镜像 `ctf-sandbox`
（Python 3.13，1.48GB，含 pwntools 4.15.0/z3/sympy）；宿主 Docker socket；
mock CTFd + 本地 HTTP/TCP 靶场。真实 GLM-5.1 调用走 Anthropic Messages 兼容端点，
Key 仅存于本地 `.env`。

| 验收项 | 结果 | 证据 |
|---|---|---|
| 1. Python 3.14 容器启动 | **实际通过** | `ctf-agent:latest`，Python 3.14.9 |
| 2. 依赖安装 | **实际通过** | agent 镜像内 33/33 模块导入；`ctf-sandbox` 构建成功并用于全部 E2E |
| 3. GLM 配置加载/模型解析 | **实际通过** | `pytest`：配置/AnthropicProvider 构造/超时重试 |
| 4. 基础模型请求 | **实际通过（真实 API）** | `check_glm.py`：413 in / 18 out，输出 42 |
| 5. 真实工具调用 | **实际通过（真实 API）** | 模型调用 `add`；真实 HTTP/TCP 题中调用工具并拿到 flag |
| 6. 本地 HTTP 题求解 | **实际通过（真实 API）** | GLM-5.1：176.9s，20 次请求，133.4k in / 116.0k cached / 2.8k out，`flag{mock_http_1337}` 经 mock CTFd 确认 |
| 7. 本地 TCP 多轮题求解 | **实际通过（真实 API）** | GLM-5.1：27.8s，6 次请求，19.6k in / 18.3k cached / 182 out，`flag{mock_tcp_4242}` 经 mock CTFd 确认 |
| 8. 模型不能伪造 flag | **实际通过** | 两种路径均 `gave_up`、flag 为空 |
| 9. 超时/断连处理 | **部分验证** | 模型/沙箱/TCP 超时已配置；连接拒绝返回错误而非抛异常（`test_connection_failures_are_reported_not_raised`）；未做真实 provider 断连演练 |
| 10. 并发题目不污染 | **实际通过** | 两题并发 1.01s（最终 `ctf-sandbox`），flag 无交叉，会话清理为 0 |
| 11. 无人值守模拟 | **实际通过** | coordinator 事件循环 6.10s 解出 2 题并写状态快照 |
| 11b. 真实 coordinator 无人值守 | **实际通过（真实 API）** | GLM-5.1 coordinator 自动发现并并发解出 `http-easy` + `tcp-menu`，464.55s，479,113 tokens（420.7k cached / 10.2k out），两题均经 mock CTFd 确认，状态快照写入 |
| 11c. MEA 2026 Qual 本地复现 | **部分通过（真实 API）** | 6 题中 `Evidence`、`whisper_evidence` 经 mock CTFd `correct` 确认；`baiby-pwn`、`popcnt_oracle`、`reduce-reuse-recycle`、`teto` 未解出；1801.11s，4,876,494 tokens，`passed=false`；`hokan` 因无 SageMath 排除 |

命令与结果：

```
pytest tests/                 -> 52 passed, 2 skipped
testbed/e2e_local.py          -> 6/6 scenarios passed (SANDBOX_IMAGE=ctf-sandbox)
testbed/e2e_concurrent.py     -> PASS, cross_contaminated=False, sessions_left=0, 1.01s
testbed/e2e_coordinator.py    -> PASSED=True, 6.10s, snapshot written
scripts/check_glm.py          -> PASS (real API, 1 tool call, 413 in / 18 out)
real http-easy (GLM-5.1)      -> FLAG FOUND flag{mock_http_1337}, 20 API calls, 176.9s
real tcp-menu (GLM-5.1)       -> FLAG FOUND flag{mock_tcp_4242}, 6 API calls, 27.8s
real coordinator (GLM-5.1)    -> PASSED=True, 464.55s, 479,113 tokens,
                                 both flags confirmed, snapshot written
MEA 2026 Qual (GLM-5.1)       -> 2/6 confirmed, 1801.11s, 4,876,494 tokens,
                                 passed=False, cost unknown
```

真实调用的 token 用量来自 harness 记录；由于未配置 GLM-5.1 单价，成本栏显示 `$0.00`，
**不代表免费，实际费用未知**。HTTP/TCP 真实运行的 flag 均经 mock CTFd 返回 `correct`
确认（两条 trace 中各含 1 个 `CORRECT —` 提交结果），不是模型自报。真实 coordinator
测试的报告在 `runtime/e2e_coordinator_real_report.json`：`passed=True`、`elapsed_s=464.55`、
`total_tokens=479113`、`snapshot_ok=true`；报告中的 `$0.0` 同样是“未配置单价”的结果。trace 文件：
`logs/trace-http-easy-GLM-5.1-20261027-081818.jsonl`、
`logs/trace-tcp-menu-GLM-5.1-20261027-082133.jsonl`（`logs/` 已在 `.gitignore`）。
`pytest` 跳过项是与真实 provider SDK 相关的可选检查。

### MEA 2026 Qual 本地复现（2026-10-27）

从 WSL 路径 `\\wsl.localhost\Ubuntu\home\ubuntu\ctf\mea-2026-qual` 复制并净化出
`testbed/mea-2026-qual/`，使用本地 mock CTFd、本地交互服务和真实 GLM-5.1 coordinator。
原题共 7 道，`hokan` 依赖的 SageMath 服务在本机不可用，默认排除，因此统计 6 道。

结果（报告见 `testbed/mea-2026-qual/runtime/mea_run_report_full_6ch.json`）：

- `Evidence`、`whisper_evidence` 经 mock CTFd `correct` 确认，**2/6**。
- `baiby-pwn`、`popcnt_oracle`、`reduce-reuse-recycle`、`teto` 未解出。
- 耗时 **1801.11s**，token **4,876,494**，`passed=false`，`total_cost_usd=0.0`
  且 `pricing_configured=false`，**费用未知**。
- 更早一次运行因 DinD 挂载路径为空得到 0/6（1,800.95s、4,030,579 tokens），报告保留为
  `runtime/mea_run_report_broken_mounts.json`；修复 `configure_path_mapping()` 后重跑。

已知数据不一致：`whisper_evidence` 的 mock 状态与状态快照 `solved` 都包含它，但报告的
`flags`/`results` 只有 `Evidence`。对应 trace 中正确 flag 已出现，但没有 `CORRECT` 工具
结果；确认发生在 solver 结果记录之外的路径（coordinator 手动提交，或 poller 自动 kill
与 solver 返回的竞态）。已让 `do_submit_flag` 在 `correct` 时记录结果，并在
`_run_and_cleanup`、自动 kill 和任务完成处补记 `swarm.confirmed_flag`；回归测试
`test_coordinator_records_only_correct_manual_submissions` 已通过，**但该修复尚未用真实
模型重跑 `whisper_evidence` 验证**。因此这里仍按报告的 2/6 陈述，不宣称 2 个 flag 都已
完整落盘。

四道未解题的原题使用动态 flag，本地为其生成占位 flag；解出与否由 mock 精确匹配判定。
`teto` 的 trace 中曾达到 width 破坏、canary/PIE/栈泄漏并开始构造 ROP，但截止时间到达前
未完成。`baiby-pwn` 的 solver 多次忽略已读取的源码，转向 GOT/PLT/ret2dlresolve 探索，
未形成可用利用。`popcnt_oracle` 和 `reduce-reuse-recycle` 出现模型请求超时和错误的
位/密钥恢复策略。以上均为实际观察，不代表这些题不可解。

## 7. 已知限制 / 未完成

- 真实 GLM-5.1 验证了最小工具调用、两道本地简单题、一次真实 coordinator 无人值守
  并发解这两题，以及 MEA 2026 Qual 本地复现的 2/6；其余 4 道 pwn/crypto/交互题未解出，
  复杂 web/pwn/crypto、长上下文、真实 provider 断线重连仍未充分验证。
- 本次真实运行未配置 GLM-5.1 单价，成本显示 `$0.00`，实际费用未知。配置
  `MODEL_INPUT_PRICE_PER_MTOK` / `MODEL_OUTPUT_PRICE_PER_MTOK` 后成本预算才会生效
  （尝试次数预算仍然有效）。
- `MAX_CONCURRENCY` 与 `API_RETRY_DELAY` 尚未单独接线（见上表说明）。
- 沙箱镜像为最小版（Python 3.13，含 pwntools/z3/sympy 等）；不含 SageMath/angr/GDB/r2。
  需要高级 pwn/rev 时改用完整 `sandbox/Dockerfile.sandbox`（镜像更大）。
- `backend/platform/` 提供平台适配抽象与 CTFd 适配器（`tests/test_platform_adapter.py`
  验证字段映射与错误状态），但运行路径仍走原有 `CTFdClient`；未实现任何未公开的平台 API。
  出题接口 `ChallengeCreator` 仅为抽象占位，等比赛规则明确。
- `Settings` 的 `repr()`/`str()` 已对密钥字段打码，避免调试日志意外泄露；真实调用日志与
  trace 中未出现 Key。
- 未做真实 CTFd 实例联调（仅 mock CTFd）；CTFd 字段/端点遵循公开 API。
- 未实现经验库、多模型竞速扩展、自动出题。

## 8. 回退基线与关键文件

项目目录不含 `.git`。改造过程中未先建立版本库；2026-10-27 15:56 建立了当前源码快照
（不含 `.env`、`runtime/`、`logs/`）：`D:\code\python\agent\backups\ctf-agent-baseline-20261027.tar.gz`。
它是**改动后**的状态快照，不是改造前的上游基线；若要回到上游版本，需要重新获取原仓库。


新增：`backend/tools/interactive.py`、`backend/interactive/`、`backend/platform/`、
`backend/state.py`、`scripts/check_glm.py`、`scripts/run-agent.ps1`、
`sandbox/Dockerfile.minimal`、`testbed/`、`tests/`。

修改：`backend/config.py`、`backend/models.py`、`backend/agents/{solver,swarm,pydantic_coordinator,coordinator_loop,coordinator_core}.py`、
`backend/{cost_tracker,ctfd,deps,poller,prompts,solver_base,cli}.py`、`.env.example`、`README.md`。

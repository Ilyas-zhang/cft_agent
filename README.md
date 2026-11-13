# CTF Agent — GLM-5.1 Fork

> **TPCTF 14 日 / 15 日入口：**使用 `python -m backend.tpctf`，部署、私有配置与验收步骤见
> [docs/TPCTF_P0.md](docs/TPCTF_P0.md)。下文原有 `ctf-solve` / PowerShell 示例用于 CTFd，不能仅改 URL 后接 CTFy。

# ***别动键鼠，正在远程！！！***

> 本仓库是 [`verialabs/ctf-agent`](https://github.com/verialabs/ctf-agent) 的 GLM-5.1 改造版：
> 通过 **Anthropic Messages 兼容接口** 接入 GLM-5.1，主控制器运行在 Python 3.14 容器中，
> 每道题使用独立 Docker 沙箱，并补充了在线交互、反伪造 flag、预算/状态持久化与本地测试靶场。
> 详细配置、验收数据与限制见 [docs/GLM_INTEGRATION.md](docs/GLM_INTEGRATION.md)。

## 这是什么

一个面向 CTF 比赛的自主求解 Agent：

- **Coordinator** 轮询 CTFd，发现未解题目后为每题启动一个 **Swarm**。
- 每个 Swarm 运行一个或多个 Solver；当前默认使用单个 **GLM-5.1**。
- Solver 在隔离的 Docker 沙箱中执行命令、访问 HTTP/TCP 服务、分析附件，并提交 flag。
- 只有 CTFd 返回 `correct` 才记为成功；模型自报的 flag 不会被当作已确认结果。

> 上游项目曾在 BSidesSF 2026 CTF 中取得 52/52、第一名。下文表格中的“真实 GLM-5.1”
> 结果来自本仓库的本地靶场测试，与上游成绩是两回事。

## 架构

```
                    +----------------------+
                    |  CTFd / mock CTFd   |
                    +----------+-----------+
                               |
                      Poller (5s interval)
                               |
                    +----------v-----------+
                    | Coordinator (GLM-5.1)|
                    |  spawn/kill swarms   |
                    +----------+-----------+
                               |
          +--------------------+--------------------+
          |                    |                    |
   +------v------+      +------v------+      +------v------+
   | Swarm       |      | Swarm       |      | Swarm       |
   | challenge-1 |      | challenge-2 |      | challenge-N |
   | GLM-5.1     |      | GLM-5.1     |      | GLM-5.1     |
   +------+------+      +------+------+      +------+------+
          |                    |                    |
   +------v------+      +------v------+      +------v------+
   | Docker      |      | Docker      |      | Docker      |
   | sandbox     |      | sandbox     |      | sandbox     |
   | ctf-sandbox |      | ctf-sandbox |      | ctf-sandbox |
   +-------------+      +-------------+      +-------------+
```

- 主控制器镜像：`ctf-agent:latest`，Python 3.14，负责调度与模型调用。
- 沙箱镜像：`ctf-sandbox`，Python 3.13（pwntools 的 unicorn 依赖尚无 3.14 wheel），
  预装 pwntools、z3、sympy、gmpy2、pycryptodome、requests、flask 等。
- Agent 容器通过挂载宿主 `/var/run/docker.sock` 创建兄弟沙箱容器；
  `WORKSPACE_ROOT` + `SANDBOX_BIND_ROOT` 负责容器路径到宿主路径的映射。

## 已实际验证的能力

以下结果均为实际运行，不是预期值。真实 GLM-5.1 调用使用 Anthropic Messages 兼容端点。

| 项目 | 结果 |
|---|---|
| 最小请求 + 工具调用（`scripts/check_glm.py`） | 通过；调用 `add`，输出 `42`，413 in / 18 out |
| 本地 HTTP 题 `http-easy`（真实 GLM-5.1） | `flag{mock_http_1337}`，20 次 API 调用，176.9s，133.4k in（116.0k cached）/ 2.8k out |
| 本地 TCP 多轮题 `tcp-menu`（真实 GLM-5.1） | `flag{mock_tcp_4242}`，6 次 API 调用，27.8s，19.6k in（18.3k cached）/ 182 out |
| 单元测试 | 52 passed, 2 skipped |
| 本地 E2E（脚本化模型） | `e2e_local.py` 6/6；并发隔离 PASS（1.01s）；coordinator PASS（6.10s） |
| **真实 coordinator 无人值守（GLM-5.1）** | **PASSED**；自动发现并并发解出 `http-easy` + `tcp-menu`，464.6s，479.1k tokens（420.7k cached，90% 命中），两题均经 mock CTFd `correct` 确认，状态快照已写入 |
| **MEA 2026 Qual 本地复现靶场（GLM-5.1）** | **2/6**；`Evidence`、`whisper_evidence` 经 mock CTFd `correct` 确认；`baiby-pwn`、`popcnt_oracle`、`reduce-reuse-recycle`、`teto` 未解出；`hokan` 因本机无 SageMath 排除。1801.1s，4.876M tokens，`PASSED=False`，费用未知 |

HTTP/TCP 两道题的 flag 均由 mock CTFd 返回 `correct` 确认，trace 中可见 `CORRECT —` 提交结果。
真实 coordinator 无人值守测试额外确认了：它能在同一进程内自动拉起两个 swarm、并发解出两题，
并在结束时写出 `testbed/runtime/coordinator_state.json`。这次 coordinator 运行使用了 479.1k
tokens；由于未配置单价，成本栏仍为 `$0.00`，**实际费用未知**。
**费用说明**：当前未配置 GLM-5.1 单价，成本栏显示 `$0.00`，这不代表免费，实际费用未知。
填写 `MODEL_*_PRICE_PER_MTOK` 后，基于成本的预算限制才会生效。

### MEA 2026 Qual 本地复现结果

从 `\\wsl.localhost\Ubuntu\home\ubuntu\ctf\mea-2026-qual` 复制并净化出
`testbed/mea-2026-qual/` 本地靶场，未连接任何外部比赛平台。真实 GLM-5.1 coordinator
在 6 道可运行题上跑了一次 1800s 无人值守测试：

- **实际确认 2/6**：`Evidence`、`whisper_evidence` 经 mock CTFd `correct` 确认。
- **未解出 4 题**：`baiby-pwn`、`popcnt_oracle`、`reduce-reuse-recycle`、`teto`。
- `hokan` 因本机没有 SageMath 服务被排除，不计入这 6 题。
- 耗时 **1801.11s**，**4,876,494 tokens**，`passed=false`；单价未配置，费用未知。
- 报告只记录到 `Evidence` 的 flag。`whisper_evidence` 的 mock 状态显示已解，但报告快照
  缺少其 flag；trace 中正确 flag 已出现，却没有对应的 `CORRECT` 工具结果，说明确认走了
  solver 结果记录之外的路径（coordinator 手动提交或 poller 自动 kill 与 solver 返回的
  竞态）。已修复这两条记录路径并添加回归测试，但**尚未用真实模型重跑验证**。
- 更早一次运行因 DinD 挂载路径为空，0/6；修复路径映射后才有上述结果。该次报告保留为
  `runtime/mea_run_report_broken_mounts.json`，不与本次结果混算。

## 快速开始

### 1. 前置条件

- Windows + Docker Desktop（WSL2 后端），确保 Docker 可用。
- PowerShell（宿主机操作）。
- GLM-5.1 的 Anthropic Messages 兼容 `base URL` 和 API Key。
- 不需要 Windows 本机 Python；所有 Python 代码在容器中运行。

### 2. 配置凭据

```powershell
# 尚无 .env 时复制模板；已有 .env 则直接编辑，不要覆盖
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 用编辑器填写：
#   ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
#   ANTHROPIC_API_KEY=<你的 Key>
#   MODEL_NAME=GLM-5.1
```

- `.env` 已在 `.gitignore` 中，**不要提交、不要粘贴到聊天或日志**。
- `ANTHROPIC_BASE_URL` 是 SDK 自动拼接 `/v1/messages` 的根地址，不要写 `/v1/messages` 后缀。
- 如果账号提供的模型 ID 不是 `GLM-5.1`，设置 `MODEL_NAME` 为准确值。
- Claude Code 自身常用 `ANTHROPIC_AUTH_TOKEN`，本项目读取的是 `ANTHROPIC_API_KEY`，两者不要混用。

### 3. 构建镜像并跑一道本地题

```powershell
.\scripts\run-agent.ps1 -BuildAgent -BuildSandbox `
  -Challenge /work/testbed/challenges/http-easy
```

首次会构建 `ctf-agent:latest` 与 `ctf-sandbox`，然后启动容器解 `http-easy`。
脚本只挂载项目目录与 Docker socket，不会把 Key 放到命令行。

单独做模型连通性检查：

```powershell
docker run --rm -v "${PWD}:/work" -w /work `
  --entrypoint python ctf-agent:latest scripts/check_glm.py
```

该脚本发起 1 次真实请求 + 1 次工具调用，输出模型、token 用量与预估费用；
未配置 Key 时退出码为 2。

### 4. 启动完整 Coordinator

```powershell
# 本地测试靶场：先启动 mock CTFd / HTTP / TCP 服务（另一个终端）
docker run -d --rm --name ctf-services `
  -p 8000:8000 -p 8081:8081 -p 9900:9900 `
  -v "${PWD}/testbed:/testbed" -w /testbed `
  python:3.14-slim python run_services.py

# 在 .env 中设置本地 CTFd（或通过环境变量传入）
#   CTFD_URL=http://host.docker.internal:8000
#   CTFD_TOKEN=test-token

# 启动 coordinator，自动发现并并发解未解题
.\scripts\run-agent.ps1 -ExtraArgs "--challenges-dir", "/work/testbed/challenges"
```

真实比赛时把 `CTFD_URL` / `CTFD_TOKEN` 换成授权比赛平台，并将 `--challenges-dir`
指向已拉取的题目目录。**只对明确授权的比赛目标或本地靶场发起网络操作。**

### 5. 常用参数

```powershell
# 单题、显式模型
.\scripts\run-agent.ps1 -Challenge /work/testbed/challenges/tcp-menu `
  -Models "anthropic/GLM-5.1"

# 只求解、不提交（dry run）
.\scripts\run-agent.ps1 -NoSubmit -Challenge /work/testbed/challenges/http-easy

# 只构建，不运行（直接用 docker）
docker build -f Dockerfile.agent -t ctf-agent .
docker build -f sandbox/Dockerfile.minimal -t ctf-sandbox .
```

## 关键配置

| 变量 | 说明 | 默认 |
|---|---|---|
| `ANTHROPIC_BASE_URL` | Anthropic Messages 兼容接口根地址 | 空 |
| `ANTHROPIC_API_KEY` | API Key（只放 `.env`/环境变量） | 空 |
| `MODEL_NAME` | 模型 ID | `GLM-5.1` |
| `MODEL_TIMEOUT` | 单次模型请求超时（秒） | 300 |
| `MAX_OUTPUT_TOKENS` | 单次最大输出 token | 128000 |
| `MAX_RETRIES` | 模型请求重试次数 | 3 |
| `MAX_COST_PER_CHALLENGE` | 单题费用上限（USD） | 10 |
| `MAX_COST_TOTAL` | 全局费用上限（USD） | 500 |
| `MAX_ATTEMPTS_PER_CHALLENGE` | 单题最大求解尝试次数 | 10 |
| `MODEL_*_PRICE_PER_MTOK` | 自定义单价（USD/1M tokens） | 0 |
| `SANDBOX_IMAGE` | 沙箱镜像 | `ctf-sandbox` |
| `MAX_CONCURRENT_CHALLENGES` | 并发题目/容器上限 | 10 |
| `CONTAINER_MEMORY_LIMIT` | 单沙箱内存限制 | 16g |
| `CONTAINER_CPUS` | 单沙箱 CPU 限制 | 2.0 |
| `WORKSPACE_ROOT` | Agent 容器内项目根（DinD） | 空 |
| `SANDBOX_BIND_ROOT` | 宿主可见的项目根（DinD） | 空 |

注意：单价全部为 0 时，`genai-prices` 也无法识别 GLM-5.1，成本记为 `$0`，
费用预算不会触发；尝试次数预算仍然有效。

## 在线交互工具

每个 Solver 除 `bash` 外还拥有 13 个有状态工具（`backend/tools/interactive.py`）：

- HTTP：`http_request`（cookie/重定向/JSON/表单/原始 body）、`http_diff`、
  `http_history`、`http_close`。
- TCP：`tcp_connect`、`tcp_send_recv`、`tcp_send`、`tcp_recv`、`tcp_recv_until`、
  `tcp_send_bytes`、`tcp_interactive`（多步菜单）、`tcp_disconnect`、`tcp_status`。

会话按 `题目:模型:Solver 实例` 隔离；并发题目不会共享 cookie 或 socket，
Solver 停止时会清理自己的会话。连接失败、超时以工具输出返回，不会让整个 Agent 崩溃。

## 可靠性与安全

- **反伪造 flag**：只有 CTFd `correct` 才算确认；`already_solved` 只表示该题此前已解，
  不证明本次提交的 flag 正确。Solver 的最终结果只采用 `confirmed_flag`，
  `INCORRECT` 不会因包含 `CORRECT` 子串而被误判。
- **不自动切换付费模型**：`QUOTA_FALLBACK = {}`，不会在未授权时调用其他模型。
- **预算与尝试上限**：单题/全局费用、单题最大尝试次数；达到全局预算会停止所有 swarm。
- **状态持久化**：`runtime/coordinator_state.json` 原子写入，重启后恢复结果、已解集合与累计费用。
- **并发隔离**：每个 Solver 独立沙箱、独立 HTTP/TCP 会话作用域；并发 E2E 验证无交叉污染。
- **优雅退出**：`CancelledError` 会向上传播，避免取消后进程挂死。
- **密钥保护**：`.env` 不入库；`Settings` 的 `repr()`/`str()` 对密钥打码；
  日志不打印 Key；`scripts/check_glm.py` 对异常信息脱敏。
- **授权范围**：网络探测/利用仅限明确授权的比赛目标或本地靶场。

## 测试

```powershell
# 单元测试（agent 镜像内）
docker run --rm -v "${PWD}:/work" -w /work `
  --entrypoint python ctf-agent:latest -m pytest tests/ -q
```

本地 E2E 需要 mock 服务、Docker socket 与 DinD 路径映射，完整命令与数据见
[docs/GLM_INTEGRATION.md](docs/GLM_INTEGRATION.md)。测试脚本：

- `testbed/e2e_local.py`：HTTP/TCP、有状态工具、两类伪造 flag 场景。
- `testbed/e2e_concurrent.py`：两题并发、会话隔离、无残留。
- `testbed/e2e_coordinator.py`：脚本化 coordinator 事件循环与状态快照。
- `testbed/e2e_coordinator_real.py`：真实 GLM-5.1 coordinator（需 `.env` 凭据）。
- `testbed/mock_ctfd.py`、`testbed/http_challenge.py`、`testbed/tcp_challenge.py`：本地靶场。
- `testbed/mea-2026-qual/`：从 WSL 复制的 MEA 2026 Qual 本地复现靶场
  （mock CTFd + 本地交互服务 + 真实模型 harness）；启动方式、flag 说明和限制见该目录
  [README](testbed/mea-2026-qual/README.md)。

## 目录结构

```
backend/                主控制器：调度、Solver、工具、CTFd、预算、状态
  agents/               coordinator / swarm / solver
  interactive/          HTTP/TCP 持久会话
  platform/             平台适配抽象（CTFd 参考实现）
  tools/                bash、提交 flag、交互工具
sandbox/                Dockerfile.minimal / 完整沙箱 Dockerfile
scripts/                run-agent.ps1、check_glm.py
testbed/                mock CTFd、HTTP/TCP 靶场、E2E
tests/                  单元/回归测试
docs/GLM_INTEGRATION.md 配置、验收数据、限制与回退说明
runtime/                运行期状态（gitignored）
logs/                   trace 日志（gitignored）
```

## 已知限制

- 真实 GLM-5.1 目前验证了最小工具调用、两道本地简单题（HTTP 单步、TCP 菜单）、
  一次真实 coordinator 无人值守并发解这两题，以及 MEA 2026 Qual 本地复现中的 2/6；
  其余 4 道 pwn/crypto/交互题未解出，长上下文与真实 provider 断线重连仍未验证。
- `MAX_ATTEMPTS_PER_CHALLENGE` 限制的是 swarm 自己的自动 bump；coordinator 通过
  `bump_agent` 主动下发的策略提示不增加该计数（这是本次真实 coordinator 能持续纠偏的原因）。
  若要让主动 bump 也计入上限，需要额外接线。
- 最小沙箱不含 SageMath/angr/GDB/radare2；高级 pwn/rev 请改用完整
  `sandbox/Dockerfile.sandbox`（镜像更大）。
- 未配置单价时实际费用未知；正式长时间运行前建议填写单价。
- `backend/platform/` 已提供适配接口与 CTFd 适配器，但运行路径仍使用原有 `CTFdClient`；
  未实现任何未公开的平台 API。`ChallengeCreator` 仅为出题接口占位。
- 未实现经验库、多模型竞速扩展、自动出题。
- 本地 CTFd 联调仅使用 mock 实现；真实平台字段/端点以公开 API 为准。

## 上游与致谢

- 上游项目：[`verialabs/ctf-agent`](https://github.com/verialabs/ctf-agent)，
  由 [Veria Labs](https://verialabs.com) 构建；上游在 BSidesSF 2026 CTF 取得 52/52、第一名。
- CTFd 交互与 HTML 辅助代码参考 [`es3n1n/Eruditus`](https://github.com/es3n1n/Eruditus)。
- 本仓库的 GLM-5.1 改造、测试结果与限制以本文件及 `docs/GLM_INTEGRATION.md` 为准。

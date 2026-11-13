# P0 本地验收记录（2026-11-13）

基线：Ilyas-zhang/cft_agent `7f3796aa6204224e55312c775bb4c913ae78b4a7`。
实现分支：`fix/tpctf-p0`。仅处理入围与初选的解题准备，未实现自动出题。

## 已执行并通过

| 验证 | 结果 |
|---|---|
| Python 3.14，按 uv.lock 安装项目与开发依赖 | 通过 |
| `python -m pytest tests -q` | 18 passed |
| 新增及修改的 Python 文件 Ruff 检查 | 通过 |
| `git diff --check` | 通过 |
| 主镜像 `ctf-agent:tpctf-p0` 实际构建 | 通过 |
| 沙箱 `ctf-sandbox:tpctf-p0` 按固定依赖实际构建 | 通过 |
| 生产镜像中的 `ctf-tpctf --help` | 通过 |
| Linux 启动脚本 `bash -n` | 通过 |
| Agent HTTP/TCP 工具连接本地真实模拟服务 | 通过 |
| 实际 DockerSandbox 的 Linux host 网络连接 | 通过 |
| 实际沙箱 HTTP 请求、实例附件只读挂载 | 通过 |
| 沙箱 pwn/Crypto/sympy/gmpy2/requests/z3 导入 | 通过 |

测试使用模拟平台，不访问真实比赛目标，不调用付费模型。
其中一项集成测试使用真实 Solver 与 Pydantic AI 工具调用流程，模型响应由 FunctionModel 脚本化生成。
覆盖两问依赖解锁、动态实例标识提交、错误次数、提交超时重试、多选数组、TLS/SNI、实例重启恢复、
取消后的释放、队伍并发限制和 VPN 路由冲突检测。

本机生成的原始记录：`runtime/p0-tests.xml`、`runtime/p0-docker-smoke.json`（不提交 Git）。
镜像为本机 Docker Desktop Linux engine 构建；这不等于已在主办方 VPS 部署。

## 仍需官方环境验收

1. 主办方 VPS 的登录方式、系统权限、Docker/WireGuard 可用性和实际资源上限。
2. 本队 agent_play 令牌、赛道编号的实际可见性与作用域。
3. 官方实例的实际 VPN 配置及目标地址；多实例 VPN 是否路由冲突。
4. 14 日自有模型调用，以及 15 日平台网关会话的真实协议、模型 ID 和工具调用。
5. 官方模拟题中正确且计分的提交回执，以及题目完成后的名额回收。

`--check` 只检查本地配置和依赖，不代替上述检查。
默认并发 1；路由重叠时拒绝并行，尚未实现每实例独立网络命名空间。
模型请求数/Token 和时间限制按单次 Solver 运行实施；赛事总量 Token 跨进程硬预算属于后续 P1。
原仓库宣称的旧 tests/testbed 未提交，因此本次不能重跑其自述的 52 项历史测试。

部署步骤见 [TPCTF_P0.md](TPCTF_P0.md)。

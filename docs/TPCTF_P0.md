# TPCTF 14 日入围 / 15 日初选运行说明

本实现依据 2026-11-13 发布的参赛接口文档 v1.0，以及同日取得的官方 OpenAPI。
新入口 `ctf-tpctf` / `python -m backend.tpctf` 专门对接 CTFy；`ctf-solve` 仍用于原有 CTFd。
运行分支 `fix/tpctf-p0`；上游基线为 `7f3796aa6204224e55312c775bb4c913ae78b4a7`。

## 当前验收边界

离线测试覆盖按问题提交、刷新解锁、实例恢复/释放、并发限制、网络检查与模型配置。
没有官方账号、VPS、网关会话时，这些测试不能证明正式平台得分，也不能证明 WireGuard 连通。
原仓库自述的 52 项测试与 testbed 没有随仓库提交；这里新增的 tests 不冒充那些测试。

## 在 Linux 上准备（必须在封网前完成）

初选使用主办方分配的 VPS。14 日也可先在自己可联网的 Linux 环境调试。
推荐在 Linux 宿主运行 Python Agent，由它创建 Docker 沙箱；宿主建立 WireGuard。
不要在 Windows PowerShell 直接运行本赛事入口；PowerShell 启动脚本是原有本地 CTFd 流程。

1. 安装 Docker、Python 3.14 / uv、WireGuard 的 `wg` / `wg-quick`、`iproute2`。
   VPN 配置若含 DNS，还需提供系统适用的 `resolvconf`。确认 VPS 允许 Docker、WireGuard 和 UDP。
2. 在项目目录执行 `uv sync --frozen`，使用已提交的 uv.lock。原有 Claude SDK 路径需额外 `--extra legacy`。
3. 构建并缓存沙箱：

```bash
docker build -f sandbox/Dockerfile.minimal -t ctf-sandbox:tpctf-p0 .
# 主控制器镜像可用于检查或容器部署；推荐的宿主 Python 运行不依赖它。
docker build -f Dockerfile.agent -t ctf-agent:tpctf-p0 .
```

预装镜像、依赖和工具后再封网。比赛运行期间不自动安装软件或拉取缺失镜像。
最小沙箱没有 SageMath/angr/GDB 等高级工具；按实际资源预算预先构建完整镜像，勿在正式任务中临时安装。

## 配置私有凭据

将 `.env.tpctf.example` 复制为 `/root/tpctf.env`，`chmod 600 /root/tpctf.env` 后填写。
可用 `TPCTF_ENV_FILE` 指向其他私有路径。不要把真实配置放入 Git、题目目录或附件。

- `CTFY_TOKEN`：网页“AI 智能体——参赛”模板的 `pf_` 令牌，限定本队赛道。
- `CTFY_COMPETITION_ID`：本队赛道 id。可先填写平台令牌，再用下面的列比赛命令获取。
- 14 日 `TPCTF_STAGE=qualifier`：使用队伍自有模型 API Key 和完整端点。
- 15 日 `TPCTF_STAGE=preliminary`：先在平台“模型连接”配置厂商 Key，再创建“本地 Agent 会话”；
  将平台生成的 BASE_URL 和 `pk_` 会话凭据写入文件，并把其准确主机名填入 `TPCTF_GATEWAY_HOST`。
  使用完整原值，不自行加路径后缀。不把厂商原始 Key 留在 VPS 的正式启动配置中。
- OpenAI 兼容协议设 `TPCTF_MODEL_PROTOCOL=openai`，配置 `OPENAI_BASE_URL/OPENAI_API_KEY`；不要混用两套凭据。
- `MODEL_NAME` 必须填所选模型的准确 ID。新入口按它选型，不使用原有固定 DEFAULT_MODELS。

## 检查与启动

```bash
# 只读取比赛列表，不启动靶机、不调用模型。
bash scripts/run-tpctf.sh --list-competitions

# 检查本地配置、权限、Docker daemon 与已缓存沙箱镜像。
bash scripts/run-tpctf.sh --check

# 14 日先选一道官方模拟题，完成一轮并退出。
bash scripts/run-tpctf.sh --stage qualifier --once --challenge ACTUAL_CHALLENGE_ID

# 稳定后，连续轮询并自主解题；Ctrl+C 会清理本进程拥有的实例。
bash scripts/run-tpctf.sh --stage qualifier

# 15 日先切换私有配置到官方网关，检查后再运行。
bash scripts/run-tpctf.sh --stage preliminary --check
bash scripts/run-tpctf.sh --stage preliminary
```

默认自动选题只处理计分题；显式指定 `--challenge` 时也可运行练习题，但其回执 `ranked=false` 不能算比赛得分。

自动 WireGuard 模式需要宿主 root；它只调用固定的 wg-quick 命令，不执行模型生成的宿主命令。
如果由运维预先管理 VPN，可设 `TPCTF_VPN_MODE=external`；此时 Agent 不下载、不变更 VPN，
但仍会检查 Agent 和沙箱到目标的连接。每个新实例仍需相应的连接，不能只连第一个实例就忽略后续实例。

## 网络与调度

- 两种工具都要连通：Python HTTP/TCP 会话在 Agent 进程；bash 在 Docker 沙箱。
  赛事沙箱使用 Linux `network=host`，因此复用宿主 VPN 路由。请勿把 WireGuard 只建在另一个隔离容器里。
- 自动模式获取本实例 `/vpn-config`，配置文件保存在 runtime 的私有目录，清理后删除。
  拒绝脚本钩子、默认互联网路由；同时运行的实例路由重叠时拒绝第二条连接。
  默认并发 1；只有实际验证路由不冲突后再增加。网络命名空间并行隔离未实现。
- 入围实例上限 1，初选上限 3，并额外服从 API 返回的 team effective 上限。
  同队其他成员已经占用的实例也计入容量。启动响应丢失或 409 会先查询已有实例。
- 单题有总期限，单问有期限与模型请求/Token 上限。先尝试热门已解题；失败后释放资源。
  连续模式按轮次重试失败题，`--once` 对当前题集各尝试一遍。
- 对每一问使用同一真实实例和沙箱，保留前问生成的工作文件；每次作答后重新读取问题与本队进度。
  `correct`、`ranked`、`challenge_fully_solved` 分开记录；不靠模型自报成功。
- `DELETE` 的 202 不视为立即释放，等待 stopped / 已回收；失败时保留 state.json 的归属记录。

## 证据与恢复

`runtime/tpctf/state.json` 保存本进程拥有的实例；重启验证赛道/队伍归属后恢复正在运行的实例。
请让一个运行进程独占一个 runtime 目录，勿同时启动两个进程使用同一目录。
题面、实例数据、本队实例附件、提交回执保存在 `runtime/tpctf/instances/`，Solver trace 保存在 logs/。
沙箱工作目录保留在本机临时目录（容器部署时必须配置 WORKSPACE_ROOT / SANDBOX_BIND_ROOT）。
这些材料可能含本队答案、目标凭据，均不可公开。正式赛段的人工介入仍受赛事规则限制。

## 最终现场验收

本地 `--check` 通过后，还必须在官方模拟题完成：启动实例 → 建立 VPN → Agent/沙箱均可连接 →
调用实际模型 → 官方正确且计分回执 → 全题完成 → 关闭靶机并释放名额。
第二次启动验证恢复；不要删除其他队员手工创建的实例。

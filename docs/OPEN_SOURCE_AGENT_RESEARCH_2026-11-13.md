# 开源 CTF Agent 选型与 playbook 研究（2026-11-13）

## 整体结论

这是一轮源码与公开证据调研，没有安装或运行候选框架，没有调用真实模型，也没有使用参赛令牌访问平台。
“更强”必须限定为相同模型、预算、工具、题目与提交规则下的有效解题率；以下资料不足以给出统一冠军排名。

对 14/15 日的建议：保留已完成本地验收的 CTFy/VPN/实例管理层，现有 Veria 派生 Solver 继续作为默认基线。
重点研究 sapper-v2 的协作机制；BoxPwnr 是优先对照候选；Strix 是 Web 专项候选；D-CIPHER 适合 SRT 的多智能体对照。
正式替换默认解题器应以同模型的小规模配对实测为依据，而不是以仓库星数或不同测试集的百分比为依据。

## 1. 现有项目与 Veria 的关系

Ilyas-zhang/cft_agent 的 README 明确称自己是 verialabs/ctf-agent 的 GLM 改造版。
进一步按 Git blob SHA 对照：用户远端基线 `7f3796aa6204224e55312c775bb4c913ae78b4a7` 与 Veria
`3366d569557c4fda3fd153040632de65e255396d` 有 36 个同路径文件，其中 19 个完全相同（含 3 个空初始化文件，非空相同文件为 16 个）。
包括 Claude/Codex Solver 与 Coordinator、消息总线、循环检测、工具封装和完整沙箱 Dockerfile。因此更准确的描述是基于上游源码的派生实现。
这组比较针对用户原始远端版本，不包含本地 P0 改造。
[用户 README](https://github.com/Ilyas-zhang/cft_agent/blob/7f3796aa6204224e55312c775bb4c913ae78b4a7/README.md)、
[用户文件树](https://api.github.com/repos/Ilyas-zhang/cft_agent/git/trees/7f3796aa6204224e55312c775bb4c913ae78b4a7?recursive=1)、
[上游文件树](https://api.github.com/repos/verialabs/ctf-agent/git/trees/3366d569557c4fda3fd153040632de65e255396d?recursive=1)

上游自报 BSidesSF 2026 52/52、第一名，默认以 Claude Opus 与 GPT/Codex 等多个模型竞速。
这不是单个 GLM Solver 的成绩，也不能预测 TPCTF 的解题率。上游 MIT LICENSE 署名 Veria Labs；用户原始文件树没有 LICENSE 文件，应在后续发布整理中保留上游许可与署名。
[上游 README](https://github.com/verialabs/ctf-agent/blob/3366d569557c4fda3fd153040632de65e255396d/README.md)、
[上游 LICENSE](https://github.com/verialabs/ctf-agent/blob/3366d569557c4fda3fd153040632de65e255396d/LICENSE)

## 2. 候选比较

| 候选 | 一手证据与长处 | 针对 TPCTF 的缺口 | 选型判断（研究推断） |
|---|---|---|---|
| [Veria ctf-agent](https://github.com/verialabs/ctf-agent) | 自报 BSidesSF 52/52；Coordinator/Swarm、隔离工具环境 | 上游竞速模型组合不同，原生平台为 CTFd | 本队已有改造基础，继续作为默认基线 |
| [sapper-v2](https://github.com/N1nEmAn/sapper-v2) | 近期国内竞赛方向；指挥/执行分工、事实图、上下文交接、watchdog；声明支持 GLM 与可替换 CLI | 强绑定腾讯赛场 API；默认分层模型/凭据轮换不能直接沿用；成绩口径不一致 | 最值得拆解的近期多智能体候选，不能直接认定胜过 Veria |
| [BoxPwnr](https://github.com/0ca/BoxPwnr) | 覆盖 Cybench、HTB、picoCTF、CTFd 等；公开逐步 trace；多种 solver/executor，包含 GLM 路径 | 无现成 CTFy 适配；GLM 分支硬编码海外 Coding API；存在公网模型元数据探测 | 优先做可控的同模型对照，需先改网关和离线行为 |
| [Strix](https://github.com/usestrix/strix) | Web 专项；公开 XBOW 脚本与结果；当前配置支持自定义模型端点 | 偏 Web 扫描，不等于全类别 CTF；缺 CTFy 计分、多问和 VPN 接入 | Web 专项备选，接入后必须重新测 GLM |
| [D-CIPHER / TATAR-LAB](https://github.com/TATAR-LAB/ctf-agents) | Planner–Executor、多 provider、工具发现，NYU 200 题配套研究 | 是学术基准工作流，非 TPCTF 即用比赛程序 | SRT 中期对照优先，赛前整套迁移收益未证实 |
| [CTFusion](https://github.com/kaist-hacking/CTFusion) | 2026 live CTF 评测框架，组合 D-CIPHER/Enigma 与 CTFd MCP | 主要解决评测组织，平台仍为 CTFd，部署组件增加 | 适合后续建立实测体系，不直接视为新强解题器 |
| [PentAGI](https://github.com/vxcontrol/pentagi) | 明确支持 GLM/自定义端点，角色体系与长记忆 | 通用渗透平台，Compose 含数据库等配套；需裁剪公网服务并另接比赛 | 两天窗口内不作为整套替换首选 |
| [PentestGPT](https://github.com/GreyDGL/PentestGPT) | 自主流水线支持 Claude Code/Codex，另有交互式多模型旧模式 | 多模型交互模式不能直接等同于无人值守 GLM 流水线 | 不因知名度就优先于现有实现 |
| [Shannon](https://github.com/KeygraphHQ/shannon) | 当前支持自定义 provider/模型/网关，偏有源码 Web 应用验证 | 白盒应用审计目标与黑盒/附件类 CTF 不同，无原生 CTFy 链路 | 不把旧白盒测试高分移用于本赛；可后续 Web 专项比较 |

CAI 与 Cyber-AutoAgent 也检查了：前者当前主分支为 archive，说明停止维护；后者 GitHub archived=true。
它们可以作历史参考，不能因旧榜单而称为近期持续维护的最佳选择。CAI 自有代码还具有研究用途许可条件，与 MIT 项目不同。
[CAI README](https://github.com/aliasrobotics/cai/blob/archive/README.md)、[CAI 元数据](https://api.github.com/repos/aliasrobotics/cai)、
[Cyber-AutoAgent](https://github.com/westonbrown/Cyber-AutoAgent)

### 2.1 sapper-v2：相关性高，接入成本也真实存在

检查版本 `7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb`。README 自报腾讯线上初赛冠军、634 队、85 flags、1B Token、约 8 小时无人值守，
其核心可借鉴点是事实/证据分层、任务交接、失败后止损、共享已验证方法，以及把“拿到答案”和“已提交计分”分开。
[固定 README](https://github.com/N1nEmAn/sapper-v2/blob/7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb/README.md)

但同版本 BATTLE-REPORT 的最终成绩单写的是 50 支已出分队第一、63 题中 36+ 题、约 45 面 Flag、约 110 元 API；
README 与战报给出的同一 80.18 分对应口径不同，本次没有找到可统一验证这些数字的组织方榜单或完整原始轨迹。
因此把战绩列为作者自报，不能把 1B/4.5B 直接当成已经验证的架构节省比例。
[固定战报](https://github.com/N1nEmAn/sapper-v2/blob/7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb/BATTLE-REPORT.md)

ENV 文档要求 BENCHMARK_BASE_URL / BENCHMARK_TOKEN，描述腾讯特定的 start/submit/close 流程；
给出厂商直连端点和可跨厂商轮换的 Key 池，README 的经济分工是 GLM 指挥/强攻、DeepSeek 执行。
这些都必须按 TPCTF 的 CTFy、实例/问题、网关和赛段模型约束改造，尤其不能原样启用厂商直连或后备模型轮换。
模型 CLI 与 base URL 可配置只能证明存在适配入口，不能证明官方 GLM 网关已可用。
[配置](https://github.com/N1nEmAn/sapper-v2/blob/7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb/docker/ENV.md)、
[架构契约](https://github.com/N1nEmAn/sapper-v2/blob/7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb/docker/commander/SPEC.md)

源码层面的额外发现：启动检查探测 `/openapi/v1/challenges`，ArenaClient 以 `unique_code` 和 `flag` 提交，
worker 的提示词还有腾讯接口直提后备路径；必须一并替换，不能只改环境变量。
当前 worker 默认模型按 `SAPPER_MODEL_DS → SAPPER_MODEL → glm-5.3[1m]` 取值，与文档描述的层级并非总一致。
Docker commander 又依赖 tmux 镜像及基础镜像，Claude CLI 版本也需锁定；换入本队会引入新的部署和会话管理面。
[启动检查](https://github.com/N1nEmAn/sapper-v2/blob/7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb/docker/commander/entrypoint.sh#L33)、
[ArenaClient](https://github.com/N1nEmAn/sapper-v2/blob/7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb/lib/arena.py#L178)、
[worker](https://github.com/N1nEmAn/sapper-v2/blob/7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb/docker/commander/worker.sh#L30)、
[构建入口](https://github.com/N1nEmAn/sapper-v2/blob/7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb/docker/Dockerfile.commander)

### 2.2 BoxPwnr：公开轨迹有价值，但总覆盖不是单次成功率

README 显示 Cybench 40/40，同时列 2165 条 traces；其榜单说明累计曲线统计跨模型的独立已解题目，按模型榜另列 pass@N。
所以不能把总覆盖当成一个 GLM、一次运行的 100% 成功率。
[README](https://github.com/0ca/BoxPwnr)、[榜单解释](https://boxpwnr.info/platform.html?platform=cybench)

源码中 `z-ai/` 分支读取 Z_AI_API_KEY 并固定使用 `https://api.z.ai/api/coding/paas/v4/`；
还存在 models.dev、OpenRouter 的模型元数据请求。这说明“支持 GLM”仍需进一步适配官方会话端点并禁用/本地化公网探测。
现有 CTFy 层可保留，先将其作为单题后端比较，而不是同时更换实例/VPN/提交层。
[LLM 源码](https://github.com/0ca/BoxPwnr/blob/main/src/boxpwnr/core/llm_manager.py)

### 2.3 Strix：证据限定在版本、模型与题型

官方 benchmark 仓库报告 Strix v0.4.0 + gemini-3-pro-preview 在 XBOW 黑盒 Web 测试 100/104，
并提供平均耗时、调用量和未解题列表。该数字不能直接换算成当前版本 + GLM 在 TPCTF 上的成功率，更不覆盖全部 Pwn/Crypto/Reverse。
当前源码支持 LLM_API_BASE / OPENAI_BASE_URL，但必须将所有模型路径都固定到赛事允许的网关；搜索、遥测等联网行为也要单独处理。
[官方 XBOW 结果](https://github.com/usestrix/benchmarks/tree/main/XBEN)、
[配置源码](https://github.com/usestrix/strix/blob/main/strix/config/settings.py)

### 2.4 研究证据如何改变行动顺序

2026 年 D-CIPHER 扩展研究对 NYU 200 题测试，报告在该实验中 Kali 工具环境相对 Ubuntu 提升 9.5 个百分点，
自动提示/类别提示并非总有收益。这是特定实验的结果，不是任意加工具的效果保证。
另一项 2026 年研究指出，比较复杂架构前应提供同模型的简单 coding-agent 基线，避免把底层模型进步记到架构名下。
[D-CIPHER 实验论文](https://arxiv.org/abs/2604.17159)、[同模型基线研究](https://arxiv.org/abs/2607.13085)

因此本队应先确认沙箱工具覆盖、交互能力、模型协议和真实提交链路，再做角色、记忆和检索的增量比较。
建议固定若干授权 Web/Pwn/附件题、同一可用模型、相同时间/Token 上限，记录有效得分、耗时、Token、失败原因，
分别比较现有 Solver、候选后端，以及“现有 Solver + 精简离线技能”。这种比较也可成为 SRT 的可复现实验。

## 3. 另一类值得关注的材料：ctf-skills

[ljagiello/ctf-skills](https://github.com/ljagiello/ctf-skills) 是 MIT 的分类技能与工具说明，当前包含 Web/Pwn/Crypto/Reverse 等材料，
比用户 playbook 更接近可按需加载的程序性技能库，但仍不是完整比赛调度器。
其 README 明确既有赛前预装方式，也有临场安装方式；另含用 Tavily 检索相似题解的 ctf-similar-search。
针对本赛只能审阅后选取离线技术材料，预装依赖并关闭在线题解检索，避免整库提示污染与封网失败。
[README 与技能列表](https://github.com/ljagiello/ctf-skills/blob/main/README.md)

## 4. 最新维护情况快照

下表仅记录 2026-11-13 查询时 GitHub API 的 pushed_at（UTC），不把它当作发布质量或性能证明。

| 项目 | pushed_at（UTC） | 许可/状态 |
|---|---|---|
| [Veria ctf-agent](https://api.github.com/repos/verialabs/ctf-agent) | 2026-03-29 06:17 | MIT |
| [sapper-v2](https://api.github.com/repos/N1nEmAn/sapper-v2) | 2026-11-11 12:18 | MIT |
| [BoxPwnr](https://api.github.com/repos/0ca/BoxPwnr) | 2026-07-24 13:54 | AGPL-3.0 |
| [Strix](https://api.github.com/repos/usestrix/strix) | 2026-11-12 19:11 | Apache-2.0 |
| [Shannon](https://api.github.com/repos/KeygraphHQ/shannon) | 2026-11-12 22:29 | AGPL-3.0 |
| [TATAR-LAB/ctf-agents](https://api.github.com/repos/TATAR-LAB/ctf-agents) | 2026-11-03 15:27 | MIT |
| [PentAGI](https://api.github.com/repos/vxcontrol/pentagi) | 2026-10-22 23:26 | MIT |
| [PentestGPT](https://api.github.com/repos/GreyDGL/PentestGPT) | 2026-07-14 12:58 | MIT |
| [ctf-skills](https://api.github.com/repos/ljagiello/ctf-skills) | 2026-11-12 05:36 | MIT |

来源为各项目 GitHub API 的 `/repos/{owner}/{repo}`；论文、源码版本与公开成绩另按正文具体来源判断。

---

# 附录：用户指定的 ctf-agent-playbook 逐项审查

研究日期：2026-11-13。本附录研究用户指定的 playbook；比赛条件沿用本次任务上下文，未访问比赛账号、未调用模型、未运行远程仓库代码。

## 结论与适用边界

**保留现有 verialabs/ctf-agent 的 GLM 改造版，把这个仓库当作流程与提示词素材，赛前不迁移框架。** 它自称“多个 AI 面板 + 人类指挥”的方法论：人负责分配目标、批准换题、提交 flag、搬运平台信息，多个面板靠共享 Markdown 和人工消息协作。它不是自主运行 Agent，也不是有检索器、工具封装、自动加载机制的技能包，更不是开源 Agent 资源清单。[README](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/README.md)、[协作体系](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/01-%E5%8D%8F%E4%BD%9C%E4%BD%93%E7%B3%BB.md)

固定检查版本为 `2473692e4a47dfec5c1a09570b30a363e8f7d864`。递归文件树只有 18 个文件：LICENSE、README、8 份 docs、8 份模板；没有 Python Agent、依赖清单、Dockerfile、模型适配器、调度器、自动提交实现或测试/评测代码。README 的“上手”就是建目录、复制模板、把角色提示词粘进面板。[完整文件树](https://api.github.com/repos/ab2836304651/ctf-agent-playbook/git/trees/2473692e4a47dfec5c1a09570b30a363e8f7d864?recursive=1)、[快速开始](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/00-%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B.md)

## 可在 14/15 日前吸收的内容

以下“接入方式”是针对当前 Python Solver + Docker + HTTP/TCP 工具及 CTFy 适配的研究建议，**不是已实现或已验证的改动**。

| 优先级 | 仓库素材与第一手来源 | 当前框架中的最小落点 |
|---|---|---|
| P0 | [纪律卡：三态、基线、证据标签](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/02-%E7%BA%AA%E5%BE%8B%E5%8D%A1.md)；[facts 模板](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/templates/bus/facts.md) | 压缩进 Solver 提示词：区分命中/明确失败/不确定；先确认目标的失败表现，保存状态码、长度和原始片段；结论附证据路径。HTTP 200 或模型自报不得代表得分，继续以平台确认结果为准。 |
| P0 | [行动卡](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/templates/bus/to_A.md) | 给每次实验记录“假设、原始依据、动作、成功/失败判据、成本、未验证前提”，下一步最多 3 条。无需另造副手 Agent 或 Markdown 总线。 |
| P0 | [容器配额表](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/templates/bus/state.md)；[复盘中的资源阻塞](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/06-%E5%AE%9E%E6%88%98%E5%A4%8D%E7%9B%98.md) | 将实例占用者、题号、到期时间、释放条件纳入已有状态。队伍额度按赛段的 1/3 配置；离线附件分析、哈希穷举优先放本地沙箱，申请活实例前确认确需交互。配额属于全队，不等于 Solver 并发数。 |
| P0 | [开赛检查单：题面与附件拿全](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/05-%E5%BC%80%E8%B5%9B%E6%A3%80%E6%9F%A5%E5%8D%95.md) | CTFy 入口对每题附件做清单核对；多问题保留题目与每个问项的对应关系、已提交答案和平台回执。后半句是当前比赛适配建议，原仓库没有多问实现。 |
| P1 | [已判死清单的记录形式](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/04-%E5%B7%B2%E5%88%A4%E6%AD%BB%E6%B8%85%E5%8D%95.md)；[进展板](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/templates/bus/progress.md) | 按当前题号与实例记录已试方法、证据和下一入口，恢复会话或换 Solver 时读回。沿用现有状态持久化，避免新建一套竞争状态源。 |
| P1 | [知识卡](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/templates/%E7%9F%A5%E8%AF%86%E5%8D%A1.md) | 自动保存真正跑通的命令、原始回显、答案提交结果与简短解题说明，便于复现和排查。 |

最小吸收顺序：先精简提示词中的证据规则，再完善实例占用/释放记录与附件清单；只有这些稳定后才考虑冷读复核。14 日前以现有链路的离线回归和完整演练为主；15 日切平台网关时优先验证模型协议、工具调用与提交闭环。**playbook 没有任何 GLM、平台网关、WireGuard 或封网部署能力可供移植。** 这是由其完整文件树和模板内容得出的判断。[文件树](https://api.github.com/repos/ab2836304651/ctf-agent-playbook/git/trees/2473692e4a47dfec5c1a09570b30a363e8f7d864?recursive=1)

## 比赛前不宜照搬

1. **三面板 + 人类总指挥 + 人工复制行动卡。** 其职责表要求人审批换题、提交 flag、搬运平台情报；这与本次自主运行条件不合。保留分析和执行的逻辑阶段即可，不应为复刻编制引入多 Agent 调度、共享文件锁和新平台侧脚本。[协作体系](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/01-%E5%8D%8F%E4%BD%9C%E4%BD%93%E7%B3%BB.md)、[平台侧提示词](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/templates/prompts/%E5%B9%B3%E5%8F%B0%E4%BE%A7.md)
2. **跨赛事的“已判死”结论。** 原文把特定实例的 2100 万弱密钥候选全负、RASP 拦截等结果写成速查表，甚至建议命中便跳过。有限字典穷尽不能证明任意目标没有弱密钥，旧实例机制也不能否定新题路线。应复用记录结构，给负结论加题目、版本、实例与实验范围。[已判死清单](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/04-%E5%B7%B2%E5%88%A4%E6%AD%BB%E6%B8%85%E5%8D%95.md)
3. **固定时间/请求数字与机制经验。** 15/30 分钟、最后 40 分钟、3–5 秒间隔、300 请求、PHP 载荷约 1200 字符等都来自作者环境；需要按本赛时长、工具延迟和题型配置。文中“500 表示路由存在”“HTTP 000 是本地问题”等也不是通用真理；应先排查传输/服务状态，再决定是否否定利用假设。[纪律卡](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/02-%E7%BA%AA%E5%BE%8B%E5%8D%A1.md)
4. **把离线推理叫作零成本。** 副手零靶场请求可减少目标负担，但仍消耗模型 token、墙钟时间和资源；正式赛模型限制与网关预算仍要统一计量。原提示词的“无限推演、成本为零”不能成为调度依据。[副手提示词](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/templates/prompts/%E5%89%AF%E6%89%8B.md)
5. **整份通用套路库塞入 system prompt。** 其内容偏应急响应、取证、Web、Java 内存分析，且掺杂旧赛条件；应只摘当前题型相关的小段。记录中提及 Arthas、jattach、爆破字典等，仓库本身没有打包这些工具，封网部署仍需独立预装与验收。[技术套路库](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/03-%E6%8A%80%E6%9C%AF%E5%A5%97%E8%B7%AF%E5%BA%93.md)
6. **旧比赛规则。** 录屏、电话抽查、赛后两小时 WP、自动抢位器的 45 秒轮询不是 TPCTF 规则来源，也没有配套实现；不得当作当前赛事要求。[开赛检查单](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/05-%E5%BC%80%E8%B5%9B%E6%A3%80%E6%9F%A5%E5%8D%95.md)

## 许可、维护和效果证据

- **许可：** README 与 LICENSE 声明 CC BY 4.0；LICENSE 是摘要，并链接完整法律文本。直接复制/改写模板时保留作者、原仓库与许可链接，并标明修改。GitHub API 的自动识别字段为 `Other / NOASSERTION`，不能据此误称无许可。未发现单独的软件许可证或第三方代码授权清单。[LICENSE](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/LICENSE)、[仓库元数据](https://api.github.com/repos/ab2836304651/ctf-agent-playbook)、[CC 官方完整文本](https://creativecommons.org/licenses/by/4.0/legalcode)
- **维护：** API 显示创建于 2026-09-13；主分支仅两个提交，均在当天。最新 commit 时间 2026-09-13 15:52:16 UTC（北京时间 23:52:16）；`pushed_at` 为 15:52:41 UTC。仓库 `updated_at` 为 2026-09-14 02:59:14 UTC，不能当成代码更新时间。截至本次查询未归档、无 GitHub release；没有持续迭代的证据。[提交记录](https://api.github.com/repos/ab2836304651/ctf-agent-playbook/commits?per_page=5)、[最新提交](https://github.com/ab2836304651/ctf-agent-playbook/commit/2473692e4a47dfec5c1a09570b30a363e8f7d864)、[元数据](https://api.github.com/repos/ab2836304651/ctf-agent-playbook)、[releases](https://api.github.com/repos/ab2836304651/ctf-agent-playbook/releases)
- **效果证据：** 作者自报 2026-09-13 第六届长城杯初赛 13/15 题、650 分、328/1508；复盘写明两套 AI 体系分别 3 题和 10 题，有人类、队友、人工搬运和外部模型参与。这是一次人机混合队伍复盘，不能换算成单个自主 Agent 的成功率。[实战复盘](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/06-%E5%AE%9E%E6%88%98%E5%A4%8D%E7%9B%98.md)
- **证据缺口：** 该固定版本没有官方榜单链接、可复现 benchmark、题目集、运行 trace、成本/模型版本清单或消融实验。文中“若互通可到第 20 名”“可挡掉 80% 时间浪费”等是作者推断或经验陈述，不能作为性能保证；“已验证有效”的标语也没有排除人类贡献。[完整树](https://api.github.com/repos/ab2836304651/ctf-agent-playbook/git/trees/2473692e4a47dfec5c1a09570b30a363e8f7d864?recursive=1)、[快速开始](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/00-%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B.md)、[实战复盘](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/06-%E5%AE%9E%E6%88%98%E5%A4%8D%E7%9B%98.md)

## 沿来源链接找候选的结果

**本仓库这一路径新增 Agent 候选为 0。** 检查 README、全部 docs 和 templates，未找到外部 Agent 仓库、论文或 benchmark 来源链接；除 badge 与示例地址外，唯一实质外链是 LICENSE 中的 CC 法律文本。Claude Code、Arthas、OpenRASP 等是文内提及的产品/工具名，没有给出 Agent 框架来源链，因此不补造“上游推荐”。研究上限是“至多 2 个”，不是必须凑 2 个；其他候选应由独立第一手来源研究合并。[固定版本完整文件树](https://api.github.com/repos/ab2836304651/ctf-agent-playbook/git/trees/2473692e4a47dfec5c1a09570b30a363e8f7d864?recursive=1)、[README](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/README.md)、[技术套路库](https://github.com/ab2836304651/ctf-agent-playbook/blob/2473692e4a47dfec5c1a09570b30a363e8f7d864/docs/03-%E6%8A%80%E6%9C%AF%E5%A5%97%E8%B7%AF%E5%BA%93.md)

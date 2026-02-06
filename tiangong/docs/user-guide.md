# Multi-Agent Tool 用户指南

## 快速开始

### 1. 初始化项目（中心化 .tiangong）

```bash
cd multi-agent-tool/scripts
./tiangong.py create my-project /path/to/project docs/plan.md --session-key main
```

> 默认会拉起 PM + Watchdog（OpenClaw sessions_spawn）。若不希望自动拉起：

```bash
./tiangong.py create my-project /path/to/project docs/plan.md --no-spawn
```

或等价：

```bash
./start-project.sh my-project /path/to/project docs/plan.md
```

### 2. 启动 Agent（OpenClaw 适配）

如果需要手动重拉：

```bash
./tiangong.py oc-check --session-key main
./tiangong.py oc-start my-project --session-key main
```

> `oc-start` 会启动 PM + Watchdog，并在当前主会话中回显输出（OpenClaw announce）。

### 3. 工作流

1. **PM** 读取计划并输出 TaskSpec（JSON）。
2. **Orchestrator** 根据 TaskSpec 触发 Worker（OpenClaw sessions_spawn）。
3. **Worker** 执行任务，输出 EvidenceChain。
4. **Watchdog** 审计 EvidenceChain，输出 Verdict。
5. **Orchestrator** 落盘 events/status 并驱动流程。

### 常用命令

```bash
./tiangong.py list
./tiangong.py progress <project>
./tiangong.py risks <project>
./tiangong.py suggest-skill <project> <taskId> --kind <kind>
./tiangong.py select-skill <project> <taskId> <skill>
./tiangong.py approve-tier <project> <taskId> <tier>
./tiangong.py human-verdict <project> <taskId> PASS|BLOCK --reason "..."
```

## 配置说明

- `agent.yaml` 中配置了默认模型 (MiniMax / Claude)。
- 可以修改 `agent.yaml` 调整模型参数。
- `.tiangong/registry.json` 可选：记录可用 skills 与支持的任务类型（供 Router 提示）。

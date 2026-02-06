# tiangong（天工）

**定位**：把项目计划按角色拆解并推进到可交付结果。

## 核心机制
- **角色分工**：PM / Worker / Reviewer / Watchdog
- **TaskSpec**：每个任务有明确 goal + acceptance + context
- **Evidence Chain**：Done 必须可复现（命令/日志/截图/产物）
- **状态面板**：用 `status.json` 结构化记录进度/阻塞/最后一次运行

## 目录说明
- `core/`：协议、状态机、orchestrator、watchdog、skill router 等核心逻辑
- `scripts/`：运行脚本（入口在 `scripts/tiangong.py`）
- `formats/`：状态/任务/证据链的 JSON schema
- `templates/`：agent 模板
- `docs/`：使用说明

## 快速开始
```bash
cd tiankit
python3 tiangong/scripts/tiangong.py --help
```

> 注：当前版本代码来自你在 MiniExplorer 项目中已验证的 multi-agent-tool 实现，已整体迁入 tiankit 作为 tiangong 的初版。

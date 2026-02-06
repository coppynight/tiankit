# tiankit

> Kai 的研发工具包：**tianji**（规划）+ **tiangong**（执行）。

tiankit 的设计理念是：把“项目协作”变成一套可重复、可验证的工程流程。

- **计划不是长文**：计划是 *可执行规格*（spec），包含目标、边界、里程碑与验收标准。
- **进度不是口头**：Done 必须伴随 *可复现证据*（steps / artifacts / logs / screenshots）。
- **协作要可控**：用清晰角色与状态机减少沟通摩擦，避免“看似完成、实际不可用”。

## Components

### tianji（天机）— 从想法到可落地计划
把一个想法（idea）压缩成可交付的项目计划：
- Goal / Non-goals
- Milestones（Phase）
- Task Specs（每个任务的验收标准）
- Risks & mitigations
- Smoke test checklist

> 现阶段：提供模板与占位 CLI；后续可接入你偏好的模型/工具链。

### tiangong（天工）— 从计划到交付（多角色协作 + 证据链）
执行项目计划并持续产出可验收结果：
- 角色：PM / Worker / Reviewer / Watchdog
- 产物：build artifacts + evidence chain + status.json
- 特性：任务规格化、证据链驱动、状态可追踪、卡死/锁/审计辅助

## Repo layout

```
tiankit/
  tianji/
    README.md
    templates/
  tiangong/
    README.md
    core/
    scripts/
    templates/
    formats/
    docs/
  docs/
    philosophy.md
    workflow.md
  pyproject.toml
```

## Quick start (tiangong)

> 目前以脚本形态提供；后续可封装成 pip 包与统一 CLI。

```bash
# 进入仓库
cd tiankit

# 运行天工 orchestrator（示例，具体以 tiangong/docs 为准）
python3 tiangong/scripts/tiangong.py --help
```

## License
TBD（你定：MIT / Apache-2.0 / Private）

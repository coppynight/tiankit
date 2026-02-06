# tiankit workflow

## 核心原则
- Done = 可复现证据（steps + artifacts + logs/screenshots）
- 一次迭代只燃尽一个主风险（控制变量）
- 权限/缓存/设备差异属于产品策略的一部分

## 典型闭环
1. tianji：Idea -> Plan（Phase + TaskSpec + Acceptance）
2. tiangong：Plan -> Execute（worker）
3. reviewer：Verify（回归 + 验收）
4. watchdog：Monitor（卡死/锁/审计）
5. Ship：产物 + evidence

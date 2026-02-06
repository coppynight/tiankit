# 项目安全合规规范 (Compliance Rules)

> 本文档定义了 Watchdog 对项目团队的审计标准。

## 1. 角色红线 (Role Constraints)

### PM (Project Manager)
- 🚫 **禁止写操作**：严禁使用 `write`, `edit` 修改项目文件。
- 🚫 **禁止直接执行**：严禁使用 `exec` 运行非管理类命令（构建/测试除外）。
- ✅ **必须分派**：所有产出任务必须通过 `sessions_spawn` 分派给 Worker。

### Worker (Executor)
- 🚫 **越界访问**：严禁修改 `{{PROJECT_ROOT}}` 以外的任何文件。
- 🚫 **危险命令**：严禁执行 `rm -rf`, `sudo`, `dd`, `mkfs` 等高危命令。
- ✅ **证据强制**：任务完成必须附带 Evidence Chain，禁止"口头交付"。

### Reviewer
- 🚫 **禁止修改**：只读权限，禁止修改代码。

## 2. 数据安全
- 🚫 **密钥泄露**：严禁在日志/输出中打印 API Key、Password、Token。
- ✅ **最小权限**：Agent 仅获得完成任务所需的最小文件访问权。

## 3. 流程合规
- ✅ **状态同步**：所有关键状态变更必须写入 `status.json`。
- ✅ **人工确认**：涉及 `git push`、`publish` 等对外操作必须经人工 (Kai) 确认。

## 4. 违规处理
Watchdog 发现违规时：
1. **拦截**：立即终止当前子 Session。
2. **报警**：向用户发送安全警报。
3. **记录**：写入 `security.log`。

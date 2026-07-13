# Quickstart: Session 持久化

**Feature**: 005-session-persistence
**Date**: 2026-07-13

## Prerequisites

- Python 3.12+
- myAgent project at `Stage 2/project/`
- 已有依赖：`openai`，无新增第三方依赖

## Validation Scenarios

### 1. 基本持久化与恢复

```bash
cd Stage 2/project

# 1.1 启动新 session 并进行对话
python main.py
> 帮我创建一个 hello.py，打印 "Hello World"
# Agent 创建文件并回复
> /exit

# 1.2 检查持久化
ls .myagent/sessions/
# 应看到 {uuid}.db 文件

# 验证数据库内容
python -c "
import sqlite3, glob, os
files = glob.glob('.myagent/sessions/*.db')
db = sqlite3.connect(files[0])
for row in db.execute('SELECT role, substr(content,1,40) FROM messages ORDER BY seq'):
    print(row)
db.close()
"
# 应看到 system, user, assistant 消息按序排列

# 1.3 恢复
python main.py --resume
# 选择刚才的 session
> 给 hello.py 加上 main 函数
# Agent 应理解上下文并正确编辑
```

### 2. 空对话清理

```bash
# 2.1 启动后立即退出
python main.py
> /exit

# 2.2 验证无残留
ls .myagent/sessions/
# 应无 .db 文件（空对话被自动清理）
```

### 3. Session 列表管理

```bash
# 3.1 创建 3 个 session
# Session 1: python main.py → "列出当前目录" → /exit
# Session 2: python main.py → "今天是几号" → /exit
# Session 3: python main.py → "帮我写排序函数" → /exit

# 3.2 查看列表
python main.py --resume
# 应显示 3 个 session，按时间降序
# 标题分别为 "帮我写排序函数"、"今天是几号"、"列出当前目录"

# 3.3 删除测试
# 在列表中按 D → 选中第二个 → 确认 y
# 验证 .myagent/sessions/ 中只剩 2 个 .db 文件

# 3.4 重命名测试
# 在列表中按 R → 选中第一个 → 输入新标题
# 验证列表刷新后显示新标题
```

### 4. 权限恢复

```bash
python main.py --resume
# 选择包含工具调用的 session
# 对 bash 工具 session 级 allow 后退出
python main.py --resume  # 再次恢复同一 session
# 触发 bash 工具
# 验证：不应弹权限确认直接执行
```

### 5. Todo 恢复

```bash
python main.py
> 帮我同时处理三个任务：创建文件 A、创建文件 B、创建文件 C
# Agent 使用 TodoWrite 规划 3 个任务
# 等待 Agent 创建完文件 A (1/3 done)
> /exit

python main.py --resume
# 选择刚才的 session
> 继续之前未完成的任务
# Agent 应记得已创建的 Todo 列表状态（2/3 remaining）
```

### 6. Compact 不干扰

```bash
python main.py
# 进行 15+ 轮长对话，触发 compact
> /exit

python main.py --resume
# 验证恢复的消息为 compact 前的原始完整内容（非压缩摘要）
```

## Expected Results

| # | Scenario | Pass Criteria |
|---|----------|--------------|
| 1 | 基本持久化 | `.db` 文件存在，messages 完整，恢复后上下文连贯 |
| 2 | 空对话清理 | 无 user 消息的 session 退出后 `.db` 被删除 |
| 3 | 列表管理 | 列表按时间排序，删除/重命名生效 |
| 4 | 权限恢复 | 恢复后 allow 的工具不弹确认 |
| 5 | Todo 恢复 | Todo 列表状态与退出前一致 |
| 6 | Compact 不干扰 | 恢复的是 compact 前的原始消息 |

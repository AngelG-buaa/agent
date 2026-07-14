# Quickstart: Session 持久化 验证指南

**Feature**: 007-session-persistence
**Date**: 2026-07-14

本指南提供可运行的验证场景，用于端到端验证 session 持久化功能。

## 环境准备

```bash
cd Stage 2/project
export PYTHONPATH=.
```

确保 `.myagent/sessions/` 目录不存在（首次运行自动创建）：
```bash
rm -rf .myagent/sessions/
```

## 验证场景

### 场景 1：新建 → 对话 → 退出 → 文件存在

```bash
# 1. 启动新 session
python main.py
# (输入) 列出当前目录的文件
# (Agent 输出)
# (输入) /exit

# 2. 验证数据库文件存在
ls .myagent/sessions/*.db
# 预期: 1 个 .db 文件

# 3. 用 sqlite3 检查内容
python -c "
import sqlite3, glob, json
db = glob.glob('.myagent/sessions/*.db')[0]
conn = sqlite3.connect(db)
cursor = conn.execute('SELECT role, content IS NOT NULL FROM messages ORDER BY seq')
for row in cursor:
    print(row)
conn.close()
"
# 预期输出:
# ('system', 1)
# ('user', 1)
# ('assistant', 1)
```

### 场景 2：空对话自动清理

```bash
# 1. 启动新 session，不输入任何内容直接退出
python main.py
# 看到 🤖 提示后直接 Ctrl+C

# 2. 验证 .myagent/sessions/ 为空
ls .myagent/sessions/*.db 2>/dev/null
# 预期: No such file or directory (目录为空)
```

### 场景 3：`--resume` 恢复

```bash
# 1. 创建并保留一个 session
python main.py <<EOF
你好，我叫小明
/exit
EOF

# 2. 恢复该 session
python main.py --resume
# 预期: 看到 session 列表，标题为 "你好，我叫小明"
# 用 ↑↓ 选择，Enter 选中
# 选择 [R]esume

# 3. 验证上下文连续
# (输入) 我叫什么名字？
# 预期: Agent 回答 "小明"（理解上下文）
```

### 场景 4：`/resume` REPL 内切换

```bash
# 1. 启动新 session A，对话一轮
python main.py
# (输入) 今天的天气真好
# (Agent 输出)

# 2. 在 REPL 内切换
# (输入) /resume
# 预期: 看到所有历史 session，包括 "你好，我叫小明"
# 选择另一个 session B → [R]esume
# 预期: 切换到 session B 的上下文

# 3. 验证切换后上下文正确
# (输入) 我叫什么名字？
# 预期: Agent 回答 "小明"（B session 的上下文）

# 4. 切回 session A
# (输入) /resume
# 选择 session A → [R]esume
# (输入) 我刚才说了什么？
# 预期: Agent 理解 "今天的天气真好" 的上下文
```

### 场景 5：删除与重命名

```bash
python main.py --resume
# 预期: 看到历史 session 列表
# 选择一个 session → [D]elete
# → 确认: y
# 预期: 列表刷新，该 session 消失

# 选择一个 session → [R]ename
# 输入新标题
# 预期: 列表刷新，显示新标题

# 按 Q 取消
# 预期: 开始新 session
```

### 场景 6：权限恢复

```bash
# 1. 创建 session，触发权限确认
python main.py
# (输入) 用 bash 执行 echo hello
# (Agent 会触发权限确认: 允许 bash)
# 选择 [a] 始终允许
# (输入) 再用 bash 执行 echo world
# 预期: 不弹确认，直接执行（权限已 saved）
# (输入) /exit

# 2. Resume 该 session
python main.py --resume
# 选择该 session → [R]esume
# (输入) 用 bash 执行 echo test
# 预期: 不弹确认，直接执行（权限已 restored）

# 3. 切换到新 session
# (输入) /resume
# 选择开始新 session（或取消）
# (输入) 用 bash 执行 echo bad
# 预期: 再次弹出权限确认（权限不跨 session 共享）
```

### 场景 7：SubAgent 消息隔离

```bash
python main.py
# (输入) 帮我做一个复杂任务，先创建 todo，然后逐步完成
# 预期: Agent 可能使用 Task 工具委托子任务
# (输入) /exit

# 用 sqlite3 验证主 session 中无 SubAgent 中间消息
python -c "
import sqlite3, glob
db = glob.glob('.myagent/sessions/*.db')[0]
conn = sqlite3.connect(db)
rows = conn.execute('SELECT role, content FROM messages ORDER BY seq').fetchall()
print(f'Total messages: {len(rows)}')
for r in rows:
    print(f'  {r[0]}: {str(r[1])[:80] if r[1] else \"(tool call)\"}...')
conn.close()
"
# 预期: 所有消息 role 为 system/user/assistant/tool
#      SubAgent 的中间 system prompt 不出现在主 session
```

## 自动化测试

```bash
# Repository 单元测试
python -m pytest tests/test_session_manager.py -v

# 端到端集成测试
python -m pytest tests/test_session_persistence.py -v

# 权限隔离测试
python -m pytest tests/test_permission_engine.py -v

# 完整测试套件
python -m pytest tests/ -q
```

## 验收检查清单

- [ ] `conversation.py` 不定义 SessionController，且不直接访问 SessionManager 或注册 Hook
- [ ] `main.py` 不包含 session 列表、删除、重命名或菜单分支（只有 `conv.start(resume=args.resume)`）
- [ ] `agent.py` 不再定义 `_normalize_message()`，统一使用 `agent.utils.normalize_message()`
- [ ] final assistant、assistant tool calls 和 tool result 都只追加一次并成功持久化
- [ ] 数据库中的 system prompt 与实际 Agent system prompt 一致（非空占位符）
- [ ] SessionController 独立文件（`agent/session_controller.py`），拥有 active、grant listener、Todo Hook 和 disposer
- [ ] `register_hook()` 返回 disposer；`TodoReminderHandle` 不直接访问全局 `HOOKS`
- [ ] 启动菜单和 REPL 菜单使用同一套循环式流程（非递归）
- [ ] 取消和加载失败不改变旧 active 状态
- [ ] SubAgent 中间消息不会进入主 session
- [ ] SessionManager 不打印终端文本，不包含 ActiveSession 业务状态
- [ ] 恢复消息只含 role/content/tool_calls/tool_call_id，无 tool_name 或 SDK object
- [ ] A session 的权限/Todo/reminder 不在 B session 生效
- [ ] 损坏数据库不在 Windows 留下文件锁
- [ ] Schema 初始化只有唯一实现入口
- [ ] 全量测试通过

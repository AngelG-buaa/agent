# Quickstart: TodoWrite Tool Validation

**Feature**: TodoWrite Tool | **Date**: 2026-07-11

## Prerequisites

- Python 3.12+
- 项目依赖已安装（`openai` 等）
- LLM API key 已配置（`config.py`）

## Validation Scenarios

### 1. Unit: TodoWriteTool 基本功能

运行单元测试验证工具本身的行为：

```bash
cd d:/LLM/Agent/Stage 2/project
python -m pytest tests/test_todo_write.py -v
```

**Expected**: 所有测试通过，覆盖：
- 创建 pending/in_progress/completed 任务
- 非法 status 被拒绝
- 空 content 被拒绝
- 空列表正常处理
- 连续两次调用以最后一次为准

### 2. Unit: Agent 循环计数器逻辑

验证 Agent 主循环中的计数器 + 提醒注入：

```bash
python -m pytest tests/test_todo_write.py -v -k "agent"
```

**Expected**: 覆盖：
- todo_write 调用后计数器重置
- 连续 3 轮未调用 → 提醒注入
- 提醒注入后计数器重置
- 纯文本轮次也递增计数器

### 3. Integration: Agent 端到端规划行为

给 Agent 发一个需要多步操作的任务，观察其规划行为：

```bash
python main.py
```

将 `main.py` 中的 question 临时改为：

```python
question = "给我写一份关于项目目录结构的简要说明，先列 todo 再执行"
```

**Expected**:
- Agent 在首次工具调用中使用了 todo_write 列出步骤
- 终端显示带状态图标的任务列表 `[ ]` / `[▸]` / `[✓]`
- Agent 按步骤逐个执行，更新状态
- 最终所有任务标记为 completed

### 4. Integration: Nag 提醒机制

修改 `agent/agent.py` 临时将 `max_steps` 设为 5，将提醒阈值从 3 临时降为 1：

```python
# 临时测试修改
if rounds_since_todo >= 1:  # 原值: 3
```

运行 Agent 并发一个简单但不触发 todo_write 的问题（如 "1+1=?"），观察 Agent 在下一个问题中是否收到提醒。

**Expected**: 第一问无提醒；第二问前出现 `<reminder>Update your todos.</reminder>`。

### 5. Regression: 现有工具不受影响

验证 todo_write 加入后，现有工具仍正常工作：

```bash
python -c "
from tooling.executor import build_tool_executor
from tools import register_all
from config import WORKDIR

executor = build_tool_executor(project_root=WORKDIR)
register_all(executor, include_dangerous=False, workdir=WORKDIR)

# 验证核心工具可正常执行
print(executor.execute('get_time', {}))
print(executor.execute('calculator', {'expression': '2+3'}))
print(executor.execute('todo_write', {'todos': [
    {'content': 'test', 'status': 'pending'}
]}))
print('All tools OK')
"
```

**Expected**: 三个工具均正常返回，无异常。

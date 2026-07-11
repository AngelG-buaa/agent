# Quickstart: Task Tool & Sub-Agent

**Feature**: 002-task-subagent-tool
**Date**: 2026-07-11

## 前置条件

- Python 3.12+
- 已安装项目依赖（`pip install openai` 等）
- 有效的 LLM API 配置（`config.py` 中的 `api_key`、`base_url`、`model`）

## 验证场景

### 场景 1: Agent 通过 task 工具委派简单子任务

**目标**: 验证 Sub-Agent 能够启动、执行工具、返回结论。

**步骤**:
1. 启动 main.py
2. 输入问题：`"用 task 工具帮我检查 tools/ 目录下有多少个 .py 文件"`
3. 预期观察：
   - 终端输出 `[Subagent spawned] 检查 tools/ 目录下有多少个 .py 文件`
   - 终端出现 `[sub] bash(ls tools/*.py)` 或 `[sub] glob(tools/*.py)`
   - 终端输出 `[Subagent done]`
   - 主 Agent 回复中包含准确的文件数量

**验证点**:
- ✅ `[Subagent spawned]` 标记出现
- ✅ `[sub]` 前缀的 Sub-Agent 工具调用可见
- ✅ `[Subagent done]` 标记出现
- ✅ 主 Agent 回复基于 Sub-Agent 返回的结果

---

### 场景 2: Sub-Agent 工具限制验证

**目标**: 验证 Sub-Agent 不能调用 `task` 或 `todo_write`。

**步骤**:
1. 启动 main.py
2. 输入问题：`"用 task 工具帮我规划一个任务：在项目根目录创建一个 test_sub.txt 文件，写入 hello，然后删除它。请 Sub-Agent 来规划步骤。"`
3. 预期观察：
   - Sub-Agent 能被创建并执行
   - Sub-Agent 不会（也不能）调用 `todo_write` 进行规划
   - Sub-Agent 不会（也不能）调用 `task` 再次委派
   - Sub-Agent 直接执行写入和删除操作

**验证点**:
- ✅ Sub-Agent 的工具调用中不出现 `todo_write`
- ✅ Sub-Agent 的工具调用中不出现 `task`
- ✅ 任务被正确执行（文件创建后删除）

---

### 场景 3: 权限检查在 Sub-Agent 中仍然生效

**目标**: 验证 Sub-Agent 调用敏感工具（如 write_file）时，用户仍被询问权限。

**步骤**:
1. 启动 main.py
2. 输入问题：`"用 task 工具帮我在 docs/ 目录下创建一个 test_subagent.md 文件，内容为 # Sub-Agent Test"`
3. 预期观察：
   - Sub-Agent 调用 `write_file` 时，终端出现权限确认提示 `⚠ 权限确认`
   - 用户选择 `[y]` 允许后，操作继续执行
   - 后续 Sub-Agent 再调用 write_file（同一 session 内）不再询问（session ALLOW 生效）

**验证点**:
- ✅ `write_file` 触发 `⚠ 权限确认` 提示
- ✅ 用户可按 `[y]` 允许或 `[n]` 拒绝
- ✅ Session ALLOW 规则在 Sub-Agent 中生效

---

### 场景 4: Sub-Agent 最大轮数限制

**目标**: 验证 Sub-Agent 在 30 轮后被正确终止。

**步骤**:
1. 构造一个需要大量迭代的任务（如 "用 task 工具逐个读取 tools/ 目录下所有文件，每个文件读一行就记录一行，直到读完所有文件"——如果文件足够多）
2. 或使用已知会触发多轮的任务
3. 预期观察：
   - Sub-Agent 在第 30 轮前收到提醒（可从终端输出推断）
   - Sub-Agent 在 30 轮后被强制终止
   - 返回的结论包含部分信息（非空）

**验证点**:
- ✅ Sub-Agent 不会超过 30 轮
- ✅ Sub-Agent 返回非空的部分结论
- ✅ 主 Agent 能基于部分结论继续工作

---

### 场景 5: 主 Agent 上下文不被 Sub-Agent 中间步骤污染

**目标**: 验证上下文隔离的核心价值。

**步骤**:
1. 在 main.py 的 Agent 循环中，添加 `print(len(messages))` 在 `run()` 的每次迭代后（临时调试）
2. 输入问题：`"用 task 工具读取 tools/ 目录下所有 .py 文件的前 10 行并汇总"`
3. 预期观察：
   - Sub-Agent 执行期间读了许多文件（在 `[sub]` 输出中可见）
   - Sub-Agent 完成后，主 Agent 的消息列表长度仅增加 task 工具调用 + 工具结果（2 条消息），而非 Sub-Agent 读过的所有文件内容

**验证点**:
- ✅ 主 Agent 消息数增长与 Sub-Agent 内部操作数无关
- ✅ 主 Agent 回复只引用 Sub-Agent 返回的汇总结论

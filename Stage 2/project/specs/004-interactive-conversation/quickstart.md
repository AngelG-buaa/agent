# Quickstart: 交互式对话

**Feature**: 004-interactive-conversation
**Date**: 2026-07-12

## Prerequisites

- Python 3.12+ 环境已配置
- LLM API Key 已在 `config.py` 中配置
- 在项目根目录 `d:/LLM/Agent/Stage 2/project/` 下执行

## Validation Scenarios

### Scenario 1: 多轮连续对话 (P1 Smoke Test)

**目标**: 验证 Agent 能连续处理多轮对话，上下文正确保持。

```bash
D:/Miniconda/envs/llm/python main.py
```

**操作与预期**:

```
👤 你: 帮我创建一个 hello.py，内容打印 Hello World
🤖 Agent: [创建文件，回复完成]
👤 你: 给它加上一个 farewell 函数，打印 Goodbye World
🤖 Agent: [理解"它"=hello.py，编辑文件，添加函数]
👤 你: 我第一轮让你创建的文件叫什么名字？
🤖 Agent: [正确回答: hello.py]
👤 你: /exit
👋 再见！
```

**通过标准**: Agent 能连续处理 ≥3 轮相关对话，正确理解上下文指代。

---

### Scenario 2: Agent 反问用户 (P2 Smoke Test)

**目标**: 验证 Agent 在歧义时主动反问。

```bash
D:/Miniconda/envs/llm/python main.py
```

**操作与预期**:

```
👤 你: 帮我分析一下数据文件
🤖 Agent 想问: 我在工作目录中找到以下数据文件：
    1. data.csv
    2. data.json
    3. data.parquet
    你想让我分析哪个文件？
👤 你的回答: data.json
🤖 Agent: [读取 data.json，分析内容，回复结果]
```

**通过标准**: Agent 识别歧义并反问；收到明确回答后继续完成任务。

---

### Scenario 3: 无效回答的回退 (P2 Edge Case)

**目标**: 验证用户说"不知道"时 Agent 自行判断。

```
👤 你: 帮我改一下配置文件
🤖 Agent 想问: 你是指 config.py 还是 config.yaml？
👤 你的回答: 不知道，你自己看
🤖 Agent: [自行检查文件，选择最可能的，给出最佳猜测]
```

**通过标准**: Agent 不重复追问，基于上下文自行判断并继续。

---

### Scenario 4: 权限跨轮保持 (P3)

**目标**: 验证权限的"始终允许"跨轮生效。

```
👤 你: 帮我读一下 README.md
🤖 Agent: [调用 read_file]
  ⚠ 权限确认: ...
  [a]始终允许?
👤 (按 a)
🤖 Agent: [读取并回复]
👤 你: 再看看 config.py
🤖 Agent: [直接读取，不再询问权限]
```

**通过标准**: 第二轮不重复弹出权限确认。

---

### Scenario 5: 空输入忽略

```
👤 你: (直接按回车)
👤 你: (再次回车)
👤 你: 列出当前目录的文件
🤖 Agent: [正常回复]
```

**通过标准**: 空输入不报错，不发送给 LLM。

---

### Scenario 6: Ctrl+C 中断

```
👤 你: 帮我分析所有 Python 文件
🤖 Agent: [开始执行...]
(按 Ctrl+C)
⚠️ 已中断当前操作。
👤 你: 只分析 agent/ 目录下的
🤖 Agent: [正常回复]
(再次 Ctrl+C)
⚠️ 再次中断，退出程序。
```

**通过标准**: 第一次 Ctrl+C 中断当前轮回到输入状态；第二次 Ctrl+C 退出程序。

---

## Run Tests

```bash
D:/Miniconda/envs/llm/python -m pytest tests/ -q
```

验证现有测试全部通过（无回归），新增测试覆盖 Conversation 和 AskUserTool。

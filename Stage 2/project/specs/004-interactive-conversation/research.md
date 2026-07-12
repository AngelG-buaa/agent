# Research: 交互式对话

**Feature**: 004-interactive-conversation
**Date**: 2026-07-12

## 1. REPL 外循环架构

### Decision: 新建 `Conversation` 类管理外循环

### Rationale

基于 Constitution Principle X（Recognize When to Elevate Design）评估三种方案：

| 方案 | 评估 |
|------|------|
| A) `main.py` 中直接 `while True` | ❌ 违反准则 1（概念内聚）：状态管理（messages、首轮标记、退出逻辑）散落在 main.py 中，与其他模块混合 |
| B) 新建 `Conversation` 类 | ✅ 满足准则 1+3：多轮状态（messages[]、_first_turn、轮数计数）内聚在一个类中；Agent 核心循环保持纯洁 |
| C) Agent 子类 | ❌ 不满足继承判断矩阵："子类是父类的一种特殊情况"不成立——多轮对话不是 Agent 的特殊情况，而是在 Agent 之上的编排层 |

**选 B**。`Conversation` 是一个新的编排层概念，位于 Agent 之上，负责"多轮对话的外循环管理"。这与 Claude Code 中 `QueryEngine`（管理多轮会话状态）在 `query`（单轮执行循环）之上的分层一致。

### Implementation Pattern

```python
# agent/conversation.py
class Conversation:
    def __init__(self, agent: Agent):
        self.agent = agent
        self.messages: list[dict] = []
    
    def start(self):
        """主 REPL 循环"""
        while True:
            user_input = self._get_input()
            if user_input is None:  # /exit or Ctrl+C
                break
            if not user_input.strip():
                continue
            self._run_turn(user_input)
    
    def _run_turn(self, user_input: str):
        """执行一轮对话"""
        if not self.messages:  # 空列表 = 新会话
            self.messages.append({"role": "system", "content": self.agent.system_prompt})
        self.messages.append({"role": "user", "content": user_input})
        
        answer = self.agent.run(self.messages)
        print(answer)
    
    def _get_input(self) -> str | None:
        try:
            return input("👤 你: ")
        except (EOFError, KeyboardInterrupt):
            return None
```

### Alternatives Considered

- **main.py 内联 while True**：简单但违反分层原则，Constitution 拒绝
- **Agent 子类**：概念不合适。多轮对话是编排概念，不是 Agent 变体

---

## 2. Agent.run() 改造

### Decision: `run(messages: list[dict])` — 由调用方负责组装 messages

### Rationale

messages 的本质是"对话历史"——这是一个应该在 Agent 之外被管理的概念。让调用方负责组装有四个原因：

1. **概念清晰**：`run()` 的契约从"给我一句话"变成"给我一段对话历史，我帮你推进"。messages 从"隐式创建"变为"一等公民显式传入"。
2. **循环体零改动**：只需删除前 3 行（messages 创建 + system prompt + user 插入），其余完全不变。
3. **无额外方法/委托链**：不需要 `continue_from`，不存在 `UserPromptSubmit` 双重触发问题。
4. **接近 Claude Code 的 `query()` 设计**：接收已组装好的 messages。

### 方案演进

最初方案是 `run()` 委托给 `continue_from()`，但发现了两个问题：
- `continue_from` 名字别扭
- `UserPromptSubmit` hook 双重触发
- 所谓"向后兼容"在当前项目中没有实际受益者

最终选择直接改 `run(messages)`——更简洁，Constitution 允许合理的 breaking change。

### 变更影响面

| 调用方 | 改动 |
|--------|------|
| Conversation (新增) | 组装 messages → `agent.run(self.messages)` |
| SubAgent / TaskTool | `sub.run(desc)` → `sub.run([sys_msg, user_msg])` |
| main.py 单次模式 | `agent.run(q)` → `agent.run([sys_msg, user_msg])` |
| 测试 | 同上的机械性改动 |

---

## 3. Ctrl+C 信号处理

### Decision: `try/except KeyboardInterrupt` 在 Conversation 外循环

### Rationale

Python 的 `KeyboardInterrupt` 本身就是为这个场景设计的。不需要复杂的 signal handler。

- Conversation 层捕获 KeyboardInterrupt → 优雅退出
- Agent 执行期间 Ctrl+C → Exception 向上传播到 Conversation
- 不丢失状态——messages 在内存中，下次 SIGINT 不会破坏

```python
try:
    answer = self.agent.continue_from(self.messages)
except KeyboardInterrupt:
    print("\n⚠️ 已中断当前操作。")
    # 不退出程序，回到输入等待状态
    continue
```

### Edge case: 连续 Ctrl+C

第一次 Ctrl+C 中断 Agent → 回到输入状态。第二次 Ctrl+C → 退出程序。用标志位跟踪。

---

## 4. ask_user 工具设计

### Decision: 标准 Tool 子类，遵循现有工具注册模式

### Rationale

Constitution IX 明确说新能力优先通过 Tool 层接入。ask_user 是一个完全符合现有 Tool 契约的工具：

- 继承 `Tool` ABC → 实现 `run(parameters)` 和 `get_parameters()`
- 注册到 ToolExecutor → LLM 通过 function calling 自动发现
- 与 permission 系统自然兼容（ask_user 可被设为 allow 规则，无需权限确认）

```python
class AskUserTool(Tool):
    def __init__(self):
        super().__init__(
            name="ask_user",
            description="向用户提问以澄清歧义或获取偏好。在信息不足、有多种可能解释、或需要用户偏好判断时使用。不要在可通过工具获取的客观事实上提问。",
        )
    
    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="question", type="string",
                         description="要问用户的问题。应在问题中附带足够的背景信息（当前在做什么、为什么需要用户输入、可选选项），使用户无需查看对话历史即可理解。",
                         required=True),
        ]
    
    def run(self, parameters: dict) -> dict:
        question = parameters["question"]
        print(f"\n🤖 Agent 想问: {question}")
        try:
            answer = input("👤 你的回答: ")
        except (EOFError, KeyboardInterrupt):
            return {"answer": "", "note": "用户选择不回答"}
        return {"answer": answer}
```

### Interaction with Permission System

`ask_user` 是只读/交互工具，不对文件系统产生影响。建议加入 Gate 2 (allow) 规则，无需用户二次确认（否则会出现"Agent 想问你问题，但你要先批准它问你"的荒谬情况）。

---

## 5. System Prompt 重设计

### Decision: 整体重写行为准则——从"任务执行器"变为"对话协作者"

### Rationale

原先的 SYSTEM_PROMPT 是为单次任务执行设计的，核心指令"不要过分收集信息，有了可以初步回答查询的信息即可回答"与反问功能存在根本冲突。简单追加"反问规则"会导致 LLM 面对矛盾指令。

改为整体重设计：将"行为准则"拆为三个子节——**工具使用**（保留）、**自主决策 vs 反问用户**（新增决策框架）、**提问规范**（新增操作规范）。核心变化：

- 删除"不要过分收集信息"→ 替换为决策框架：自主决策的场合 vs 反问的场合
- 人格从"智能助手"→"智能协作者"（暗示持续协作关系）
- 新增并发约束："如需反问，不要在同一轮混入其他工具调用"

完整 prompt 已写入 `agent/prompts.py`。改动量：约 20 行替换而非追加。

### Guardrails 总览

| 防护 | 机制 |
|------|------|
| 防止不过度提问 | "答案可通过工具获取的，自己判断" |
| 防止从不提问 | "多种合理理解、需要用户偏好、关键信息缺失"触发 ask_user |
| 防止追问死循环 | "连续 2 次未获有效回答→停止追问，自行决策" |
| 防止无效回答 | "不知道/随便 = 授权自行判断" |
| 防止并发混乱 | "反问时不混入其他工具调用" |

---

## 6. /exit 命令处理

### Decision: 在 Conversation 层拦截，不送入 LLM

### Rationale

`/exit` 是元命令（meta-command），不应该作为对话内容送给 LLM：
- 交给 LLM 处理不可靠（LLM 可能忽略、误解释）
- 增加不必要的 API 调用
- 与现有 pattern 一致（`terminal_approver` 的 y/n/a 也是在工具层拦截）

```python
def _get_input(self) -> str | None:
    user_input = input("👤 你: ").strip()
    if user_input.lower() in ("/exit", "/quit", "exit"):
        return None
    return user_input
```

---

## 7. 跨轮状态保持

### Decision: 无需额外改动，现有机制已支持

### Rationale

- **PermissionEngine**: 会话规则存储在 `self._session_rules` dict 中，只要 executor 实例不销毁，规则自然跨轮保持
- **TodoWrite**: `CURRENT_TODOS` 是模块级全局变量，自然跨轮保持
- **Compact**: `compact_pipeline()` 在每轮 LLM 调用前运行（已在 `continue_from` 中），需验证跨轮场景下 TodoWrite 恢复逻辑

唯一需要验证的是 compact L4（摘要压缩）后 TodoWrite 恢复在跨轮场景下是否正常工作——这部分已有代码，仅需测试确认。

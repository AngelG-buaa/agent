# myAgent — LLM Agent 个人学习项目

从零搭建的 CLI Agent 系统（不依赖 LangChain、CrewAI 等框架），面向多步骤工具调用任务。实现了 Think-Act-Observe 核心循环、受控工具执行、可恢复会话、长期记忆与知识增强检索。

## 整体架构

```
用户输入 → Conversation (REPL)
               ├── MemoryService.recall()        ← semantic + lexical 混合召回 + RRF 排序
               │
               ▼
             Agent.run()                         ← Think → Act → Observe 循环
               ├── compact_pipeline()            ← 四层渐进式压缩 (L3→L1→L2→L4)
               ├── LLM.chat()                    ← Think：调用大模型
               └── ToolExecutor.execute()        ← Act：执行工具
                     ├── PermissionEngine        ← 实例级权限，3 步评估管线
                     └── Tool.run()              ← 14 个内置工具
               │
               ▼
             SessionController                   ← SQLite 持久化
               ├── 消息、权限授权、任务列表
               └── 恢复 / 切换 / 删除
```

## 核心功能

| 模块 | 说明 |
|------|------|
| **Agent Loop** | Think-Act-Observe 循环，循环体固定不膨胀，新能力通过构造注入或子类覆盖接入 |
| **工具系统** | 14 个内置工具（bash、文件读写、网页搜索、子任务委派、记忆、RAG），统一注册 + 执行器网关 |
| **权限引擎** | 实例级注入（非全局 Hook），3 步管线（DENY→ALLOW→ASK），~44 条安全策略，支持会话授权持久化 |
| **上下文压缩** | 四层渐进式压缩（L3 大结果持久化 → L1 消息截断 → L2 旧结果占位符 → L4 LLM 摘要），便宜的先跑、贵的后跑 |
| **SubAgent** | 子任务独立上下文，受限工具集（禁止递归委派），30 轮限制，只返回结论不含中间步骤 |
| **长期记忆** | Markdown 文件为事实真源，semantic + lexical 双通道检索 + RRF 融合排序，每轮自动召回并临时注入 |
| **RAG 管线** | 文档解析（TXT/PDF）→ 句级分块 → Embedding 向量化 → FAISS/Qdrant 存储 → 两阶段检索（先搜摘要再读正文）|
| **Session 持久化** | 每会话独立 SQLite 数据库（消息 + 权限 + Todo），完整快照恢复，路径越界防护 |

## 快速开始

```bash
# 1. 克隆并安装依赖
git clone git@github.com:AngelG-buaa/agent.git
cd agent
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 API Key（兼容 DeepSeek / 阿里云百炼等 OpenAI 兼容 API）

# 3. 启动
python src/main.py
```

## 项目结构

```
src/
├── main.py                  # 组装入口（Composition Root）
├── config.py                # 集中配置（LLM、Embedding、RAG、Memory、Compaction）
├── hooks.py                 # 7 事件 Hook 系统
├── agent/                   # Agent 核心循环 + 会话管理 + 上下文压缩
├── tools/                   # 14 个内置工具
├── tooling/                 # 工具基础设施 + 权限引擎
├── memory/                  # 长期记忆（Markdown 存储 + 混合检索）
├── rag/                     # RAG 子系统（解析→分块→Embedding→存储→检索）
├── embedding/               # 共享 Embedding 客户端
└── terminal/                # IO 抽象层
```

## 技术栈

- **语言**：Python 3.12+
- **LLM**：OpenAI 兼容 API（DeepSeek / 通义千问 / GPT）
- **Embedding**：OpenAI 兼容共享 Embedder
- **向量存储**：FAISS / Qdrant
- **测试**：pytest
- **开发流程**：Spec Kit（specify → clarify → plan → tasks → implement）

## 设计理念

- **零框架依赖**：每个模块从第一原理出发构建，理解 Agent 系统的每一层
- **Constitution IX**：Agent 循环体不随功能增长而膨胀。新能力通过构造参数、子类覆盖或请求上下文接入，永远不改循环体本身
- **先便宜后昂贵**：上下文压缩前三层零 API 调用，挡不住了才出动 LLM 做摘要
- **事实源单一**：记忆以独立 Markdown 文件为真源，SQLite 会话可随时丢弃重建

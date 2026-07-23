# myAgent — LLM Agent 个人学习项目

从零搭建的 CLI Agent 系统，不依赖 LangChain、CrewAI 等框架，面向多步骤工具调用任务。实现了 Think-Act-Observe 核心循环、受控工具执行、可恢复会话、长期记忆与知识增强检索。

## 整体架构

```
用户输入 → Conversation
               ├── MemoryService.recall()        记忆召回：双通道检索 + RRF 融合
               │
               ▼
             Agent.run()                         核心循环：Think → Act → Observe
               ├── compact_pipeline()            四层渐进式上下文压缩
               ├── LLM.chat()                    Think：调用大模型
               └── ToolExecutor.execute()        Act：执行工具
                     ├── PermissionEngine        实例级权限评估
                     └── Tool.run()              14 个内置工具
               │
               ▼
             SessionController                   SQLite 持久化
               ├── 消息、权限授权、任务列表
               └── 恢复、切换、删除
```

## 核心功能

| 模块 | 说明 |
|------|------|
| **Agent Loop** | Think-Act-Observe 循环，循环体固定不膨胀，新能力通过构造注入、子类覆盖或请求上下文接入 |
| **工具系统** | 14 个内置工具，覆盖 bash 执行、文件读写、网页搜索、子任务委派、记忆读写和知识库检索，统一注册、统一执行 |
| **权限引擎** | 实例级注入而非全局 Hook，DENY → ALLOW → ASK 三步评估，内置安全策略覆盖危险命令拦截、文件越界保护和敏感操作审批，支持会话级授权并持久化到 SQLite |
| **上下文压缩** | 四层渐进式压缩：大结果持久化到磁盘 → 消息数量截断 → 旧工具结果占位符化 → LLM 摘要。前三层零 API 调用，挡不住了才出动 LLM |
| **SubAgent** | 子任务拥有独立的上下文和受限的工具集，禁止递归委派，30 轮限制，中间步骤随函数返回而销毁，主 Agent 只收到一句结论 |
| **长期记忆** | 以 Markdown 文件为事实真源，语义检索和词法检索双通道并行，RRF 融合排序，每轮对话自动召回并临时注入上下文，不进 SQLite、不进压缩管线 |
| **RAG 管线** | 覆盖文档解析、句级分块、Embedding 向量化、FAISS 与 Qdrant 双存储，以及两阶段检索——先搜索引卡看摘要，再按需翻书读正文 |
| **Session 持久化** | 每个会话对应一个独立的 SQLite 数据库，消息、权限授权和任务列表完整落盘，支持历史会话的恢复、切换、重命名和删除 |

## 快速开始

```bash
# 1. 克隆并安装依赖
git clone git@github.com:AngelG-buaa/agent.git
cd agent
pip install -r requirements.txt

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env 填入你的密钥，兼容 DeepSeek、阿里云百炼等 OpenAI 兼容 API

# 3. 启动
python src/main.py
```

## 项目结构

```
src/
├── main.py                  # 组装入口，解析参数、创建依赖、启动会话
├── config.py                # 集中配置：LLM、Embedding、RAG、Memory、Compaction
├── hooks.py                 # 7 事件 Hook 系统
├── agent/                   # Agent 核心循环、会话管理、上下文压缩
├── tools/                   # 14 个内置工具
├── tooling/                 # 工具基础设施与权限引擎
├── memory/                  # 长期记忆：Markdown 存储与混合检索
├── rag/                     # RAG 子系统：解析、分块、向量化、存储、检索
├── embedding/               # 共享 Embedding 客户端
└── terminal/                # IO 抽象层
```

## 技术栈

- **语言**：Python 3.12+
- **LLM**：OpenAI 兼容协议，支持 DeepSeek、通义千问、GPT 等
- **Embedding**：与 LLM 共用同一个 OpenAI 兼容客户端
- **向量存储**：FAISS 为主，Qdrant 为备选
- **测试**：pytest
- **开发流程**：Spec Kit，从需求规格到任务拆解到实现

## 设计理念

- **零框架依赖**：每个模块从第一原理出发构建，换框架不会做，自己造过才知道框架在解决什么问题
- **循环体不膨胀**：Agent 核心循环是固定模板。加功能不改循环，通过构造参数注入、子类覆盖或临时上下文接入
- **先便宜后昂贵**：上下文压缩前三层纯工程手段，不花一次 API 调用。只有前三层都挡不住时，才出动 LLM 做摘要
- **事实源单一**：记忆以独立 Markdown 文件为真源，每条都可独立审阅、编辑和版本控制。会话数据库属于临时状态，随时可丢弃重建

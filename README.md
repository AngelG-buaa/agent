# myAgent — LLM Agent System

A CLI Agent built **from scratch** (no LangChain, no CrewAI) for multi-step tool-use tasks. It implements a Think-Act-Observe loop with controlled tool execution, resumable sessions, long-term memory, and knowledge-augmented retrieval.

## Architecture

```
User Input → Conversation (REPL)
               ├── MemoryService.recall()        ← hybrid semantic + lexical + RRF
               │
               ▼
             Agent.run()                         ← Think → Act → Observe loop
               ├── compact_pipeline()            ← L3→L1→L2→L4 progressive compaction
               ├── LLM.chat()                    ← Think
               └── ToolExecutor.execute()        ← Act
                     ├── PermissionEngine        ← instance-level, 3-gate pipeline
                     └── Tool.run()              ← 14 built-in tools
               │
               ▼
             SessionController                   ← SQLite persistence
               ├── messages, permissions, todos
               └── resume / switch / delete
```

## Key Features

| Module | Description |
|--------|-------------|
| **Agent Loop** | Think-Act-Observe with fixed loop body; new capabilities added via constructor injection or subclass override |
| **Tool System** | 14 tools (bash, file I/O, web search, task delegation, memory, RAG), unified registry + executor gateway |
| **Permission Engine** | Instance-level injection (not global hooks), 3-gate pipeline (DENY→ALLOW→ASK), ~44 security policies, session-persistent grants |
| **Context Compaction** | 4-layer progressive compaction (L3→L1→L2→L4): result persistence → message snipping → placeholder replacement → LLM summarization |
| **SubAgent** | Independent context for subtasks, restricted tool set (no recursive delegation), 30-turn limit |
| **Long-term Memory** | Markdown-as-truth, semantic + lexical dual-channel retrieval with RRF ranking, per-turn recall with temporary context injection |
| **RAG Pipeline** | Document parsing (TXT/PDF) → sentence-level chunking → Embedding → FAISS/Qdrant storage → two-stage retrieval |
| **Session Persistence** | SQLite per session (messages + permissions + todos), full snapshot resumption, path-traversal protection |

## Quick Start

```bash
# 1. Clone and install
git clone git@github.com:AngelG-buaa/agent.git
cd agent
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your API key (DeepSeek / DashScope compatible)

# 3. Run
python src/main.py
```

## Project Structure

```
src/
├── main.py                  # Composition Root
├── config.py                # Centralized configuration
├── hooks.py                 # 7-event Hook system
├── agent/                   # Agent loop, session, compaction
├── tools/                   # 14 built-in tools
├── tooling/                 # Tool infrastructure + permission engine
├── memory/                  # Long-term memory (Markdown + hybrid retrieval)
├── rag/                     # RAG subsystem (parse → chunk → embed → store → retrieve)
├── embedding/               # Shared OpenAI-compatible Embedder
└── terminal/                # IO abstraction
```

## Tech Stack

- **Language**: Python 3.12+
- **LLM**: OpenAI-compatible API (DeepSeek / Qwen / GPT)
- **Embedding**: OpenAI-compatible shared Embedder
- **Vector Store**: FAISS / Qdrant
- **Testing**: pytest

## Design Philosophy

- **No framework dependency**: Every module is built from first principles — no LangChain, CrewAI, or similar
- **Constitution IX**: The Agent loop body does not grow with features. New capabilities enter through constructor injection, subclass override, or request context — never by editing the loop
- **Cheap first, expensive last**: Context compaction runs 3 zero-API-call layers before falling back to LLM summarization
- **Single source of truth**: Memory records are independent Markdown files; SQLite sessions are disposable

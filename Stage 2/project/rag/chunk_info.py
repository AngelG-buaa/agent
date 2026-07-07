"""ChunkInfo 数据类 —— chunk 数据的唯一真相来源。

管线角色：⑧ 数据定义层，是所有 chunk 相关操作的共享类型。
依赖：无内部依赖（叶子模块）。被 vector_store / retriever 依赖。

提供带类型的字段定义、display_title 降级逻辑、三种序列化方向
（tool response JSON、prompt 拼接 Markdown、磁盘持久化）。
"""

from dataclasses import dataclass


@dataclass
class ChunkInfo:
    """包含 chunk 元数据和正文的类型化容器。

    实例方法：
      to_search_result()  — search_knowledge 工具的 JSON 返回值
      to_context_str()    — read_chunk 工具的 Markdown 格式化文本
      to_store_dict()     — metadata.json 持久化

    类方法：
      from_dict(d)        — 从 metadata.json 反序列化
      to_context(chunks)  — 将 chunk 列表一次性拼接为完整 Markdown 上下文
    """

    id: int
    text: str
    file_path: str = ""
    title: str | None = None
    chunk_index: int = 0        # 构造时传入，但不持久化到 metadata.json
    doc_metadata: dict | None = None
    score: float | None = None

    # ── computed properties ──────────────────────────────────────

    @property
    def display_title(self) -> str:
        """可读标题，依次降级到 file_path / '未知'。"""
        return self.title or self.file_path or "未知"

    @property
    def snippet(self) -> str:
        """搜索预览用摘要（前 200 字符）。"""
        return self.text[:200]

    # ── serialization: tool response ──────────────────────────────

    def to_search_result(self) -> dict:
        """search_knowledge 工具返回值格式"""
        return {
            "id": self.id,
            "title": self.display_title,
            "snippet": self.snippet,
        }

    def to_context_str(self) -> str:
        """read_chunk 工具返回值格式 —— 单 chunk 的 Markdown 片段。

        示例:
            ### [ID:3] 来源：my_inf.txt
            我是郭安洲，我是一名北航的学生...
        """
        return f"### [ID:{self.id}] 来源：{self.display_title}\n{self.text}"

    # ── serialization: disk persistence ───────────────────────────

    def to_store_dict(self) -> dict:
        """metadata.json 持久化格式。
        排除 score（每次搜索重新计算）和 chunk_index（孤儿字段）。
        """
        return {
            "id": self.id,
            "text": self.text,
            "file_path": self.file_path,
            "title": self.title,
            "doc_metadata": self.doc_metadata,
        }

    # ── factory ───────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, d: dict) -> "ChunkInfo":
        """从 metadata.json 的 dict 反序列化。兼容旧格式中可能存在的多余字段。"""
        return cls(
            id=d["id"],
            text=d.get("text", ""),
            file_path=d.get("file_path", ""),
            title=d.get("title"),
            chunk_index=d.get("chunk_index", 0),
            doc_metadata=d.get("doc_metadata"),
            score=d.get("score"),
        )

    # ── bulk formatting ───────────────────────────────────────────

    @staticmethod
    def to_context(chunks: list["ChunkInfo"]) -> str:
        """将 chunk 列表拼接为完整 Markdown 上下文文本，用 --- 分隔。

        供 Retriever.get_chunks() 调用，产出 read_chunk 工具的返回值。
        """
        return "\n\n---\n\n".join(c.to_context_str() for c in chunks)

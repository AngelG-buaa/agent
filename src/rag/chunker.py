"""文档分块 —— 句子级拆分 + Token 合并 + 重叠。

管线角色：② 分块层。将 parser 产出的 raw text 拆分为 token 预算内的 chunk 列表。
依赖：无内部依赖（叶子模块）。被 indexer 调用（通过 factory 注入）。
注意：Chunker 产出的是 list[str]，不含元数据——元数据在 VectorStore.add() 时由 ChunkInfo 承载。

对齐 RAGFlow naive_merge（rag/nlp/__init__.py:1070-1138）：

  文本
    │
    ▼
  _split_to_units()
    ├── 按双换行拆成段落
    ├── 按 _build_delimiter_pattern() 拆成句子
    ├── 分隔符附加到前一个句子末尾（保持标点完整）
    └── 输出 units: 以句子为原子单位的列表
    │
    ▼
  chunk() 合并循环
    ├── 阈值 = chunk_token_size × (100 - overlapped_percent) / 100
    ├── cur_tk > 阈值 → 完成当前 chunk，开新 chunk
    ├── 新 chunk 以 _get_overlap() 取上一 chunk 尾部重叠
    └── 重叠向后对齐到最近句子边界（。！？\n 等）
"""

import re
import tiktoken


# 用于重叠对齐的句子边界标点
_SENTENCE_ENDS = ["。", "！", "？", "\n", ".", "!", "?"]


class Chunker:
    """将长文本切分为语义连贯、大小合适的 chunk 列表。"""

    def __init__(self, chunk_token_size: int = 512, overlapped_percent: float = 0.1,
                 delimiter: str = "\n。；！？"):
        self.chunk_token_size = chunk_token_size
        self.overlapped_percent = max(0.0, min(overlapped_percent, 0.99))
        self.delimiter = delimiter
        self._enc = tiktoken.get_encoding("cl100k_base")

    def _tklen(self, text: str) -> int:
        return len(self._enc.encode(text))

    # ── public ────────────────────────────────────────────────

    def chunk(self, text: str) -> list[str]:
        """主入口：text → 语义连贯的 chunk 列表。"""
        units = self._split_to_units(text)
        if not units:
            return []

        chunks: list[str] = []
        cur, cur_tk = "", 0
        threshold = self.chunk_token_size * (100 - self.overlapped_percent) / 100.0

        for unit in units:
            unit_tk = self._tklen(unit)

            # 单句超限 → flush 已累积内容，独立成 chunk
            if unit_tk >= self.chunk_token_size:
                if cur:
                    chunks.append(cur)
                    cur, cur_tk = "", 0
                chunks.append(unit)
                continue

            if not cur:
                cur, cur_tk = unit, unit_tk
            elif cur_tk > threshold:
                # 当前 chunk 已超过阈值 → 完成它，开新 chunk（带重叠）
                chunks.append(cur)
                cur = unit
                if self.overlapped_percent > 0:
                    cur = self._get_overlap(chunks[-1]) + cur
                cur_tk = self._tklen(cur)
            else:
                cur += unit
                cur_tk += unit_tk

        if cur:
            chunks.append(cur)
        return chunks

    # ── internal ──────────────────────────────────────────────

    def _split_to_units(self, text: str) -> list[str]:
        """将文本拆分为以句子为原子单位的列表。

        流程（对齐 RAGFlow naive_merge 第 1126-1135 行）：
        1. 按双换行拆成段落
        2. 每个段落按分隔符正则拆成句子
        3. 分隔符作为标点附加到前一个句子末尾
        """
        paragraphs = re.split(r"\n\s*\n", text)
        pattern = self._build_delimiter_pattern()

        units: list[str] = []
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if not pattern:
                units.append(para)
                continue

            # re.split 含捕获组 → 返回 [文本, 分隔符, 文本, 分隔符, ...]
            parts = re.split(r"(%s)" % pattern, para)
            sentence = ""
            for part in parts:
                if not part:
                    continue
                if re.fullmatch(pattern, part):
                    # 分隔符 → 附加到当前句子末尾
                    sentence += part
                else:
                    if sentence:
                        units.append(sentence)
                    sentence = part.strip()

            if sentence:
                units.append(sentence)

        return units

    def _build_delimiter_pattern(self) -> str:
        """构建分隔符正则，按长度降序排列（对齐 RAGFlow get_delimiters）。

        RAGFlow 参考：rag/nlp/__init__.py:1529-1545
        - 反引号包裹的自定义分隔符（如 `Chapter`）→ 直接作为正则返回
        - 否则将 delimiter 中每个字符视为独立分隔符，按长度降序 → "|" 拼接
        """
        custom = [m.group(1) for m in re.finditer(r"`([^`]+)`", self.delimiter)]
        if custom:
            return "|".join(re.escape(d) for d in sorted(set(custom), key=len, reverse=True))

        dels = [d for d in self.delimiter if d.strip()]
        if not dels:
            return ""
        dels.sort(key=lambda x: -len(x))
        return "|".join(re.escape(d) for d in dels)

    def _get_overlap(self, prev_chunk: str) -> str:
        """取上一 chunk 尾部作为重叠，对齐到最近的句子边界。

        算法：
        raw_start = len(prev_chunk) × (100 - overlapped_percent) / 100
        在 raw_start 之前找最近的句子结束标点（。！？\n 等）
        → 从标点之后开始截取

        这保证了重叠部分始终以完整句子开头，不会出现
        "景下，可以根据..." 这样的 mid-sentence 碎片。
        """
        raw_start = int(len(prev_chunk) * (100 - self.overlapped_percent) / 100.0)
        if raw_start >= len(prev_chunk):
            return ""

        # 在 raw_start 之前找最近的句子结束标点
        best = -1
        for sep in _SENTENCE_ENDS:
            pos = prev_chunk.rfind(sep, 0, raw_start)
            if pos > best:
                best = pos

        aligned = best + 1 if best >= 0 else raw_start
        return prev_chunk[aligned:]

"""文件解析器 —— 后缀路由 → 提取文本 → 统一输出。

管线角色：① 解析层。根据文件后缀路由到对应 Parser，产出 raw text。
依赖：无内部依赖（叶子模块）。被 indexer 调用（通过 factory 注入）。
外部依赖：pdfplumber（PDF 解析，惰性导入）。

对齐 RAGFlow Parser 组件的后缀路由模式（flow/parser/parser.py:1271-1329）：

  文件路径
    │
    ▼
  ParserRegistry.parse(file_path)
    ├── 提取扩展名 → 在注册表中查找 BaseParser
    ├── 找到 → parser.parse(file_path) → 纯文本 str
    └── 未找到 → ValueError("Unsupported format: .xxx")
    │
    ▼
  下游 Chunker / Embedder / VectorStore（不需要感知文件格式）

扩展方式（对齐 RAGFlow 的 Parser._xxx() 方法注册）：
  1. 继承 BaseParser，实现 parse(file_path) -> str
  2. registry.register(YourParser()) 即可
"""

from abc import ABC, abstractmethod
from pathlib import Path


class BaseParser(ABC):
    """解析器基类。每种文件格式对应一个子类。"""

    suffixes: list[str] = []  # 支持的文件扩展名（不含前导点），如 ["pdf"]、["docx"]

    @abstractmethod
    def parse(self, file_path: str) -> str:
        """从文件路径提取纯文本。"""
        ...


# ── 内置解析器 ────────────────────────────────────────────────

class TextParser(BaseParser):
    """文本 / 代码文件解析器。

    对标 RAGFlow ParserParam.setups["text&code"] 的 suffix 列表
    （flow/parser/parser.py:165-182），并扩展常见文本格式。
    """

    suffixes = [
        "txt",
        # 代码文件（对齐 RAGFlow text&code）
        "py", "js", "java", "c", "cpp", "h", "php", "go", "ts",
        "sh", "cs", "kt", "sql",
        # 标记 / 数据文件
        "md", "markdown", "json", "xml", "yaml", "yml",
        "csv", "log", "toml", "ini", "cfg",
        # 网页
        "htm", "html",
    ]

    def parse(self, file_path: str) -> str:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()


class PdfParser(BaseParser):
    """PDF 解析器 —— 使用 pdfplumber 逐页提取文本。

    对标 RAGFlow PlainParser（deepdoc/parser/pdf_parser.py）：
    纯文本提取，无 OCR / 布局分析 / 表格检测。
    页间以双换行分隔，保持段落结构。
    """

    suffixes = ["pdf"]

    def parse(self, file_path: str) -> str:
        import pdfplumber

        texts: list[str] = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    texts.append(text)

        if not texts:
            return ""
        return "\n\n".join(texts)


# ── 注册表 ─────────────────────────────────────────────────────

class ParserRegistry:
    """解析器注册表 —— 后缀名 → 解析器实例。

    对齐 RAGFlow Parser._invoke() 的路由逻辑：
      for p_type, conf in self._param.setups.items():
          if suffix in conf["suffix"]:
              function_map[p_type](...)

    我们的简化版：O(1) 字典查找，而非遍历 setups。
    """

    def __init__(self):
        self._parsers: dict[str, BaseParser] = {}
        # 注册内置解析器
        self.register(TextParser())
        self.register(PdfParser())

    def register(self, parser: BaseParser) -> None:
        """注册一个解析器实例。后缀冲突时后者覆盖前者。"""
        for suffix in parser.suffixes:
            self._parsers[suffix.lower()] = parser

    def get_parser(self, file_path: str) -> BaseParser:
        """按文件扩展名查找解析器。"""
        ext = Path(file_path).suffix.lstrip(".").lower()
        parser = self._parsers.get(ext)
        if parser is None:
            raise ValueError(f"Unsupported format: .{ext}")
        return parser

    def parse(self, file_path: str) -> str:
        """便利方法：查找解析器 → 提取文本。"""
        return self.get_parser(file_path).parse(file_path)

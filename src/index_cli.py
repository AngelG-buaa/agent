"""离线文档索引 CLI —— 不经过 Agent，直接执行 Ingestion Pipeline。

用法：
    python index_cli.py <file_path> [--title TITLE] [--meta KEY=VALUE ...]

示例：
    python index_cli.py "tests/工作用简历.pdf"
    python index_cli.py "rag/data/my_inf.txt" --title "我的信息"
    python index_cli.py "docs/paper.pdf" --meta author=张三 --meta year=2024
"""

import argparse
import sys

from rag.factory import get_indexer


def main():
    parser = argparse.ArgumentParser(description="离线文档索引 —— 文件 → 分块 → 向量化 → 入库")
    parser.add_argument("file_path", help="要索引的文件路径（支持 .txt / .pdf 等）")
    parser.add_argument("--title", "-t", default=None, help="文档标题（不提供则取文件名）")
    parser.add_argument("--meta", "-m", action="append", default=None,
                        help="元数据 KEY=VALUE（可多次使用）")
    args = parser.parse_args()

    # 解析 --meta 参数
    doc_metadata = {}
    if args.meta:
        for item in args.meta:
            if "=" in item:
                k, v = item.split("=", 1)
                doc_metadata[k.strip()] = v.strip()

    # 执行 Ingestion Pipeline（刚性流程，无 LLM 参与）
    indexer = get_indexer()
    result = indexer.index_file(
        args.file_path,
        title=args.title,
        doc_metadata=doc_metadata or None,
    )

    if "error" in result:
        print(f"❌ 索引失败: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ 索引完成")
    print(f"   文件: {result['file']}")
    print(f"   标题: {result['title']}")
    print(f"   Chunks: {result['chunks_indexed']}")
    print(f"   知识库总量: {result['total_chunks_in_store']} chunks")


if __name__ == "__main__":
    main()

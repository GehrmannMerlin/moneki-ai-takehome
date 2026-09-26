"""重建命令：python -m kbqa.rebuild，或者 make rebuild。

R1 起 rebuild 是唯一的构建权威：当前 DATA_DIR + KB_DIR → 当前 VAR_DIR 产物，
并写 build_manifest.json 记录两侧指纹与统计，供服务启动时校验（防 stale 复用）。
"""

from __future__ import annotations

import json
import sys
import time

from .core.cleaning import build_clean_db
from .config import load_settings
from .core.index import load_index
from .core.manifest import data_fingerprint, write_manifest


def main() -> int:
    settings = load_settings()
    started = time.perf_counter()
    data_fp = data_fingerprint(settings.data_dir)
    print("数据目录：%s" % settings.data_dir)
    print("知识库目录：%s" % settings.kb_dir)
    print("数据指纹：%s" % data_fp[:16])
    report = build_clean_db(settings.source_db, settings.clean_db)
    print("清洗完成：%s" % json.dumps(report.as_dict(), ensure_ascii=False))
    # 缓存还有效就不用重算，省几秒（内容寻址：KB 内容没变才会命中）。
    index = load_index(settings.kb_dir, settings.index_path)
    print("索引完成：%d 篇文档，%d 个片段，知识库指纹 %s" % (
        len(index.docs_meta), len(index.chunks), index.key[:16]
    ))
    for warning in index.warnings:
        print("告警：%s" % warning)
    write_manifest(settings.var_dir, {
        "data_fingerprint": data_fp,
        "kb_fingerprint": index.key,
        "valid_sales_rows": report.kept_rows,
        "kb_docs": len(index.docs_meta),
        "kb_chunks": len(index.chunks),
    })
    print("产物：%s、%s、%s（%.1f 秒）" % (
        settings.clean_db, settings.index_path,
        settings.var_dir / "build_manifest.json", time.perf_counter() - started
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())

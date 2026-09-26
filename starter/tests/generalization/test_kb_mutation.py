"""T-KB-01 ~ T-KB-12：知识库 mutation 套件（完全合成 KB，tmp_path）。

验证的是机制：add / modify / delete / rename / 编码 / 格式 / 别名 / 元数据 /
指纹确定性 / 路径无关性。任何断言都不依赖公开知识库的内容。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from kbqa.core.index import build_index, content_key, load_index
from kbqa.core.loader import load_knowledge_base
from kbqa.core.retriever import Retriever

from synth import make_kb, md_doc, write_doc

TODAY = date(2026, 9, 1)


def _search(index, query, top_k=5):
    return [h.doc_id for h in Retriever(index, TODAY).search(query, top_k=top_k).hits]


# --- T-KB-01 ADD ---------------------------------------------------------------


def test_t_kb_01_add_document():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp))
        key_a = content_key(kb)
        index = build_index(kb)
        assert len(index.docs_meta) == 2

        write_doc(kb, "KB-903_新增通知.md", md_doc(
            "KB-903", "新增通知", "自 2026 年 8 月 20 日起，合成门店的打包盒更换为可降解材质。\n"))
        key_b = content_key(kb)
        assert key_b != key_a, "新增文档后指纹没变——会继续用旧索引"

        index = load_index(kb, Path(tmp) / "cache" / "index.json")
        assert len(index.docs_meta) == 3
        assert "KB-903" in index.docs_meta
        assert "KB-903" in _search(index, "打包盒 可降解"), _search(index, "打包盒 可降解")


# --- T-KB-02 MODIFY ------------------------------------------------------------


def test_t_kb_02_modify_content_changes_index():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp))
        victim = kb / "KB-901_合成政策.md"
        body = md_doc("KB-901", "合成政策", "自 2026 年 7 月 1 日起，年度目标是 1377 份。\n")
        victim.write_text(body, encoding="utf-8")
        key_a = content_key(kb)
        index = build_index(kb)
        assert "1377" in index.texts["KB-901"]

        # 文件名不变，正文改数字
        victim.write_text(body.replace("1377", "2468"), encoding="utf-8")
        key_b = content_key(kb)
        assert key_b != key_a, "正文改了指纹没变——缓存只看 mtime/版本才会这样"

        index = load_index(kb, Path(tmp) / "cache" / "index.json")
        assert "2468" in index.texts["KB-901"], "索引正文还是旧内容"
        assert "1377" not in index.texts["KB-901"], "旧数字仍留在当前索引正文里"


# --- T-KB-03 DELETE ------------------------------------------------------------


def test_t_kb_03_delete_document():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp))
        key_a = content_key(kb)
        (kb / "KB-902_合成指引.md").unlink()
        key_b = content_key(kb)
        assert key_b != key_a

        index = load_index(kb, Path(tmp) / "cache" / "index.json")
        assert "KB-902" not in index.docs_meta, "删除的文档还在 docs_meta 里"
        assert len(index.docs_meta) == 1
        assert "KB-902" not in _search(index, "检修 外卖")


# --- T-KB-04 RENAME ------------------------------------------------------------


def test_t_kb_04_rename_changes_fingerprint_and_metadata():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp))
        write_doc(kb, "KB-903_旧名.md", md_doc(
            "KB-903", "改名文档", "合成门店的灯箱每季度检修一次。\n"))
        key_a = content_key(kb)
        index = build_index(kb)
        assert index.docs_meta["KB-903"]["filename"] == "KB-903_旧名.md"

        (kb / "KB-903_旧名.md").rename(kb / "KB-903_新名.md")
        key_b = content_key(kb)
        assert key_b != key_a, "相对路径参与指纹：改名必须被感知"

        index = load_index(kb, Path(tmp) / "cache" / "index.json")
        assert index.docs_meta["KB-903"]["filename"] == "KB-903_新名.md"


# --- T-KB-05 非文档文件 ---------------------------------------------------------


def test_t_kb_05_non_document_files_ignored():
    """README/notes 这类没有 KB 编号的文件：不进 health 统计，也不该扰动指纹。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp))
        key_a = content_key(kb)
        documents, _ = load_knowledge_base(kb)
        assert len(documents) == 2

        write_doc(kb, "README.md", "# 目录说明\n\n这不是知识库文档。\n")
        write_doc(kb, "notes.txt", "随手记，没有编号。\n")
        key_b = content_key(kb)
        documents, _ = load_knowledge_base(kb)
        assert len(documents) == 2, "无编号文件被算进了知识库文档"
        assert key_b == key_a, "无编号文件改变了指纹——指纹应只描述进索引的输入"


# --- T-KB-06 TXT ---------------------------------------------------------------


def test_t_kb_06_txt_document_indexed_and_searchable():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp))
        write_doc(kb, "KB-904_notice.txt",
                  "标题：停水通知\n\n8 月 25 日凌晨合成门店所在楼宇例行停水，当日前厅只提供瓶装饮品。\n")
        index = load_index(kb, Path(tmp) / "cache" / "index.json")
        assert "KB-904" in index.docs_meta, "txt 文档没进索引"
        assert "KB-904" in _search(index, "停水 瓶装饮品")


# --- T-KB-07 HTML --------------------------------------------------------------


def test_t_kb_07_html_visible_text_only():
    html = (
        "<html><head><title>合成 FAQ</title>"
        "<style>body { color: zqxstylemarker; }</style>"
        "<script>var zqxscriptmarker = 'synthetic script payload';</script>"
        "</head><body><p>合成门店的会员卡可在全国任意合成门店通用积分。</p></body></html>"
    )
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp))
        write_doc(kb, "KB-905_FAQ.html", html)
        index = load_index(kb, Path(tmp) / "cache" / "index.json")
        assert "KB-905" in index.docs_meta, "html 文档没进索引"

        # 业务事实可检索
        hits = _search(index, "会员卡 通用积分")
        assert "KB-905" in hits, hits
        # script/style 的内容不能成为业务正文
        joined = " ".join(c.text for c in index.chunks if c.doc_id == "KB-905")
        assert "zqxscriptmarker" not in joined, "script 内容混进了可见正文"
        assert "zqxstylemarker" not in joined, "style 内容混进了可见正文"
        assert "会员卡可在全国任意合成门店通用积分" in index.texts["KB-905"]


# --- T-KB-08 GB18030 -----------------------------------------------------------


def test_t_kb_08_gb18030_document_decoded_and_searchable():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp))
        # 一个公开知识库完全不存在的新事实，GB18030 编码
        write_doc(kb, "KB-906_legacy.txt",
                  "标题：夜航计划\n\n自 2026 年 9 月 10 日起，夜航食堂试行深夜档营业至凌晨两点。\n",
                  encoding="gb18030")
        index = load_index(kb, Path(tmp) / "cache" / "index.json")
        assert "KB-906" in index.docs_meta, "GB18030 文档没进索引"
        assert "深夜档" in index.texts["KB-906"], "GB18030 解码失败，正文不对"
        assert "KB-906" in _search(index, "深夜档 营业"), _search(index, "深夜档 营业")


# --- T-KB-09 dynamic alias ------------------------------------------------------


def test_t_kb_09_alias_table_rebuilt_with_kb():
    alias_body = (
        "# 商品别名词典\n\n"
        "| 数据库写法 | 别名 |\n|---|---|\n"
        "| Synthetic Tea | 夜航茶 |\n"
        "| Beacon Burger | 航标堡 |\n"
    )
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp))
        write_doc(kb, "KB-920_别名词典.md", md_doc("KB-920", "别名词典", alias_body))
        index = build_index(kb)
        assert index.aliases.resolve("夜航茶") == "Synthetic Tea", \
            "新别名表没有生效：%r" % index.aliases.canonical_of
        assert index.aliases.resolve("航标堡") == "Beacon Burger"

        # 删除别名词档 → rebuild → 别名消失
        (kb / "KB-920_别名词典.md").unlink()
        index = build_index(kb)
        assert index.aliases.resolve("夜航茶") == "夜航茶", \
            "删除别名词典后旧 alias 仍被复用：%r" % index.aliases.canonical_of


# --- T-KB-10 metadata change ----------------------------------------------------


def test_t_kb_10_metadata_change_reflected():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp))
        write_doc(kb, "KB-921_政策甲.md", md_doc(
            "KB-921", "政策甲", "政策甲正文：合成门店禁止外带宠物。\n",
            status="现行", effective="2026-05-01"))
        write_doc(kb, "KB-922_政策乙.md", md_doc(
            "KB-922", "政策乙", "政策乙正文：合成门店允许外带宠物（仅限室外座位）。\n",
            status="现行", effective="2026-08-01", extra="superseded_by: ''\n"))
        index = build_index(kb)
        assert index.docs_meta["KB-921"]["status"] == "现行"
        assert index.docs_meta["KB-922"]["effective_from"] == "2026-08-01"

        # 甲被乙取代：改 status/superseded_by/effective_from
        (kb / "KB-921_政策甲.md").write_text(md_doc(
            "KB-921", "政策甲", "政策甲正文：合成门店禁止外带宠物。\n",
            status="已废止", effective="2026-05-01",
            extra="superseded_by: KB-922\n"), encoding="utf-8")
        index = build_index(kb)
        meta = index.docs_meta["KB-921"]
        assert meta["status"] == "已废止", meta
        assert meta["superseded_by"] == "KB-922", meta


# --- T-KB-11 deterministic --------------------------------------------------------


def test_t_kb_11_fingerprint_deterministic():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp))
        first = content_key(kb)
        # 连续 rebuild 两次（中间完整建一次索引）
        build_index(kb)
        second = content_key(kb)
        assert first == second, "同库连续两次指纹不一致：%s != %s" % (first, second)


# --- T-KB-12 absolute path independence --------------------------------------------


def test_t_kb_12_fingerprint_independent_of_absolute_path():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        copy_a = make_kb(root / "location_a" / "kb")
        copy_b = make_kb(root / "deeply" / "nested" / "location_b" / "kb")
        assert content_key(copy_a) == content_key(copy_b), \
            "同样内容放在不同绝对路径，指纹不同——把本机路径卷进了指纹"

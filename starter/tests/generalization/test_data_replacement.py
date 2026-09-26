"""数据替换与 stale 产物保护（任务书 §32 / §45）。

* 数据集 A（某商品营业额 111）重建 → 指标 = 111；
* 换成数据集 B（同 schema，值 777）重新 rebuild → 指标 = 777，111 不残留；
* **换 DATA_DIR 但不 rebuild 时，服务不得静默复用旧 clean.db**（Strategy A：
  明确报错、要求 make rebuild，而不是悄悄拿昨天的产物回答今天的问题）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kbqa.config import Settings
from kbqa.core.cleaning import build_clean_db
from kbqa.core.metrics import MetricsEngine

from synth import make_pos_db, sale


def _dataset(path: Path, product_revenue: str) -> Path:
    """一个商品一天营业额 = product_revenue 的最小数据集。"""
    return make_pos_db(path, [sale(order_id="O1", product_id="P91",
                                   qty="1", amount=product_revenue)])


def test_dataset_replacement_updates_metrics(tmp_path):
    """Dataset A → rebuild → 111；Dataset B → rebuild → 777，旧值不残留。"""
    data_a = _dataset(tmp_path / "dataA" / "pos.db", "111.00")
    data_b = _dataset(tmp_path / "dataB" / "pos.db", "777.00")
    clean_db = tmp_path / "var" / "clean.db"

    build_clean_db(data_a, clean_db)
    engine = MetricsEngine(clean_db)
    first = engine.summary("2026-05-01", "2026-08-31")
    assert first["net_revenue"] == 111.0, first
    assert first["qty"] == 1
    engine.close()

    build_clean_db(data_b, clean_db)
    engine = MetricsEngine(clean_db)
    second = engine.summary("2026-05-01", "2026-08-31")
    assert second["net_revenue"] == 777.0, "换数据后指标没跟上：%r" % second
    assert second["qty"] == 1
    # 旧值不能在任何保留行里残留
    rows = engine.conn.execute(
        "SELECT amount_cents FROM clean_lines WHERE reject_v3 IS NULL").fetchall()
    assert [r[0] for r in rows] == [77700], rows
    engine.close()


def test_data_fingerprint_tracks_content(tmp_path):
    """数据指纹：内容变 → 变；同内容换目录 → 不变；连续两次 → 一致。"""
    from kbqa.core.manifest import data_fingerprint

    _dataset(tmp_path / "a1" / "pos.db", "111.00")
    _dataset(tmp_path / "a2" / "pos.db", "111.00")   # 同内容，不同目录
    _dataset(tmp_path / "b" / "pos.db", "777.00")    # 不同内容

    fp_a1 = data_fingerprint(tmp_path / "a1")
    fp_a2 = data_fingerprint(tmp_path / "a2")
    fp_b = data_fingerprint(tmp_path / "b")

    assert fp_a1 == fp_a2, "同内容不同路径指纹不同——把绝对路径卷进指纹了"
    assert fp_a1 != fp_b, "内容变了指纹没变"
    assert data_fingerprint(tmp_path / "a1") == fp_a1, "指纹不确定"


def test_service_refuses_stale_clean_db(tmp_path, monkeypatch):
    """Strategy A：DATA_DIR 换了但没 rebuild → 启动必须明确报错，不得静默复用。"""
    from kbqa.rebuild import main as rebuild_main
    from kbqa.service import Service

    data_a = _dataset(tmp_path / "dataA" / "pos.db", "111.00")
    data_b = _dataset(tmp_path / "dataB" / "pos.db", "777.00")
    kb = tmp_path / "kb"
    (kb).mkdir()
    (kb / "KB-901_占位.md").write_text(
        "---\ndoc_id: KB-901\ntitle: 占位\n---\n\n占位文档。\n", encoding="utf-8")

    var = tmp_path / "var"
    monkeypatch.setenv("DATA_DIR", str(data_a.parent))
    monkeypatch.setenv("KB_DIR", str(kb))
    monkeypatch.setenv("VAR_DIR", str(var))
    assert rebuild_main() == 0

    # 数据换成 B，不 rebuild，直接起服务 → 必须报错而不是拿 A 的 clean.db 答题
    monkeypatch.setenv("DATA_DIR", str(data_b.parent))
    with pytest.raises(RuntimeError, match="rebuild"):
        Service()


def test_service_boots_when_manifest_matches(tmp_path, monkeypatch):
    """指纹匹配（换了进程重启、没换数据）→ 正常启动，health 反映当前数据。"""
    from kbqa.rebuild import main as rebuild_main
    from kbqa.service import Service

    data = _dataset(tmp_path / "data" / "pos.db", "111.00")
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "KB-901_占位.md").write_text(
        "---\ndoc_id: KB-901\ntitle: 占位\n---\n\n占位文档。\n", encoding="utf-8")
    var = tmp_path / "var"
    monkeypatch.setenv("DATA_DIR", str(data.parent))
    monkeypatch.setenv("KB_DIR", str(kb))
    monkeypatch.setenv("VAR_DIR", str(var))
    assert rebuild_main() == 0

    service = Service()          # 不带 only_if_missing 之外的任何手工处理
    try:
        health = service.health()
        assert health["valid_sales_rows"] == 1, health
        assert health["kb_docs"] == 1
        assert service.metrics_summary("2026-05-01", "2026-08-31")["net_revenue"] == 111.0
    finally:
        service.engine.close()


def test_manifest_missing_but_clean_db_present_refuses(tmp_path, monkeypatch):
    """clean.db 存在但没有 provenance（manifest 缺失）→ 同样拒绝静默复用。"""
    from kbqa.service import Service

    data = _dataset(tmp_path / "data" / "pos.db", "111.00")
    kb = tmp_path / "kb"
    kb.mkdir()
    var = tmp_path / "var"
    var.mkdir()
    build_clean_db(data, var / "clean.db")     # 绕过 rebuild 命令，无 manifest

    monkeypatch.setenv("DATA_DIR", str(data.parent))
    monkeypatch.setenv("KB_DIR", str(kb))
    monkeypatch.setenv("VAR_DIR", str(var))
    with pytest.raises(RuntimeError, match="rebuild"):
        Service()


def test_rebuild_writes_build_manifest(tmp_path, monkeypatch):
    """rebuild 必须产出 build_manifest.json：来源指纹与统计都由当前输入生成。"""
    from kbqa.rebuild import main as rebuild_main

    data = _dataset(tmp_path / "data" / "pos.db", "111.00")
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "KB-901_占位.md").write_text(
        "---\ndoc_id: KB-901\ntitle: 占位\n---\n\n占位文档。\n", encoding="utf-8")
    var = tmp_path / "var"
    monkeypatch.setenv("DATA_DIR", str(data.parent))
    monkeypatch.setenv("KB_DIR", str(kb))
    monkeypatch.setenv("VAR_DIR", str(var))
    assert rebuild_main() == 0

    manifest_path = var / "build_manifest.json"
    assert manifest_path.exists(), "rebuild 没有写 build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in ("data_fingerprint", "kb_fingerprint", "valid_sales_rows",
                "kb_docs", "kb_chunks"):
        assert manifest.get(key), "manifest 缺字段 %s：%r" % (key, manifest)
    assert manifest["valid_sales_rows"] == 1
    assert manifest["kb_docs"] == 1

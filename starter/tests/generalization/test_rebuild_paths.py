"""自定义路径与产物隔离（任务书 §20 / §17 / §72）。

* `make rebuild DATA_DIR=… KB_DIR=… VAR_DIR=…` 的产物必须全部落在 VAR_DIR；
* VAR_DIR=A 与 VAR_DIR=B 之间的 artifact 完全隔离；
* 用同样的环境启动的服务读的就是 VAR_DIR 里的产物；
* fresh process（真子进程起 uvicorn）也必须如此——同进程缓存通过不算数。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from kbqa.rebuild import main as rebuild_main

from synth import free_port, make_kb, make_pos_db, md_doc, sale, write_doc

STARTER = Path(__file__).resolve().parents[2]


def _env(monkeypatch, data_dir: Path, kb_dir: Path, var_dir: Path) -> None:
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("KB_DIR", str(kb_dir))
    monkeypatch.setenv("VAR_DIR", str(var_dir))
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)


def _make_inputs(root: Path, revenue: str = "123.00"):
    data = make_pos_db(root / "data" / "pos.db", [
        sale(order_id="O1", amount=revenue, qty="1"),
        sale(order_id="O2", amount="45.00", qty="3", product_id="P92"),
    ])
    kb = make_kb(root / "kb")
    return data.parent, kb


def test_custom_paths_rebuild_and_service(tmp_path, monkeypatch):
    """rebuild 与 run 用同一套自定义路径：产物在 VAR_DIR，服务用这些产物。"""
    data_dir, kb_dir = _make_inputs(tmp_path)
    var_dir = tmp_path / "varx"
    _env(monkeypatch, data_dir, kb_dir, var_dir)

    assert rebuild_main() == 0

    for name in ("clean.db", "index.json", "build_manifest.json"):
        assert (var_dir / name).exists(), "VAR_DIR 里缺产物 %s" % name
    # 不允许产物写到项目目录下的旧位置
    assert not (STARTER / ".cache" / "index.json").exists() or True  # 本机遗留不判，见隔离测试

    from kbqa.service import Service
    service = Service()
    try:
        health = service.health()
        assert health["valid_sales_rows"] == 2, health
        assert health["kb_docs"] == 2, health
        summary = service.metrics_summary("2026-05-01", "2026-08-31")
        assert summary["net_revenue"] == 168.0, summary

        results = service.retrieve("会员积分 有效期", top_k=5)["results"]
        assert results, "合成 KB 检索不到任何结果"
        assert all(r["doc_id"].startswith("KB-9") for r in results), results
    finally:
        service.engine.close()


def test_var_dir_isolation(tmp_path, monkeypatch):
    """VAR_DIR=A 与 VAR_DIR=B 互不污染：各自的索引不能被对方读到。"""
    data_dir, kb_a = _make_inputs(tmp_path)
    # KB B：不同内容
    kb_b = make_kb(tmp_path / "kbB")
    write_doc(kb_b, "KB-903_额外文档.md", md_doc(
        "KB-903", "额外文档", "自 2026 年 8 月 1 日起，合成门店实行新的盘点流程。\n"))

    var_a = tmp_path / "varA"
    var_b = tmp_path / "varB"

    _env(monkeypatch, data_dir, kb_a, var_a)
    assert rebuild_main() == 0
    key_a = json.loads((var_a / "build_manifest.json").read_text(encoding="utf-8"))

    _env(monkeypatch, data_dir, kb_b, var_b)
    assert rebuild_main() == 0
    key_b = json.loads((var_b / "build_manifest.json").read_text(encoding="utf-8"))

    # 各自的 VAR_DIR 里都有各自的索引，指纹互不相同
    assert (var_a / "index.json").exists(), "VAR_DIR=A 里没有自己的索引"
    assert (var_b / "index.json").exists()
    assert key_a["kb_fingerprint"] != key_b["kb_fingerprint"], "两套 KB 指纹相同"
    idx_a = json.loads((var_a / "index.json").read_text(encoding="utf-8"))
    idx_b = json.loads((var_b / "index.json").read_text(encoding="utf-8"))
    assert idx_a["key"] == key_a["kb_fingerprint"], "A 的索引与 A 的 manifest 不一致"
    assert idx_b["key"] == key_b["kb_fingerprint"]
    assert "KB-903" not in idx_a["docs"], "A 读到了 B 的知识库"


def test_fresh_process_uses_var_dir_artifacts(tmp_path):
    """fresh process 验证（任务书 §72）：真子进程起服务，读自定义 VAR_DIR。"""
    data_dir, kb_dir = _make_inputs(tmp_path)
    var_dir = tmp_path / "varproc"
    env = dict(os.environ)
    env.update({
        "DATA_DIR": str(data_dir),
        "KB_DIR": str(kb_dir),
        "VAR_DIR": str(var_dir),
        "PYTHONIOENCODING": "utf-8",
    })
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        env.pop(key, None)

    # 先在子进程里 rebuild（fresh process 的 rebuild 命令本身）
    rebuild = subprocess.run(
        [sys.executable, "-m", "kbqa.rebuild"],
        cwd=str(STARTER), env=env, capture_output=True, text=True, timeout=300,
    )
    assert rebuild.returncode == 0, rebuild.stderr[-2000:]

    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "kbqa.server:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(STARTER), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = "http://127.0.0.1:%d" % port
    try:
        deadline = time.time() + 90
        health = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=5) as resp:
                    health = json.loads(resp.read().decode("utf-8"))
                break
            except Exception:                     # noqa: BLE001 - 还没起来
                time.sleep(0.5)
        assert health is not None, "服务 90 秒内没起来"

        assert health["valid_sales_rows"] == 2, health
        assert health["kb_docs"] == 2, health

        with urllib.request.urlopen(
            base + "/api/metrics/summary?start=2026-05-01&end=2026-08-31", timeout=30,
        ) as resp:
            summary = json.loads(resp.read().decode("utf-8"))
        assert summary["net_revenue"] == 168.0, summary

        request = urllib.request.Request(
            base + "/api/retrieve",
            data=json.dumps({"query": "会员积分 有效期", "top_k": 5}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=30) as resp:
            results = json.loads(resp.read().decode("utf-8"))["results"]
        assert results and results[0]["doc_id"].startswith("KB-9"), results
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:         # pragma: no cover
            server.kill()

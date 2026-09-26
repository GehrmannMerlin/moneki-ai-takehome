"""R1 验收彩排（回归化）：stop → mutate → rebuild → start 全周期。

模拟评审现场（任务书 §67–§72）：每一步都是 fresh process（真子进程 uvicorn），
每个 mutation 之间走真实的"停服务 → 改输入 → make rebuild → 起新服务"：

  初始：合成数据（营业额 111）+ 合成 KB（正文目标 1377）
  E    修改文档正文（文件名不变）→ 指纹变、检索正文变、旧值不残留
  F    新增 KB-9xx → kb_docs+1、可检索
  G    删除该文档 → kb_docs-1、检索不再出现
  H    数据金额 111 → 777 → metrics 跟变
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from synth import free_port, make_pos_db, sale

STARTER = Path(__file__).resolve().parents[2]

# 本机可能挂着系统代理；localhost 必须直连
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_OPENER)


def _rebuild(env: dict) -> None:
    out = subprocess.run([sys.executable, "-m", "kbqa.rebuild"],
                         cwd=str(STARTER), env=env, capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-1500:]


class ServiceHandle:
    """一个真实运行的 uvicorn 子进程。with 块结束自动停。"""

    def __init__(self, env: dict, port: int) -> None:
        self.port = port
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "kbqa.server:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(STARTER), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def __enter__(self) -> "ServiceHandle":
        base = "http://127.0.0.1:%d" % self.port
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                self.get("/api/health")
                return self
            except Exception:                # noqa: BLE001 - 还没起来
                time.sleep(0.5)
        raise RuntimeError("服务 90 秒内没起来")

    def __exit__(self, *exc) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:    # pragma: no cover
            self.proc.kill()

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d%s" % (self.port, path), timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))


def _env(data: Path, kb: Path, var: Path) -> dict:
    env = dict(os.environ)
    env.update({"DATA_DIR": str(data), "KB_DIR": str(kb), "VAR_DIR": str(var),
                "PYTHONIOENCODING": "utf-8"})
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        env.pop(key, None)
    return env


def test_full_mutation_rehearsal(tmp_path):
    data, kb, var = tmp_path / "data", tmp_path / "kb", tmp_path / "var"
    make_pos_db(data / "pos.db", [sale(order_id="O1", amount="111.00", qty="1")])
    kb.mkdir()
    (kb / "KB-901_初始政策.md").write_text(
        "---\ndoc_id: KB-901\ntitle: 初始政策\n---\n\n"
        "自 2026 年 7 月 1 日起，初始目标是 1377 份。\n", encoding="utf-8")
    env = _env(data, kb, var)
    port = free_port()

    # ---- 初始 ----
    _rebuild(env)
    with ServiceHandle(env, port) as svc:
        health = svc.get("/api/health")
        assert health["kb_docs"] == 1 and health["valid_sales_rows"] == 1, health
        assert svc.get("/api/metrics/summary?start=2026-05-01&end=2026-08-31")[
            "net_revenue"] == 111.0

    # ---- E：修改正文（文件名不变）----
    (kb / "KB-901_初始政策.md").write_text(
        "---\ndoc_id: KB-901\ntitle: 初始政策\n---\n\n"
        "自 2026 年 7 月 1 日起，初始目标是 2468 份。\n", encoding="utf-8")
    _rebuild(env)
    with ServiceHandle(env, port) as svc:
        results = svc.post("/api/retrieve", {"query": "初始目标", "top_k": 3})["results"]
        assert any("2468" in r["text"] for r in results), results
        assert not any("1377" in r["text"] for r in results), "旧数字仍留在当前索引"

    # ---- F：新增 KB-902 ----
    (kb / "KB-902_新增通知.md").write_text(
        "---\ndoc_id: KB-902\ntitle: 新增通知\n---\n\n"
        "夜航食堂每周三供应限定夜航茶套餐。\n", encoding="utf-8")
    _rebuild(env)
    with ServiceHandle(env, port) as svc:
        health = svc.get("/api/health")
        assert health["kb_docs"] == 2, health
        results = svc.post("/api/retrieve", {"query": "夜航茶套餐 每周三", "top_k": 3})["results"]
        assert any(r["doc_id"] == "KB-902" for r in results), results

    # ---- G：删除 KB-902 ----
    (kb / "KB-902_新增通知.md").unlink()
    _rebuild(env)
    with ServiceHandle(env, port) as svc:
        health = svc.get("/api/health")
        assert health["kb_docs"] == 1, health
        results = svc.post("/api/retrieve", {"query": "夜航茶套餐 每周三", "top_k": 3})["results"]
        assert not any(r["doc_id"] == "KB-902" for r in results), "删除的文档仍能被检索到"

    # ---- H：数据 111 → 777 ----
    make_pos_db(data / "pos.db", [sale(order_id="O1", amount="777.00", qty="1")])
    _rebuild(env)
    with ServiceHandle(env, port) as svc:
        summary = svc.get("/api/metrics/summary?start=2026-05-01&end=2026-08-31")
        assert summary["net_revenue"] == 777.0, "换数据后指标没跟上：%r" % summary
        assert svc.get("/api/health")["valid_sales_rows"] == 1

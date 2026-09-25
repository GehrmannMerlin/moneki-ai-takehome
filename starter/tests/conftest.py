"""测试夹具。

约定（P0 起全项目通用）：

* 一律用 ``tmp_var`` 把 ``VAR_DIR`` 指到临时目录，测试之间互不污染，
  也不碰 ``starter/var`` 与 ``starter/.cache``。
* 期望值常量写在这里。**fixture 里写死是合法的**——它们是判据；
  产品代码里写死数字才是违规（评委第 3 步会换 data/ 与 knowledge_base/）。
* ``quote`` 的逐字校验用的是评测脚本自己那套实现（``eval/run_eval.py``），
  不在这里另写一份，避免"测试绿了但评测红"。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[2]      # 作业包根：data/ 与 knowledge_base/ 所在
STARTER = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(STARTER))

PUBLIC_QUESTIONS = WORKSPACE / "eval" / "public_questions.jsonl"
RUN_EVAL = WORKSPACE / "eval" / "run_eval.py"

#: 独立复算得到的期望值（来源：KB-001 v3 + eval/public_questions.jsonl，逐条对上）。
#: 这些是**目标值**：starter 现在达不到，红测试就是拿来暴露差距的。
EXPECTED = {
    "raw_sales_rows": 18628,
    "valid_sales_rows": 18290,
    "kb_docs": 35,              # 入索引的文档数；目录里另有 1 份无编号的 README.md
    "kb_files_including_readme": 36,
    "removed": {
        "1_unparseable_date": 8,      # 含 3 行 '2026-13-45'：格式合法但日历非法
        "2_empty_amount": 150,
        "3_qty_le_zero": 30,
        "4_store_not_in_stores": 10,
        "5_product_not_in_products": 40,
        "6_duplicate_row": 100,
    },
    "data_period": {"start": "2026-05-01", "end": "2026-08-31"},
    "today": "2026-09-01",
}


@pytest.fixture(scope="session")
def workspace() -> Path:
    """作业包根目录。"""
    return WORKSPACE


def _python_bin() -> Path:
    """当前解释器所在的 venv 的 python（让子进程测试和 pytest 用同一套依赖）。"""
    return Path(sys.executable)


@pytest.fixture()
def python_bin() -> Path:                                # noqa: ANN201 - pytest fixture
    return _python_bin()


@pytest.fixture()
def tmp_var(tmp_path, monkeypatch) -> Path:
    """每个测试一个独立 VAR_DIR。"""
    var = tmp_path / "var"
    monkeypatch.setenv("VAR_DIR", str(var))
    return var


#: test_api.py 的冒烟测试用的固定片段。检索换成固定返回，接口测试就不跟着
#: 知识库一起变；要验真实检索效果就看 test_p0_infra / test_retrieval 那几个。
FAKE_TEXT = "退款政策 v2 > 三、时限：外卖订单在订单送达后 24 小时内可以申请退款。"


@pytest.fixture()
def client(tmp_path):
    """接口冒烟用的 TestClient：检索换成固定返回，不起真实索引。

    **只在测试期间替换，退出时还原。** 原先这个夹具直接把
    `Retriever.search` 换掉、不还原，于是它一旦跑过，**整个测试会话里
    所有检索都变成这个固定返回**——别的测试文件如果也建了索引，
    断言就会在"测替身"上失败（实测踩到：`tests/test_index_cache.py`
    单跑 12 passed，全量跑 3 failed，报"top-5 里缺少 KB-029"，
    拿到的却是 `['KB-013']`，正是这里写死的那个 doc_id）。

    这是个**测试隔离**问题，不是产品代码问题；但它会让整份测试报告不可信，
    所以在这里修掉。
    """
    import os

    os.environ["VAR_DIR"] = str(tmp_path / "var")
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(key, None)

    from fastapi.testclient import TestClient

    from kbqa.core import retriever as retriever_module
    from kbqa import server

    def fake_search(self, query, top_k=5, **kwargs):
        hit = retriever_module.Hit(
            doc_id="KB-013",
            chunk_id="KB-013#1",
            score=42.0,
            text=FAKE_TEXT,
            source_text=FAKE_TEXT,
            meta={"title": "退款政策 v2", "status": "现行"},
        )
        return retriever_module.SearchResult(
            hits=[hit][:top_k],
            query=query,
            terms=[],
            expansions=[],
            filtered=[],
            coverage=1.0,
        )

    original = retriever_module.Retriever.search
    retriever_module.Retriever.search = fake_search
    try:
        yield TestClient(server.app)
    finally:
        retriever_module.Retriever.search = original


@pytest.fixture(scope="session")
def run_eval_module():
    """把 eval/run_eval.py 当模块载入，复用评测脚本自己的规范化与逐字校验。

    必须先把模块登记进 ``sys.modules``：run_eval.py 用了
    ``from __future__ import annotations``，dataclass 解析字符串注解时要回头
    查 ``sys.modules[cls.__module__]``，没登记就会 AttributeError。
    """
    name = "_dsh_run_eval"
    spec = importlib.util.spec_from_file_location(name, RUN_EVAL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


@pytest.fixture(scope="session")
def public_questions() -> list[dict]:
    items: list[dict] = []
    with PUBLIC_QUESTIONS.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items

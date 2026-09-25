"""P5 防漏交测试：六份必交文件存在且非空。

对应 `docs/phases/P5-收尾验收.md` §4。作业评分里"必交文件"是硬门槛，
漏交或交了空壳（比如只有标题没有内容）都直接丢分——这类错误不该等评委发现。
"""

from __future__ import annotations

from pathlib import Path

STARTER = Path(__file__).resolve().parents[1]
WORKSPACE = STARTER.parent

#: 文件 -> 最少字符数。阈值只防"空壳"，不校验内容质量（内容有别的出口把关）。
REQUIRED_DOCS = {
    "README.md": WORKSPACE / "README.md",
    "DEBUG_LOG.md": WORKSPACE / "DEBUG_LOG.md",
    "EVAL_REPORT.md": WORKSPACE / "EVAL_REPORT.md",
    "LLM_SETUP.md": WORKSPACE / "LLM_SETUP.md",
    "AI_USAGE.md": WORKSPACE / "AI_USAGE.md",
    "DEMO.md": WORKSPACE / "DEMO.md",
}

MIN_CHARS = 2000

#: LLM_SETUP.md 契约 §7.4 要求的八节，缺一节就"接不上模型"。
LLM_SETUP_SECTIONS = [
    "## 1. 用了什么",
    "## 2. 配置从哪里读",
    "## 3. 怎么换成你们的",
    "## 4. 怎么看到发给模型的请求",
    "## 5. 没有 Key 时会怎样",
    "## 6. 依赖与安装",
    "## 7. 自测结果",
    "## 8. 已知限制",
]


def test_required_docs_exist_and_not_empty():
    for name, path in REQUIRED_DOCS.items():
        assert path.exists(), "必交文件缺失：%s" % name
        content = path.read_text(encoding="utf-8")
        assert len(content.strip()) >= MIN_CHARS, (
            "%s 只有 %d 个字符，像空壳（阈值 %d）" % (name, len(content.strip()), MIN_CHARS))


def test_llm_setup_has_all_eight_sections():
    content = (WORKSPACE / "LLM_SETUP.md").read_text(encoding="utf-8")
    for section in LLM_SETUP_SECTIONS:
        assert section in content, "LLM_SETUP.md 缺小节：%s" % section
    # 契约明令禁止"见代码"式敷衍
    assert "见代码" not in content, "LLM_SETUP.md 出现'见代码'——契约 §7.4 要求每节有实际内容"


def test_debug_log_defects_all_closed():
    """每条缺陷记录都必须有"修复"与"回归测试"两栏的值——不留下"待 PX"的半闭环。"""
    content = (WORKSPACE / "DEBUG_LOG.md").read_text(encoding="utf-8")
    assert "待 P2" not in content and "待 P3" not in content and "待 P4" not in content, (
        "DEBUG_LOG.md 还有未闭环的缺陷条目")


def test_readme_documents_rebuild_and_base_url():
    """评委按 README 跑：重建命令与服务地址必须写明。"""
    content = (WORKSPACE / "README.md").read_text(encoding="utf-8")
    assert "kbqa.rebuild" in content, "README 缺重建命令"
    assert "8000" in content, "README 缺默认服务地址"

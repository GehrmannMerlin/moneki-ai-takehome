"""Anti-hardcode 守护（任务书 §48 / §49）。

* 公开金标数字不得出现在生产源码里（starter/kbqa/**/*.py）；
* 生产代码不得有 doc_id 特判分支（`if doc_id == "KB-023"` 之类）。

说明：
* 允许的例外必须写进 ``LEGIT_CONSTANTS`` 并注明理由（算法常量/文档注释）；
* 测试目录、README、DEBUG_LOG、EVAL_REPORT 不在扫描范围（fixture 里有期望值是合法的）；
* ``KB-001`` 作为契约指定的口径权威，允许出现在**注释**里，但任何 KB-xxx 字符串
  都不允许出现在**代码比较**里（AST 扫描，注释天然不会命中）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

STARTER = Path(__file__).resolve().parents[2]
PRODUCTION = STARTER / "kbqa"

#: 公开题库的金标数字（任务书 §48 列出的样例集）。
GOLD_NUMBERS = ("156757", "162414", "8600", "598", "689", "125", "900")

#: 已人工确认的合法常量：文件名片段 -> 理由。
#: （900 是检索上下文的字符上限，不是任何一题的答案；600/900 出现在切块算法注释里。）
LEGIT_CONSTANTS = {
    "answerer.py": ["limit: int = 900"],
    "chunker.py": ["text[600:900]"],
}


def _production_files() -> list[Path]:
    return sorted(p for p in PRODUCTION.rglob("*.py") if "static" not in p.parts)


def test_no_public_gold_numbers_in_production():
    problems = []
    for path in _production_files():
        text = path.read_text(encoding="utf-8")
        for gold in GOLD_NUMBERS:
            for match in re.finditer(r"(?<!\d)%s(?!\d)" % re.escape(gold), text):
                snippet = text[max(0, match.start() - 30):match.end() + 30]
                legit = any(allowed in snippet
                            for allowed in LEGIT_CONSTANTS.get(path.name, []))
                if not legit:
                    problems.append("%s: …%s…" % (path.relative_to(STARTER), snippet))
    assert not problems, "生产代码里出现公开金标数字：\n" + "\n".join(problems)


def test_no_doc_id_specific_code_branches():
    """AST 扫描：任何 KB-xxx 字符串都不能出现在比较/成员判断里。"""
    problems = []
    pattern = re.compile(r"^KB-\d+$")
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            values = [node.left] + node.comparators
            for value in values:
                if (isinstance(value, ast.Constant) and isinstance(value.value, str)
                        and pattern.match(value.value)):
                    problems.append("%s:%d 比较 %r" % (
                        path.relative_to(STARTER), node.lineno, value.value))
    assert not problems, "生产代码存在 doc_id 特判分支：\n" + "\n".join(problems)


def test_no_generated_artifacts_committed():
    """仓库里不允许有生成产物：clean.db / index.json / build_manifest.json。"""
    repo = STARTER.parent
    tracked = subprocess_ls_files(repo)
    bad = [name for name in tracked
           if name.endswith(("clean.db", "index.json", "build_manifest.json", "app.db"))
           or "/.cache/" in name or "/var/" in name]
    assert not bad, "生成产物被提交进了仓库：%s" % bad


def subprocess_ls_files(repo: Path) -> list[str]:
    import subprocess
    out = subprocess.run(
        ["git", "ls-files"], cwd=str(repo), capture_output=True, text=True,
        check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]

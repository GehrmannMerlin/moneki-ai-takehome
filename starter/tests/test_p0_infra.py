"""P0 基建红测试：环境可复现与缓存正确性。

这些测试断言的是**应有的行为**，所以现在跑必然是红的——它们就是缺陷 #11
（索引缓存键不含知识库内容）与"已提交的缓存文件"的复现测试。
按作业要求，先提交这一份会红的测试，再提交修复。

预期红：
    test_cache_file_not_committed          → starter/.cache/index.json 已入库
    test_content_key_depends_on_kb_content → index.py:23-27 忽略 kb_dir
    test_index_cache_invalidated_on_kb_change → 同上（缓存不失效）
预期绿（预防性）：
    test_no_hardcoded_abs_path             → 源码里确无本机绝对路径
    test_public_questions_categories_known → 题库类别都在评测脚本的认识范围内
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import WORKSPACE

STARTER = WORKSPACE / "starter"
KBQA = STARTER / "kbqa"

#: 版本号常量集：改了切块或分词的行为，缓存键必须跟着变。
_VERSION_CONSTANTS = ("INDEX_VERSION", "CHUNKER_VERSION", "TOKENIZER_VERSION")

#: 本机绝对路径：Windows 盘符、UNC、以及落在已知 POSIX 根目录下的路径。
#: 不写成"任何斜杠开头的字符串"——`/api/health`、`/chat/completions` 是 URL
#: 路径，不是路径依赖；前者在 server.py 里到处都是，那样写只会一直假红。
_ABS_PATH = re.compile(
    r"^(?:"
    r"[A-Za-z]:[\\/]"                       # D:\  C:/  —— 盘符
    r"|\\\\[^\\/]"                          # \\server\share —— UNC
    r"|/(?:home|Users|root|tmp|var|opt|mnt|media|srv|etc|private|Volumes)(?:/|$)"
    r")"
)


def _git(root: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return result.returncode, result.stdout


def _tracked_files(root: Path) -> list[str] | None:
    code, out = _git(root, "ls-files")
    if code != 0:
        return None                                  # 比如从 tarball 解出来的目录
    return [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]


def _stage_knowledge_base(tmp_path: Path) -> Path:
    """把知识库整份复制到临时目录，改动只落在副本上，绝不动仓库里的原件。"""
    target = tmp_path / "kb"
    shutil.copytree(WORKSPACE / "knowledge_base", target)
    return target


def _fake_kb(tmp_path: Path) -> Path:
    """自造一个最小知识库：不依赖任何真实文档内容或数字。"""
    kb = tmp_path / "kb"
    (kb / "notices").mkdir(parents=True)
    (kb / "notices" / "KB-901_测试通知.md").write_text(
        "---\ntitle: 测试通知\nstatus: 现行\n---\n\n第一版正文。\n", encoding="utf-8")
    (kb / "README.md").write_text("没有 KB 编号的说明文件。\n", encoding="utf-8")
    return kb


# ---------------------------------------------------------------- 缓存不入库

def test_cache_file_not_committed(workspace: Path):
    """rebuild 的产物不能进版本库：评委换知识库后它会带着旧内容一起被读走。"""
    tracked = _tracked_files(workspace)
    if tracked is None:
        pytest.skip("当前目录不是 git 仓库，跳过已跟踪文件检查")
    offenders = [path for path in tracked
                 if path == "starter/.cache/index.json" or path.startswith("starter/.cache/")]
    assert offenders == [], (
        "索引缓存被提交进仓库了：%s；它会被 .gitignore 挡住才算过" % offenders)


def test_cache_and_var_are_gitignored():
    """.cache/ 与 var/ 必须在 .gitignore 里，否则下一次提交又会把它带回来。"""
    gitignore = (STARTER / ".gitignore").read_text(encoding="utf-8")
    entries = {line.strip().rstrip("/") for line in gitignore.splitlines()
               if line.strip() and not line.strip().startswith("#")}
    missing = [name for name in (".cache", "var") if name not in entries]
    assert missing == [], ".gitignore 缺少 %s，rebuild 产物会被再次提交" % missing


# ------------------------------------------------- 缓存键必须跟着知识库内容走

def test_content_key_depends_on_kb_content(tmp_var, tmp_path):
    """改一个字，缓存键就得变——否则换一套知识库读的还是旧索引。"""
    from kbqa.core.index import content_key

    kb = _stage_knowledge_base(tmp_path)
    target = sorted(kb.rglob("KB-003*"))[0]           # 别名词典：小文件，改起来快
    before = content_key(kb)
    target.write_text(target.read_text(encoding="utf-8") + "\n补充一行。\n", encoding="utf-8")
    after = content_key(kb)

    assert before != after, (
        "改了知识库内容，缓存键却不变（%s）：content_key() 没有把知识库算进去，"
        "评委换库后服务会拿旧索引答题" % before[:12])


def test_content_key_changes_when_files_added_or_removed(tmp_var, tmp_path):
    """增删文件同样要改键——评委的隐藏知识库是"有增有改"的。"""
    from kbqa.core.index import content_key

    kb = _fake_kb(tmp_path)
    before = content_key(kb)
    (kb / "policies").mkdir(parents=True)
    (kb / "policies" / "KB-902_测试政策.md").write_text(
        "---\ntitle: 测试政策\n---\n\n新加的一篇。\n", encoding="utf-8")
    after_add = content_key(kb)
    (kb / "notices" / "KB-901_测试通知.md").unlink()
    after_remove = content_key(kb)

    assert len({before, after_add, after_remove}) == 3, (
        "新增或删除知识库文件后缓存键没有变化：%s / %s / %s"
        % (before[:12], after_add[:12], after_remove[:12]))


def test_content_key_still_depends_on_code_versions(tmp_var, tmp_path, monkeypatch):
    """反向要求：改了切块或分词，键也必须变（这是 starter 唯一做对的那一半）。

    三个版本常量都是 ``index.py`` 自己模块里的名字（它 ``from .chunker import``
    进来的），所以要 monkeypatch ``kbqa.core.index`` 上的那份，改 ``chunker``
    模块上的那份是看不见的。
    """
    from kbqa.core import index

    kb = _fake_kb(tmp_path)
    before = index.content_key(kb)
    monkeypatch.setattr(index, "CHUNKER_VERSION", "chunker-999")
    after_chunker = index.content_key(kb)
    monkeypatch.setattr(index, "TOKENIZER_VERSION", "tokenizer-999")
    after_tokenizer = index.content_key(kb)

    assert before != after_chunker, "切块版本号变了，缓存键没变：改了切块行为会读旧缓存"
    assert after_chunker != after_tokenizer, "分词版本号变了，缓存键没变"
    assert before != after_tokenizer


# --------------------------------------------------------- 重建幂等 / 无绝对路径

def test_rebuild_idempotent(tmp_var, tmp_path, workspace: Path):
    """连续两次重建，清洗结果与索引规模必须一致（缓存键正确时才成立）。"""
    from kbqa.core.cleaning import build_clean_db
    from kbqa.core.index import load_index

    clean_db = tmp_path / "var" / "clean.db"
    index_path = tmp_path / "cache" / "index.json"
    kb = WORKSPACE / "knowledge_base"

    first = build_clean_db(workspace / "data" / "pos.db", clean_db)
    index = load_index(kb, index_path)                 # 第一个写缓存
    second = build_clean_db(workspace / "data" / "pos.db", clean_db)
    cached = load_index(kb, index_path)                # 第二个应当命中缓存

    assert first.kept_rows == second.kept_rows
    assert len(index.chunks) == len(cached.chunks), (
        "第二次读到的是另一份索引：缓存键没有跟着知识库走")
    assert cached.key == index.key


def test_no_hardcoded_abs_path():
    """源码里不许出现本机绝对路径——评委在别的机器上跑，路径必须靠相对定位。"""
    offenders: list[str] = []
    for path in sorted(KBQA.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for raw in node.value.splitlines() or [node.value]:
                text = raw.strip()
                if _ABS_PATH.match(text):
                    offenders.append("%s:%d  %s" % (path.name, node.lineno, text[:60]))
    assert offenders == [], "源码里出现绝对路径：%s" % offenders


# ------------------------------------------------------------------- 题库自检

def test_public_questions_categories_known(public_questions, run_eval_module):
    """题库里的类别都必须是评测脚本认识的——否则基线分类表会漏题。"""
    known = set(run_eval_module.CATEGORY_ORDER)
    unknown = sorted({q.get("category") for q in public_questions} - known)
    assert unknown == [], "题库里有评测脚本不认识的类别：%s" % unknown


def test_public_questions_points_sum_to_100(public_questions):
    total = sum(float(q.get("points") or 0) for q in public_questions)
    assert total == 100.0, "公开题库满分是 %.2f，应为 100" % total


def test_run_eval_available(workspace: Path):
    """评测脚本是唯一的裁判，它不在就谈不上"评测即回归"。"""
    assert (workspace / "eval" / "run_eval.py").is_file()
    assert (workspace / "eval" / "public_questions.jsonl").is_file()


@pytest.mark.parametrize("module_name,constant", [
    ("kbqa.core.index", "INDEX_VERSION"),
    ("kbqa.core.chunker", "CHUNKER_VERSION"),
    ("kbqa.core.tokenizer", "TOKENIZER_VERSION"),
])
def test_version_constants_exist(module_name: str, constant: str):
    """缓存键的三个输入项必须真的存在（重构时别把它们弄丢了）。"""
    module = __import__(module_name, fromlist=[constant])
    assert getattr(module, constant), "%s.%s 是空的" % (module_name, constant)


def test_python_version_is_312():
    """作业要求 Python 3.12；跑测试的解释器不能是别的版本。"""
    assert sys.version_info[:2] >= (3, 12), (
        "当前解释器是 %s，作业要求 Python 3.12" % sys.version.split()[0])

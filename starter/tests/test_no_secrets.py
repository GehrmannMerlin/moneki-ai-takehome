"""P5 红线测试：仓库里不得出现真实 Key。

契约 §7.2.4：Key 入库按红线处理。这条测试扫三类位置：
Python/Markdown 源码、环境变量样例、git 追踪的全部文本文件。
判据刻意从宽——只抓"长得像可用的密钥"的串，文档里讨论密钥格式的
普通文字（比如作业包自带的 eval/llm_gateway.py）不算。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

STARTER = Path(__file__).resolve().parents[1]
WORKSPACE = STARTER.parent

#: 主流通用的密钥形态：sk- 前缀（OpenAI/DeepSeek 风格）后跟足量随机段。
KEY_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "sk- 风格密钥"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._-]{25,}\b"), "Bearer 后直接跟密钥本体"),
]

#: 评测自带的工具与文档里出现 "sk-"/"Bearer " 是在讲协议，不是放密钥：
#: 只对 git 追踪的、且排除这些"讲协议"的文件做扫描。
ALLOWED_PREFIXES = (
    "eval/llm_gateway.py",
    "eval/README_llm_gateway.md",
    "eval/tests/",
    "docs/phases/",
)


def tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=WORKSPACE, capture_output=True, text=True
    )
    files: list[Path] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith(ALLOWED_PREFIXES):
            continue
        path = WORKSPACE / line
        if path.suffix.lower() in {".py", ".md", ".txt", ".json", ".jsonl", ".ts", ".vue",
                                   ".html", ".css", ".toml", ".cfg", ".ini", ".env",
                                   ".sh", ".bat", ".ps1", ".mjs"}:
            files.append(path)
    return files


def test_no_real_api_keys_tracked():
    offenders: list[str] = []
    for path in tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern, label in KEY_PATTERNS:
            for match in pattern.finditer(text):
                offenders.append("%s: %s %r" % (path.relative_to(WORKSPACE), label, match.group(0)[:12] + "…"))
    assert not offenders, "仓库里疑似出现真实 Key：\n" + "\n".join(offenders)


def test_env_example_has_no_real_values():
    """有 .env 样例的话，里面只允许占位符。"""
    for name in (".env", "starter/.env", ".env.example", "starter/.env.example"):
        path = WORKSPACE / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("LLM_API_KEY"):
                value = line.split("=", 1)[-1].strip()
                assert value in ("", "<your-key>", "your-key-here") or not value, (
                    "%s 里 LLM_API_KEY 像是填了真值" % name)

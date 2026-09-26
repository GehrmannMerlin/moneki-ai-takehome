"""构建清单：当前 artifact 到底是从哪一份输入建出来的。

评审会替换 ``data/`` 与 ``knowledge_base/`` 再跑 ``make rebuild``。
没有 provenance 时，"clean.db 存在 → 默认有效" 是静默复用旧数据的最短路径
（R1 缺陷 D3）。这里给 rebuild 与服务启动一个共同的判据：

* ``data_fingerprint(data_dir)``：清洗算法版本 + 真正参与清洗的输入（pos.db）内容；
* ``build_manifest.json``：rebuild 成功后写进 VAR_DIR，记录两侧指纹与统计数；
* 服务启动时（``Service(only_if_missing=True)``）校验 manifest 与当前输入，
  不匹配就**明确报错**要求 rebuild（Strategy A：宁可响亮地失败，不悄悄用旧货）。

指纹规则（对应任务书 §15）：

* 只由"实现版本 + 输入内容"决定——不含本机绝对路径、不含 mtime；
* 内容变 → 指纹变；同内容换目录 → 指纹不变；连续计算 → 指纹一致。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .cleaning import CLEANING_VERSION, SCHEMA_VERSION

MANIFEST_VERSION = 1
MANIFEST_NAME = "build_manifest.json"

#: 真正参与清洗的数据输入：只有 pos.db（stores/products/sales 三表都在里面）。
#: data/ 里的其它文件（如 CSV 原始导出）不参与清洗，不进指纹。
SOURCE_DB_NAME = "pos.db"


def data_fingerprint(data_dir: Path) -> str:
    """数据侧指纹：清洗算法版本 + pos.db 的内容哈希。"""
    digest = hashlib.sha256()
    digest.update(("cleaning:%s|schema:%s\n" % (CLEANING_VERSION, SCHEMA_VERSION)).encode())
    source = Path(data_dir) / SOURCE_DB_NAME
    if not source.exists():
        digest.update(("%s:<missing>\n" % SOURCE_DB_NAME).encode())
        return digest.hexdigest()
    digest.update(("%s:%s\n" % (
        SOURCE_DB_NAME, hashlib.sha256(source.read_bytes()).hexdigest())).encode())
    return digest.hexdigest()


def manifest_path(var_dir: Path) -> Path:
    return Path(var_dir) / MANIFEST_NAME


def write_manifest(var_dir: Path, payload: dict) -> Path:
    """把构建清单写进 VAR_DIR。全部字段由本次 rebuild 的真实输入生成。"""
    path = manifest_path(var_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["manifest_version"] = MANIFEST_VERSION
    body["cleaning_version"] = CLEANING_VERSION
    body["schema_version"] = SCHEMA_VERSION
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_manifest(var_dir: Path) -> dict | None:
    """读构建清单；不存在或损坏返回 None（调用方按无 provenance 处理）。"""
    path = manifest_path(var_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def stale_artifact_error(current: str, stored: str | None) -> RuntimeError:
    """输入与现有 artifact 不匹配时的报错（Strategy A：明确要求 rebuild）。"""
    return RuntimeError(
        "现有清洗产物与当前输入不一致，拒绝静默复用（可能是 data/ 被替换过）。\n"
        "  当前数据指纹：%s\n  产物记录指纹：%s\n"
        "请先执行重建：cd starter && make rebuild"
        "（自定义路径：make rebuild DATA_DIR=... KB_DIR=... VAR_DIR=...）"
        % (current[:16] + "…", (stored[:16] + "…") if stored else "<无 manifest>"))

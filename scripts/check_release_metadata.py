#!/usr/bin/env python3
"""轻量发布元数据一致性校验（版本 / CHANGELOG / release tag）。

在无第三方依赖的前提下运行（仅标准库），可在发布构建环境（只装了
``build``）中直接调用，用于 ``.github/workflows/publish.yml`` 与 CI。

检查项：
1. ``remote_cmd/_version.py`` 可解析出版本号
2. ``pyproject.toml`` 的动态版本指向 ``remote_cmd._version.__version__``
3. ``remote_cmd/__init__.py`` 从 ``remote_cmd._version`` 导入 ``__version__``
4. ``CHANGELOG.md`` 含 ``## [Unreleased]`` 区
5. ``CHANGELOG.md`` 含与当前版本一致的 ``## [x.y.z]`` 标题
6. 提供 ``--tag`` 时（发布工作流），tag（允许 ``v`` 前缀）必须等于包版本

用法::

    python scripts/check_release_metadata.py
    python scripts/check_release_metadata.py --tag v2.1.0
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_VERSION_PATTERN = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_CHANGELOG_HEADING = re.compile(r"^##\s+\[([^\]]+)\]", re.MULTILINE)
_EXPECTED_ATTR = "remote_cmd._version.__version__"


def read_version(version_file: Path) -> str:
    """从 _version.py 中解析 __version__（正则，不导入包以避免依赖）。"""
    match = _VERSION_PATTERN.search(version_file.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"无法从 {version_file} 解析 __version__")
    return match.group(1)


def collect_errors(repo_root: Path, tag: str | None = None) -> list[str]:
    """执行全部一致性检查，返回错误列表（空列表表示通过）。"""
    errors: list[str] = []
    version_file = repo_root / "remote_cmd" / "_version.py"
    init_file = repo_root / "remote_cmd" / "__init__.py"
    pyproject_file = repo_root / "pyproject.toml"
    changelog_file = repo_root / "CHANGELOG.md"

    if not version_file.exists():
        return [f"缺少版本文件: {version_file}"]
    version = read_version(version_file)

    # 2. pyproject 动态版本指向单一真相源
    pyproject = pyproject_file.read_text(encoding="utf-8")
    if _EXPECTED_ATTR not in pyproject:
        errors.append(f"pyproject.toml 未将动态版本指向 {_EXPECTED_ATTR}")

    # 3. 包级导出沿用单一真相源
    init_text = init_file.read_text(encoding="utf-8")
    if "from remote_cmd._version import __version__" not in init_text:
        errors.append("remote_cmd/__init__.py 未从 remote_cmd._version 导入 __version__")

    # 4./5. CHANGELOG 结构与版本标题
    changelog = changelog_file.read_text(encoding="utf-8")
    headings = _CHANGELOG_HEADING.findall(changelog)
    if "Unreleased" not in headings:
        errors.append("CHANGELOG.md 缺少 '## [Unreleased]' 区")
    if version not in headings:
        errors.append(f"CHANGELOG.md 缺少当前版本标题 '## [{version}]'")

    # 6. release tag 与包版本一致（可带 v 前缀）
    if tag is not None:
        normalized = tag[1:] if tag.startswith("v") else tag
        if normalized != version:
            errors.append(f"release tag {tag!r} 与包版本 {version!r} 不一致")

    if not errors:
        print(f"release metadata OK: version={version}" + (f", tag={tag}" if tag else ""))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验版本/CHANGELOG/tag 一致性")
    parser.add_argument(
        "--tag",
        default=None,
        help="release tag（允许 v 前缀）；提供时校验其与包版本一致",
    )
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="仓库根目录（默认自动定位到脚本上级）",
    )
    args = parser.parse_args(argv)

    errors = collect_errors(Path(args.repo_root), tag=args.tag)
    if errors:
        for error in errors:
            print(f"✗ {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

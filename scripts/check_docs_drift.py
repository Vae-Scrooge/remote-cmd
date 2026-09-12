#!/usr/bin/env python3
"""CI 文档漂移门禁：``docs/api`` 必须与当前源码的 pdoc 输出一致。

行为：
1. 用当前解释器运行 ``python -m pdoc remote_cmd -o <临时目录>``
2. 与仓库中已跟踪的 ``docs/api`` 逐文件比较（仅忽略行尾空白：
   pdoc 输出对解释器/小版本可能产生行尾空格差异，内容必须完全一致）
3. 文件集合不一致或任一文件内容漂移时退出码 1，并打印 diff 片段

环境约定（重要）：
- pdoc 输出对 Python 解释器版本敏感（源码渲染/类型注解解析不同），
  因此生成环境必须与 CI docs 任务一致：**Python 3.12** + ``docs`` extra
  中 pinned 的 pdoc 版本（见 ``pyproject.toml``）。
- 需要安装 async extra，否则 ``remote_cmd`` 的条件导出不同，输出会漂移。

重新生成::

    scripts/gen_docs.sh          # 使用当前解释器的 pdoc

用法::

    python scripts/check_docs_drift.py
    python scripts/check_docs_drift.py --output-dir /tmp/pdoc-out   # 保留生成物
"""

from __future__ import annotations

import argparse
import difflib
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACKED_DIR = REPO_ROOT / "docs" / "api"
# pdoc 15 对单包默认输出：index.html + <package>.html + search.js
GENERATED_SUFFIXES = (".html", ".js")
_MAX_DIFF_LINES = 60


def _normalize(text: str) -> str:
    """忽略行尾空白后的规范化文本（保留行结构，末尾统一补换行）。"""
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def _generate(output_dir: Path) -> None:
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pdoc",
                "remote_cmd",
                "-o",
                str(output_dir),
            ],
            cwd=REPO_ROOT,
            check=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - 环境错误
        raise SystemExit(f"pdoc 生成失败（exit {exc.returncode}）") from exc


def _generated_files(root: Path) -> set[str]:
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.suffix in GENERATED_SUFFIXES
    }


def check_drift(output_dir: Path | None = None) -> int:
    """生成并与 docs/api 比较；返回 0=一致，1=漂移。"""
    with tempfile.TemporaryDirectory(prefix="pdoc-drift-") as tmp:
        generated_dir = output_dir if output_dir is not None else Path(tmp)
        generated_dir.mkdir(parents=True, exist_ok=True)
        _generate(generated_dir)

        tracked_files = _generated_files(TRACKED_DIR)
        generated_files = _generated_files(generated_dir)

        errors: list[str] = []
        for missing in sorted(generated_files - tracked_files):
            errors.append(f"docs/api 缺少生成文件: {missing}")
        for extra in sorted(tracked_files - generated_files):
            errors.append(f"docs/api 存在未被生成的多余文件: {extra}")

        for name in sorted(tracked_files & generated_files):
            tracked_text = _normalize((TRACKED_DIR / name).read_text(encoding="utf-8"))
            generated_text = _normalize((generated_dir / name).read_text(encoding="utf-8"))
            if tracked_text == generated_text:
                continue
            diff = list(
                difflib.unified_diff(
                    tracked_text.splitlines(),
                    generated_text.splitlines(),
                    fromfile=f"docs/api/{name}",
                    tofile=f"generated/{name}",
                    lineterm="",
                    n=2,
                )
            )
            errors.append(f"docs/api/{name} 已过期（{len(diff)} 行 diff）")
            print("\n".join(diff[:_MAX_DIFF_LINES]))
            if len(diff) > _MAX_DIFF_LINES:
                print(f"... （省略 {len(diff) - _MAX_DIFF_LINES} 行）")

        if errors:
            print("检测到 API 文档漂移：", file=sys.stderr)
            for error in errors:
                print(f"  ✗ {error}", file=sys.stderr)
            print(
                "请在 Python 3.12 + 已安装 docs/async extra 的环境中重新生成： scripts/gen_docs.sh",
                file=sys.stderr,
            )
            return 1

        print(f"API 文档与源码一致（{len(tracked_files)} 个文件）")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 docs/api 是否与 pdoc 输出一致")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="pdoc 输出目录（默认使用临时目录并在检查后清理）",
    )
    args = parser.parse_args(argv)
    return check_drift(Path(args.output_dir) if args.output_dir else None)


if __name__ == "__main__":
    raise SystemExit(main())

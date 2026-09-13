#!/usr/bin/env bash
# 生成 pdoc API 文档到 docs/api/
# 用法: scripts/gen_docs.sh
# 可选: PYTHON=/path/to/python3.12 scripts/gen_docs.sh
#
# 重要: pdoc HTML 输出对 Python 解释器版本敏感。CI 文档漂移门禁
# (scripts/check_docs_drift.py) 固定使用 Python 3.12 + pyproject docs extra
# 中 pinned 的 pdoc。请尽量使用 Python 3.12 生成，否则 CI 可能报文档漂移。
set -euo pipefail

# 确定性构建契约：关闭 pdoc 的环境变量遮蔽（否则值等于构建环境某环境变量
# 的模块常量会被渲染成 $ENVVAR，例如 CI 上的 __author__ 与 actor 同名）。
# 必须与 scripts/check_docs_drift.py 的 PDOC_DETERMINISTIC_ENV 保持一致。
: "${PDOC_DISPLAY_ENV_VARS:=1}"
export PDOC_DISPLAY_ENV_VARS

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
    if [ -x .venv/bin/python ]; then
        PYTHON_BIN=.venv/bin/python
    else
        PYTHON_BIN=python3
    fi
fi

if ! "$PYTHON_BIN" -c "import pdoc" >/dev/null 2>&1; then
    echo "pdoc 未安装，请先执行: $PYTHON_BIN -m pip install -e '.[docs,async]'" >&2
    exit 1
fi

if ! "$PYTHON_BIN" -c "import asyncssh" >/dev/null 2>&1; then
    echo "asyncssh 未安装，文档中异步导出将缺失，请安装 async extra" >&2
    exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys

if sys.version_info[:2] != (3, 12):
    sys.stderr.write(
        "警告: 当前解释器 %s 与 CI docs 环境 (Python 3.12) 不一致；"
        "生成结果可能导致 CI 文档漂移门禁失败。可设置 PYTHON 指向 3.12 解释器。\n"
        % sys.version.split()[0]
    )
PY

# 先输出到临时目录，成功后再替换 docs/api，避免生成失败时丢失已跟踪文档
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

"$PYTHON_BIN" -m pdoc remote_cmd -o "$tmpdir"

# 去除 pdoc 生成 HTML/JS 的行尾空白，保持 git diff --check 干净；
# 漂移门禁（check_docs_drift.py）比较时同样忽略行尾空白。
"$PYTHON_BIN" - "$tmpdir" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
for path in root.rglob("*"):
    if path.is_file() and path.suffix in (".html", ".js"):
        text = path.read_text(encoding="utf-8")
        path.write_text(
            "\n".join(line.rstrip() for line in text.splitlines()) + "\n",
            encoding="utf-8",
        )
PY

rm -rf docs/api
mkdir -p docs/api
cp -r "$tmpdir"/. docs/api/
echo "API 文档已生成到 docs/api/"

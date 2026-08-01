#!/usr/bin/env bash
# 生成 pdoc API 文档到 docs/api/
# 用法: scripts/gen_docs.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# 使用项目 venv 中的 pdoc；若不存在则提示安装
if [ ! -x .venv/bin/pdoc ]; then
    echo "pdoc 未安装，请先执行: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
    exit 1
fi

mkdir -p docs/api
.venv/bin/pdoc remote_cmd -o docs/api
echo "API 文档已生成到 docs/api/"

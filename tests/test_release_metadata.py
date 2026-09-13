"""发布元数据一致性门禁（scripts/check_release_metadata.py）测试。

CI 与 publish 工作流使用该脚本作为门禁；此处通过真实调用验证：
- 当前仓库版本/CHANGELOG/pyproject 元数据自洽
- release tag 与包版本一致时通过、不一致时失败
- 缺少对应 CHANGELOG 版本标题时被检出
"""

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_release_metadata.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    """调用门禁脚本，强制子进程与父进程均按 UTF-8 处理输出。

    脚本会打印中文错误（如 ``不一致``）；Windows runner 的默认控制台编码
    （cp1252）会让子进程 stderr 退化为 ``\\uXXXX`` 转义字面量。这里显式
    固定两侧编码，使断言与平台无关。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _current_version() -> str:
    text = (REPO_ROOT / "remote_cmd" / "_version.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    assert match, "无法解析 __version__"
    return match.group(1)


def test_current_metadata_is_consistent():
    proc = _run()
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "release metadata OK" in proc.stdout


def test_release_tag_matching_version_passes():
    proc = _run("--tag", f"v{_current_version()}")
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_release_tag_mismatch_fails():
    proc = _run("--tag", "v0.0.1")
    assert proc.returncode == 1
    assert "不一致" in proc.stderr


def test_missing_changelog_version_heading_fails(tmp_path: Path):
    """构造最小假仓库：版本 9.9.9 但 CHANGELOG 无对应标题 → 门禁失败"""
    (tmp_path / "remote_cmd").mkdir()
    (tmp_path / "remote_cmd" / "_version.py").write_text(
        '__version__ = "9.9.9"\n', encoding="utf-8"
    )
    (tmp_path / "remote_cmd" / "__init__.py").write_text(
        "from remote_cmd._version import __version__\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        'version = { attr = "remote_cmd._version.__version__" }\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [2.0.0] - 2025-01-01\n", encoding="utf-8"
    )

    proc = _run("--repo-root", str(tmp_path))
    assert proc.returncode == 1
    assert "9.9.9" in proc.stderr


def test_dependency_security_floors_do_not_regress():
    """v2.5 安全下限回归：Python / Paramiko / AsyncSSH 版本约束不得回退。

    - Python 3.10+（3.9 已 EOL）
    - Paramiko 5.x（SHA-1 相关 CVE 影响旧版本）
    - AsyncSSH 2.24+（2026 年 SCP 路径穿越等 CVE 修复于 2.23.1+）
    """
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.10"' in pyproject
    assert "paramiko>=5.0,<6" in pyproject
    assert "asyncssh>=2.24.0,<3" in pyproject

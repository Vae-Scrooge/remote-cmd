"""包级 import 行为测试（v2.7 P2.2）。

- asyncssh 存在时导出异步符号，``_HAS_ASYNC`` 与 ``find_spec`` 一致
- 异步模块自身的 import 缺陷不再被静默吞掉（旧实现会误判为
  "未安装 asyncssh" 并降级导出）
"""

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_has_async_matches_find_spec():
    import importlib.util

    import remote_cmd

    has_asyncssh = importlib.util.find_spec("asyncssh") is not None
    assert has_asyncssh == remote_cmd._HAS_ASYNC
    if remote_cmd._HAS_ASYNC:
        assert hasattr(remote_cmd, "AsyncSSHClient")


def test_broken_async_module_import_propagates():
    """asyncssh 已安装时，我们自身模块的 ImportError 必须向上传播。"""
    code = textwrap.dedent(
        """
        import builtins
        import importlib.util

        assert importlib.util.find_spec("asyncssh") is not None, "test requires asyncssh"

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "remote_cmd.core.async_connection_pool":
                raise ImportError("simulated defect in our async module")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            import remote_cmd  # noqa: F401
        except ImportError:
            print("PROPAGATED")
        else:
            print("SWALLOWED")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PROPAGATED" in proc.stdout, proc.stdout

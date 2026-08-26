"""
版本号单一真相源（无任何 import，纯常量）

供 ``remote_cmd/__init__.py`` 与 ``pyproject.toml`` 的 setuptools 动态版本读取。

设计约定：
- 本模块不 import 任何东西，无副作用——setuptools 通过 ``attr`` 解析版本时
  不会触发 ``remote_cmd`` 包级 import（避免干净构建环境缺依赖时 ImportError）。
- ``__version__`` 由 ``remote_cmd/__init__.py`` 导入并继续作为公共 API 导出。
"""

__version__ = "2.1.0"

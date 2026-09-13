"""
Recipe 存储能力协议（v2.9）

与 :class:`ProfileStore` 同一模式：``HostRepository`` 的 ABC 保持稳定，
支持 Recipe 的仓库额外实现本协议（runtime_checkable），能力检测用
``isinstance(repo, RecipeStore)``。

存储语义与主机一致：
- JSON：内存保存，``flush()`` 原子落盘（``recipes`` 段）
- SQLite：``save_recipe`` 即时生效（``recipes`` 表）
"""

from typing import Protocol, runtime_checkable

from remote_cmd.core.recipe import Recipe


@runtime_checkable
class RecipeStore(Protocol):
    """可选的 Recipe 持久化能力协议。"""

    def save_recipe(self, recipe: Recipe) -> None:
        """保存（新增或覆盖）Recipe。"""
        ...

    def get_recipe(self, name: str) -> Recipe:
        """按名称获取 Recipe，不存在时抛出 ``KeyError``。"""
        ...

    def delete_recipe(self, name: str) -> None:
        """按名称删除 Recipe，不存在时抛出 ``KeyError``。"""
        ...

    def list_recipes(self) -> list[Recipe]:
        """列出全部 Recipe（按名称排序）。"""
        ...

    def contains_recipe(self, name: str) -> bool:
        """检查 Recipe 是否存在。"""
        ...


__all__ = ["RecipeStore"]

"""
Recipe 业务服务（v2.9）

协调 :class:`RecipeStore` 完成 Recipe 的 CRUD，并基于
:func:`remote_cmd.core.recipe.render_recipe` 提供安全渲染：

    >>> from remote_cmd.service.recipe_service import RecipeService
    >>> service = RecipeService(store=repo)
    >>> service.add_recipe(Recipe(name="uptime", command="uptime"))
    >>> rendered = service.render("uptime", {})

设计约定：
- 存储能力来自 RecipeStore 协议（JSON/SQLite 仓库均实现）；
  仓库自身若有 ``flush()``（JSON 内存+落盘模型），服务在写操作后调用。
- Recipe 不接触凭据；渲染只做两件事：shell_arg → ``shlex.quote``、
  env → ``environment`` 导出 + ``"$NAME"`` 引用（不提供 raw/裸插值）。
- 执行不在本模块内硬编码：调用方把 ``RenderedRecipe`` 交给
  ``BatchExecutor.execute(command=..., environment=...)``。
"""

import logging

from remote_cmd.core.recipe import Recipe, RenderedRecipe, render_recipe
from remote_cmd.repository.recipe_store import RecipeStore

logger = logging.getLogger(__name__)


class RecipeService:
    """Recipe 管理服务。

    Args:
        store: Recipe 存储（实现 RecipeStore 协议的仓库）
    """

    def __init__(self, store: RecipeStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def add_recipe(self, recipe: Recipe) -> Recipe:
        """新增 Recipe。

        Raises:
            ValueError: 同名 Recipe 已存在
        """
        if self._store.contains_recipe(recipe.name):
            raise ValueError(f"recipe '{recipe.name}' already exists")
        self._store.save_recipe(recipe)
        self._flush()
        logger.info(f"recipe added: {recipe.name}")
        return recipe

    def update_recipe(self, recipe_name: str, **kwargs) -> Recipe:
        """更新 Recipe 字段（不存在时 KeyError）。

        仅更新 Recipe 已有字段；``name`` 不可改名（传入会抛 ValueError）。
        更新后重新构造以触发完整校验（含占位符声明一致性）。
        """
        recipe = self._store.get_recipe(recipe_name)
        data = recipe.to_dict()
        for key, value in kwargs.items():
            if key == "name":
                raise ValueError("recipe name cannot be changed; remove and re-add instead")
            if key in data:
                data[key] = value
        validated = Recipe.from_dict(data)
        self._store.save_recipe(validated)
        self._flush()
        logger.info(f"recipe updated: {recipe_name}")
        return validated

    def remove_recipe(self, name: str) -> None:
        """删除 Recipe（不存在时 KeyError）。

        Recipe 不被主机引用，因此无引用保护。
        """
        self._store.get_recipe(name)  # 不存在 -> KeyError
        self._store.delete_recipe(name)
        self._flush()
        logger.info(f"recipe removed: {name}")

    def get_recipe(self, name: str) -> Recipe:
        return self._store.get_recipe(name)

    def list_recipes(self) -> list[Recipe]:
        return self._store.list_recipes()

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def render(self, name: str, values: dict[str, str] | None = None) -> RenderedRecipe:
        """按名称渲染 Recipe（变量类型安全替换）。

        Raises:
            KeyError: Recipe 不存在
            ValidationError: 必填缺失 / 提供未声明变量 / 值非字符串
        """
        recipe = self._store.get_recipe(name)
        return render_recipe(recipe, values)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _flush(self) -> None:
        """仓库支持 flush（JSON）时落盘；SQLite 写入即时生效则跳过。"""
        flush = getattr(self._store, "flush", None)
        if callable(flush):
            flush()


__all__ = ["RecipeService"]

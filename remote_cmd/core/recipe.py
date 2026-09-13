"""
安全命令 Recipe 模型与渲染器（v2.9）

Recipe 是**参数化命令模板**：占位符 ``{{ name }}`` 按变量类型安全替换，
从根上避免裸字符串插值（``str.format`` / f-string）造成的命令注入：

    >>> from remote_cmd.core.recipe import Recipe, RecipeVariable, render_recipe
    >>> recipe = Recipe(
    ...     name="deploy",
    ...     command="deploy {{ package }}",
    ...     variables={"package": RecipeVariable(name="package")},
    ... )
    >>> rendered = render_recipe(recipe, {"package": "foo; rm -rf /"})
    >>> rendered.command
    "deploy 'foo; rm -rf /'"
    >>> render_recipe(recipe, {"package": "$(id)"}).command
    "deploy '$(id)'"

变量类型（v2.9 仅两种，不提供 raw/裸插值）：
- ``shell_arg``（默认）：值经 :func:`shlex.quote` 转义后内联到命令；
  分号、空格、``$()``、反引号、换行等全部被隔离为单个参数。
- ``env``：值通过 SSH 客户端的 ``environment`` 机制导出为远端环境变量，
  命令中的占位符替换为 ``"$NAME"``（双引号展开，值不会二次分词）。

严格校验：
- 变量名必须是合法 shell 标识符（``[A-Za-z_][A-Za-z0-9_]*``）
- 命令中出现的每个 ``{{ name }}`` 必须已在 ``variables`` 中声明
  （未声明的占位符在构造 Recipe 时报 ValidationError）
- 渲染时：必填变量缺失 / 提供了未声明的变量 / 值非字符串 → ValidationError
- 替换为单遍 regex 替换：变量值即使包含 ``{{ ... }}`` 也不会被二次解析

用法（CLI/服务层）:
    >>> env_recipe = Recipe(
    ...     name="deploy",
    ...     command="deploy --token {{ TOKEN }} {{ package }}",
    ...     variables={
    ...         "package": RecipeVariable(name="package"),
    ...         "TOKEN": RecipeVariable(name="TOKEN", type="env"),
    ...     },
    ... )
"""

import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional

from remote_cmd.utils.exceptions import ValidationError

#: 变量类型：shell 参数（shlex.quote 内联）
VAR_TYPE_SHELL_ARG = "shell_arg"
#: 变量类型：远端环境变量（environment 导出 + "$NAME" 引用）
VAR_TYPE_ENV = "env"
VARIABLE_TYPES = (VAR_TYPE_SHELL_ARG, VAR_TYPE_ENV)

#: 合法变量名（同时是合法环境变量名）
_VAR_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: 占位符语法：{{ name }}（name 为合法标识符）
_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class RecipeVariable:
    """Recipe 变量声明。

    Args:
        name: 变量名（合法 shell/环境变量标识符）
        type: ``shell_arg``（默认，自动 shlex.quote）或 ``env``（导出环境变量）
        required: 未提供值且无 default 时是否报错（默认 True）
        default: 默认值（提供时 required 不再生效）
        description: 变量说明（展示用）
    """

    name: str
    type: str = VAR_TYPE_SHELL_ARG
    required: bool = True
    default: Optional[str] = None
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _VAR_NAME_PATTERN.match(self.name):
            raise ValidationError(
                f"variable name must match [A-Za-z_][A-Za-z0-9_]*, got: {self.name!r}"
            )
        if self.type not in VARIABLE_TYPES:
            raise ValidationError(
                f"unsupported variable type: {self.type!r} "
                f"(expected one of: {', '.join(VARIABLE_TYPES)})"
            )
        if self.default is not None and not isinstance(self.default, str):
            raise ValidationError(f"variable default must be a string, got: {self.default!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecipeVariable":
        known = {"name", "type", "required", "default", "description"}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class RenderedRecipe:
    """渲染结果：远端命令 + 需要导出的环境变量。"""

    command: str
    environment: dict[str, str] = field(default_factory=dict)


@dataclass
class Recipe:
    """参数化命令模板。

    Args:
        name: Recipe 名称（非空唯一标识）
        command: 命令模板（占位符 ``{{ var }}``）
        variables: 变量声明表（``name -> RecipeVariable``）
        description: 描述
        tags: 标签

    Raises:
        ValidationError: 名称为空/模板为空/变量非法/存在未声明的占位符
    """

    name: str
    command: str
    variables: dict[str, RecipeVariable] = field(default_factory=dict)
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValidationError(f"recipe name must be a non-empty string, got: {self.name!r}")
        if not isinstance(self.command, str) or not self.command.strip():
            raise ValidationError("recipe command must be a non-empty string")
        if self.tags is None:
            self.tags = []
        if not isinstance(self.tags, list) or any(not isinstance(t, str) for t in self.tags):
            raise ValidationError(f"recipe tags must be a list of strings, got: {self.tags!r}")
        if not isinstance(self.variables, dict):
            raise ValidationError("recipe variables must be a dict of name -> RecipeVariable")
        for key, var in self.variables.items():
            if not isinstance(var, RecipeVariable):
                raise ValidationError(f"recipe variable {key!r} must be a RecipeVariable")
            if key != var.name:
                raise ValidationError(
                    f"recipe variable key {key!r} does not match declaration name {var.name!r}"
                )

        undeclared = {
            m.group(1)
            for m in _PLACEHOLDER_PATTERN.finditer(self.command)
            if m.group(1) not in self.variables
        }
        if undeclared:
            raise ValidationError(
                "recipe command references undeclared variable(s): "
                f"{', '.join(sorted(undeclared))}; declare them in variables"
            )

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "variables": {name: var.to_dict() for name, var in self.variables.items()},
            "description": self.description,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Recipe":
        variables_data = data.get("variables", {})
        variables = (
            {name: RecipeVariable.from_dict(var) for name, var in variables_data.items()}
            if isinstance(variables_data, dict)
            else {}
        )
        return cls(
            name=data.get("name", ""),
            command=data.get("command", ""),
            variables=variables,
            description=data.get("description", ""),
            tags=data.get("tags", []),
        )


def render_recipe(recipe: Recipe, values: Optional[dict[str, str]] = None) -> RenderedRecipe:
    """按变量类型安全渲染 Recipe。

    Args:
        recipe: Recipe 定义
        values: 变量值（CLI/API 提供的字符串）

    Returns:
        RenderedRecipe: ``command``（shell_arg 已转义、env 以 ``"$NAME"`` 引用）
        与 ``environment``（env 变量的值，交由 SSH 客户端安全导出）

    Raises:
        ValidationError: 必填缺失、提供未声明变量、值非字符串
    """
    supplied = dict(values or {})
    unknown = set(supplied) - set(recipe.variables)
    if unknown:
        raise ValidationError(
            f"unknown variable(s) for recipe '{recipe.name}': {', '.join(sorted(unknown))}"
        )
    for key, value in supplied.items():
        if not isinstance(value, str):
            raise ValidationError(f"value for variable {key!r} must be a string, got: {value!r}")

    resolved: dict[str, str] = {}
    missing: list[str] = []
    for name, var in recipe.variables.items():
        if name in supplied:
            resolved[name] = supplied[name]
        elif var.default is not None:
            resolved[name] = var.default
        elif var.required:
            missing.append(name)
        else:
            resolved[name] = ""
    if missing:
        raise ValidationError(
            f"missing required variable(s) for recipe '{recipe.name}': {', '.join(sorted(missing))}"
        )

    environment: dict[str, str] = {}

    def _substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        value = resolved[name]
        if recipe.variables[name].type == VAR_TYPE_ENV:
            environment[name] = value
            # 双引号展开：远端 shell 不会对值做分词/通配
            return f'"${name}"'
        return shlex.quote(value)

    command = _PLACEHOLDER_PATTERN.sub(_substitute, recipe.command)
    return RenderedRecipe(command=command, environment=environment)


__all__ = [
    "VARIABLE_TYPES",
    "VAR_TYPE_ENV",
    "VAR_TYPE_SHELL_ARG",
    "Recipe",
    "RecipeVariable",
    "RenderedRecipe",
    "render_recipe",
]

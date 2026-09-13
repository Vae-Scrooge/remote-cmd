"""Recipe 模型与安全渲染器测试（v2.9）。

安全重点：shell_arg 值经 shlex.quote 后**在真实 /bin/sh 中执行也必须保持
字面量**——注入 payload 不得触发命令执行、分词或替换。
"""

import os
import shlex
import shutil
import subprocess

import pytest

from remote_cmd.core.recipe import (
    Recipe,
    RecipeVariable,
    render_recipe,
)
from remote_cmd.utils.exceptions import ValidationError

# 注入安全测试在真实 POSIX shell 中执行渲染结果；Windows runner 通常没有
# `sh`，此时跳过（Unix/Linux/macOS 保持完整覆盖），由 shlex.quote 断言
# （test_quoted_form_matches_shlex）继续在各平台验证转义语义。
_HAS_POSIX_SH = shutil.which("sh") is not None
_requires_posix_sh = pytest.mark.skipif(
    not _HAS_POSIX_SH, reason="requires a POSIX shell (sh) on PATH"
)

INJECTION_PAYLOADS = [
    "; touch pwned",
    "$(touch pwned)",
    "`touch pwned`",
    "a b",
    "a\nb",
    "a'b",
    'a"b',
    "&& rm -rf /tmp/x",
    "| cat /etc/passwd",
    "*",
    "~",
    "$HOME",
    "a;b;c",
    "> pwned",
    "${IFS}",
    "\\$(touch pwned)",
]


class TestRecipeModel:
    def test_minimal(self):
        recipe = Recipe(name="uptime", command="uptime")
        assert recipe.variables == {}

    def test_variable_serialization_roundtrip(self):
        recipe = Recipe(
            name="deploy",
            command="deploy {{ pkg }}",
            variables={
                "pkg": RecipeVariable(name="pkg", default="app", description="package"),
            },
            description="d",
            tags=["ops"],
        )
        restored = Recipe.from_dict(recipe.to_dict())
        assert restored == recipe

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_invalid_name(self, bad):
        with pytest.raises(ValidationError, match="name"):
            Recipe(name=bad, command="x")

    def test_empty_command(self):
        with pytest.raises(ValidationError, match="command"):
            Recipe(name="r", command="  ")

    def test_undeclared_placeholder_rejected(self):
        with pytest.raises(ValidationError, match="undeclared variable.*pkg"):
            Recipe(name="r", command="deploy {{ pkg }}")

    @pytest.mark.parametrize("name", ["1abc", "a-b", "", "a.b", "a b"])
    def test_invalid_variable_name(self, name):
        with pytest.raises(ValidationError, match="variable name"):
            RecipeVariable(name=name)

    def test_invalid_variable_type(self):
        with pytest.raises(ValidationError, match="unsupported variable type"):
            RecipeVariable(name="x", type="raw")

    def test_non_string_default(self):
        with pytest.raises(ValidationError, match="default"):
            RecipeVariable(name="x", default=1)

    def test_key_name_mismatch(self):
        with pytest.raises(ValidationError, match="does not match"):
            Recipe(
                name="r",
                command="{{ a }}",
                variables={"a": RecipeVariable(name="b")},
            )


class TestRenderBasics:
    def test_shell_arg_quoted(self):
        recipe = Recipe(
            name="deploy",
            command="deploy {{ package }}",
            variables={"package": RecipeVariable(name="package")},
        )
        rendered = render_recipe(recipe, {"package": "foo"})
        assert rendered.command == "deploy foo"
        assert rendered.environment == {}

    def test_shell_arg_with_spaces_quoted(self):
        recipe = Recipe(
            name="deploy",
            command="deploy {{ package }}",
            variables={"package": RecipeVariable(name="package")},
        )
        rendered = render_recipe(recipe, {"package": "foo bar"})
        assert rendered.command == "deploy 'foo bar'"

    def test_default_used_when_missing(self):
        recipe = Recipe(
            name="r",
            command="echo {{ who }}",
            variables={"who": RecipeVariable(name="who", default="world")},
        )
        assert render_recipe(recipe, {}).command == "echo world"

    def test_optional_empty_when_missing(self):
        recipe = Recipe(
            name="r",
            command="echo {{ opt }}",
            variables={"opt": RecipeVariable(name="opt", required=False)},
        )
        assert render_recipe(recipe, {}).command == "echo ''"

    def test_missing_required_raises(self):
        recipe = Recipe(
            name="r",
            command="echo {{ a }} {{ b }}",
            variables={
                "a": RecipeVariable(name="a"),
                "b": RecipeVariable(name="b"),
            },
        )
        with pytest.raises(ValidationError, match="missing required.*a, b"):
            render_recipe(recipe, {})

    def test_unknown_supplied_raises(self):
        recipe = Recipe(name="r", command="echo {{ a }}", variables={"a": RecipeVariable(name="a")})
        with pytest.raises(ValidationError, match="unknown variable.*zzz"):
            render_recipe(recipe, {"a": "x", "zzz": "y"})

    def test_non_string_value_raises(self):
        recipe = Recipe(name="r", command="echo {{ a }}", variables={"a": RecipeVariable(name="a")})
        with pytest.raises(ValidationError, match="must be a string"):
            render_recipe(recipe, {"a": 1})

    def test_single_pass_no_reprocessing(self):
        """变量值里的 {{ }} 不得被二次解析。"""
        recipe = Recipe(name="r", command="echo {{ a }}", variables={"a": RecipeVariable(name="a")})
        rendered = render_recipe(recipe, {"a": "{{ b }}"})
        assert rendered.command == "echo '{{ b }}'"

    def test_env_type_renders_reference_and_environment(self):
        recipe = Recipe(
            name="r",
            command="curl {{ url }} --token {{ TOKEN }}",
            variables={
                "url": RecipeVariable(name="url"),
                "TOKEN": RecipeVariable(name="TOKEN", type="env"),
            },
        )
        rendered = render_recipe(recipe, {"url": "https://x", "TOKEN": "s3cr3t"})
        assert rendered.command == 'curl https://x --token "$TOKEN"'
        assert rendered.environment == {"TOKEN": "s3cr3t"}


class TestInjectionSafety:
    @_requires_posix_sh
    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_shell_arg_payload_stays_literal_in_real_sh(self, payload, tmp_path):
        """核心安全测试：在真实 /bin/sh 中执行渲染结果，payload 不得生效。"""
        recipe = Recipe(
            name="echo",
            command="printf '%s' {{ value }}",
            variables={"value": RecipeVariable(name="value")},
        )
        rendered = render_recipe(recipe, {"value": payload})
        assert rendered.environment == {}

        proc = subprocess.run(
            ["sh", "-c", rendered.command],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert proc.returncode == 0
        assert proc.stdout == payload  # 完整字面量，未分词/未展开
        assert not (tmp_path / "pwned").exists()

    @_requires_posix_sh
    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_env_payload_stays_literal_in_real_sh(self, payload, tmp_path):
        """env 类型：值经 environment 导出，命令内 "$NAME" 展开仍为字面量。"""
        recipe = Recipe(
            name="echo",
            command="printf '%s' {{ TOKEN }}",
            variables={"TOKEN": RecipeVariable(name="TOKEN", type="env")},
        )
        rendered = render_recipe(recipe, {"TOKEN": payload})
        assert rendered.command == "printf '%s' \"$TOKEN\""

        env = dict(os.environ)
        env.update(rendered.environment)
        proc = subprocess.run(
            ["sh", "-c", rendered.command],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=env,
        )
        assert proc.returncode == 0
        assert proc.stdout == payload
        assert not (tmp_path / "pwned").exists()

    def test_quoted_form_matches_shlex(self):
        recipe = Recipe(name="r", command="{{ v }}", variables={"v": RecipeVariable(name="v")})
        for payload in INJECTION_PAYLOADS:
            assert render_recipe(recipe, {"v": payload}).command == shlex.quote(payload)

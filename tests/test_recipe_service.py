"""RecipeService 与 RecipeStore 双后端测试（v2.9）。"""

import json

import pytest

from remote_cmd.core.recipe import Recipe, RecipeVariable
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.repository.recipe_store import RecipeStore
from remote_cmd.repository.sqlite_host_repository import SqliteHostRepository
from remote_cmd.service.recipe_service import RecipeService
from remote_cmd.utils.exceptions import ValidationError


def _recipe(name="deploy", command="deploy {{ pkg }}") -> Recipe:
    return Recipe(
        name=name,
        command=command,
        variables={"pkg": RecipeVariable(name="pkg", default="app")},
        description="deploy app",
        tags=["ops"],
    )


class TestRecipeStoreCapability:
    def test_builtin_repos_implement_protocol(self, tmp_path):
        assert isinstance(JsonHostRepository(str(tmp_path / "h.json")), RecipeStore)
        assert isinstance(SqliteHostRepository(str(tmp_path / "h.db")), RecipeStore)


class TestRecipeServiceJson:
    def _build(self, tmp_path):
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        return repo, RecipeService(store=repo)

    def test_add_get_list(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_recipe(_recipe("b"))
        service.add_recipe(_recipe("a"))
        assert [r.name for r in service.list_recipes()] == ["a", "b"]
        assert service.get_recipe("a").description == "deploy app"

    def test_persists_and_reloads(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_recipe(_recipe())
        raw = json.loads((tmp_path / "hosts.json").read_text(encoding="utf-8"))
        assert raw["recipes"]["deploy"]["command"] == "deploy {{ pkg }}"

        reloaded = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        assert reloaded.get_recipe("deploy").variables["pkg"].default == "app"

    def test_duplicate_add_raises(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_recipe(_recipe())
        with pytest.raises(ValueError, match="already exists"):
            service.add_recipe(_recipe())

    def test_update_validates_and_persists(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_recipe(_recipe())
        updated = service.update_recipe("deploy", description="new")
        assert updated.description == "new"
        # 非法更新（引用未声明变量）不得污染存储
        with pytest.raises(ValidationError, match="undeclared"):
            service.update_recipe("deploy", command="deploy {{ ghost }}")
        assert service.get_recipe("deploy").command == "deploy {{ pkg }}"

    def test_update_name_rejected(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_recipe(_recipe())
        with pytest.raises(ValueError, match="cannot be changed"):
            service.update_recipe("deploy", name="other")

    def test_remove_flow(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_recipe(_recipe())
        service.remove_recipe("deploy")
        assert service.list_recipes() == []
        with pytest.raises(KeyError):
            service.remove_recipe("deploy")

    def test_render_through_service(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_recipe(_recipe())
        rendered = service.render("deploy", {"pkg": "a;b"})
        assert rendered.command == "deploy 'a;b'"


class TestRecipeServiceSqlite:
    def test_crud_persistence_parity(self, tmp_path):
        db = str(tmp_path / "hosts.db")
        service = RecipeService(store=SqliteHostRepository(db))

        service.add_recipe(
            Recipe(
                name="gcp",
                command="echo {{ msg }}",
                variables={"msg": RecipeVariable(name="msg", required=False)},
                tags=["cloud"],
            )
        )
        service.add_recipe(_recipe("aws"))

        repo2 = SqliteHostRepository(db)
        assert [r.name for r in repo2.list_recipes()] == ["aws", "gcp"]
        assert repo2.get_recipe("gcp").variables["msg"].required is False

        service.update_recipe("gcp", description="updated")
        assert SqliteHostRepository(db).get_recipe("gcp").description == "updated"

        service.remove_recipe("aws")
        assert not repo2.contains_recipe("aws")


class TestRecipeBackendParity:
    def test_recipe_roundtrip_parity(self, tmp_path):
        json_repo = JsonHostRepository(filepath=str(tmp_path / "h.json"))
        sqlite_repo = SqliteHostRepository(str(tmp_path / "h.db"))
        recipe = Recipe(
            name="mix",
            command="run {{ arg }} {{ TOKEN }}",
            variables={
                "arg": RecipeVariable(name="arg"),
                "TOKEN": RecipeVariable(name="TOKEN", type="env"),
            },
            tags=["t"],
        )
        for repo in (json_repo, sqlite_repo):
            repo.save_recipe(recipe)
        json_repo.flush()

        a = JsonHostRepository(filepath=str(tmp_path / "h.json")).get_recipe("mix")
        b = SqliteHostRepository(str(tmp_path / "h.db")).get_recipe("mix")
        assert a.to_dict() == b.to_dict() == recipe.to_dict()

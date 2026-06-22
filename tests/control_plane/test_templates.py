"""Tests for the task template module (V1.1 P1).

Covers:
  * TemplatesStore: defaults seeding, persistence, CRUD, malformed file fallback
  * render_template: literal substitution, defaults, escaped braces, missing-param error
  * HTTP routes: list / create / patch / delete / render (success + 404 + 422)

All file I/O is redirected to ``tmp_path`` via ``monkeypatch.setattr`` on
``templates_store._default_path`` so the user's real ``~/.hermes/`` directory
is never touched.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway.control_plane import templates_store as ts_mod
from gateway.control_plane.app import create_control_plane_app, init_store
from gateway.control_plane.deps import AppState
from gateway.control_plane.templates_store import (
    MissingParamsError,
    Template,
    TemplateParam,
    TemplatesStore,
    render_template,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def isolated_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the default templates path into ``tmp_path``."""
    target = tmp_path / "control-plane-templates.json"
    monkeypatch.setattr(ts_mod, "_default_path", lambda: str(target))
    return target


@pytest.fixture()
def store(isolated_path: Path) -> TemplatesStore:
    return TemplatesStore()


@pytest.fixture()
def app(tmp_path: Path, isolated_path: Path) -> FastAPI:
    """A FastAPI app with the control plane mounted; templates_store is fresh."""
    db_path = tmp_path / "test.db"
    cp = create_control_plane_app(db_path=str(db_path))
    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_store(cp))
    loop.close()
    main = FastAPI()
    main.mount("/control-plane", cp)
    return main


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── Store unit tests ─────────────────────────────────────────────────────────


class TestTemplatesStore:
    def test_default_seeding(self, store: TemplatesStore, isolated_path: Path) -> None:
        templates = store.load_from_disk()
        ids = {t.id for t in templates}
        assert ids == {"refactor-module", "fix-bug", "add-tests"}
        # File should now exist on disk
        assert isolated_path.exists()
        raw = json.loads(isolated_path.read_text(encoding="utf-8"))
        assert isinstance(raw, list) and len(raw) == 3

    def test_no_overwrite_on_user_modifications(
        self, isolated_path: Path
    ) -> None:
        # Seed defaults
        TemplatesStore().load_from_disk()
        # User edits the file — drops one default and renames another
        edited = [
            {
                "id": "refactor-module",
                "name": "重构（自定义）",
                "description": "user edited",
                "body": "edited body",
                "params": [],
            }
        ]
        isolated_path.write_text(json.dumps(edited), encoding="utf-8")
        # Re-loading must not re-seed defaults
        store2 = TemplatesStore()
        templates = store2.load_from_disk()
        assert len(templates) == 1
        assert templates[0].name == "重构（自定义）"

    def test_malformed_file_falls_back_to_defaults(
        self, isolated_path: Path
    ) -> None:
        isolated_path.parent.mkdir(parents=True, exist_ok=True)
        isolated_path.write_text("{not a list}", encoding="utf-8")
        templates = TemplatesStore().load_from_disk()
        # Falls back to in-memory defaults; doesn't write defaults to disk
        assert {t.id for t in templates} == {
            "refactor-module",
            "fix-bug",
            "add-tests",
        }

    @pytest.mark.asyncio
    async def test_add_update_delete(self, store: TemplatesStore) -> None:
        created = await store.add(
            name="my-template",
            description="desc",
            body="hi {who}",
            params=[TemplateParam(name="who", label="谁")],
        )
        assert created.id.startswith("tpl_")

        items = await store.list()
        assert any(t.id == created.id for t in items)

        updated = await store.update(created.id, name="renamed")
        assert updated is not None and updated.name == "renamed"

        # update for missing id returns None
        assert await store.update("nope") is None

        ok = await store.delete(created.id)
        assert ok is True
        assert await store.delete(created.id) is False
        items = await store.list()
        assert all(t.id != created.id for t in items)

    @pytest.mark.asyncio
    async def test_persistence_across_instances(
        self, isolated_path: Path
    ) -> None:
        s1 = TemplatesStore()
        await s1.add(name="X", description="", body="hello {name}",
                     params=[TemplateParam(name="name", label="名")])
        # Fresh instance must read what was persisted
        s2 = TemplatesStore()
        items = await s2.list()
        assert any(t.name == "X" for t in items)


# ── render_template tests ────────────────────────────────────────────────────


class TestRenderTemplate:
    def test_simple_substitution(self) -> None:
        tpl = Template(
            id="t",
            name="t",
            description="",
            body="hello {name}",
            params=[TemplateParam(name="name", label="名")],
        )
        assert render_template(tpl, {"name": "world"}) == "hello world"

    def test_uses_default_when_param_missing(self) -> None:
        tpl = Template(
            id="t",
            name="t",
            description="",
            body="{greet} {name}",
            params=[
                TemplateParam(name="greet", label="g", default="hi"),
                TemplateParam(name="name", label="n"),
            ],
        )
        assert render_template(tpl, {"name": "x"}) == "hi x"

    def test_missing_required_param_raises(self) -> None:
        tpl = Template(id="t", name="t", description="", body="{a} {b}")
        with pytest.raises(MissingParamsError) as exc:
            render_template(tpl, {"a": "1"})
        assert exc.value.missing == ["b"]

    def test_escaped_braces(self) -> None:
        tpl = Template(
            id="t",
            name="t",
            description="",
            body="dict literal {{a: 1}} and {x}",
        )
        assert render_template(tpl, {"x": "v"}) == "dict literal {a: 1} and v"

    def test_unused_params_are_ignored(self) -> None:
        tpl = Template(id="t", name="t", description="", body="x={x}")
        assert render_template(tpl, {"x": "1", "extra": "ignored"}) == "x=1"


# ── HTTP route tests ─────────────────────────────────────────────────────────


class TestTemplatesAPI:
    def test_list_returns_defaults(self, client: TestClient) -> None:
        r = client.get("/control-plane/templates")
        assert r.status_code == 200
        data = r.json()
        ids = {t["id"] for t in data["templates"]}
        assert ids == {"refactor-module", "fix-bug", "add-tests"}

    def test_create_template(self, client: TestClient) -> None:
        r = client.post(
            "/control-plane/templates",
            json={
                "name": "demo",
                "description": "",
                "body": "hello {name}",
                "params": [{"name": "name", "label": "名"}],
            },
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["id"].startswith("tpl_")
        assert data["name"] == "demo"

    def test_patch_template(self, client: TestClient) -> None:
        # Patch a built-in
        r = client.patch(
            "/control-plane/templates/fix-bug",
            json={"description": "patched"},
        )
        assert r.status_code == 200
        assert r.json()["description"] == "patched"

    def test_patch_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.patch(
            "/control-plane/templates/does-not-exist",
            json={"name": "x"},
        )
        assert r.status_code == 404

    def test_delete_template(self, client: TestClient) -> None:
        r = client.delete("/control-plane/templates/add-tests")
        assert r.status_code == 204
        # And it is really gone
        r2 = client.get("/control-plane/templates")
        ids = {t["id"] for t in r2.json()["templates"]}
        assert "add-tests" not in ids

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.delete("/control-plane/templates/none")
        assert r.status_code == 404

    def test_render_success(self, client: TestClient) -> None:
        r = client.post(
            "/control-plane/templates/refactor-module/render",
            json={
                "params": {
                    "module": "core/foo.py",
                    "abstraction": "Repository 接口",
                    "extra": "保留向后兼容",
                }
            },
        )
        assert r.status_code == 200, r.text
        rendered = r.json()["rendered"]
        assert "core/foo.py" in rendered
        assert "Repository 接口" in rendered
        assert "保留向后兼容" in rendered

    def test_render_uses_param_default(self, client: TestClient) -> None:
        # add-tests has framework default = pytest
        r = client.post(
            "/control-plane/templates/add-tests/render",
            json={"params": {"target": "agent.foo"}},
        )
        assert r.status_code == 200
        rendered = r.json()["rendered"]
        assert "agent.foo" in rendered
        assert "pytest" in rendered

    def test_render_missing_params_returns_422(
        self, client: TestClient
    ) -> None:
        r = client.post(
            "/control-plane/templates/fix-bug/render",
            json={"params": {"description": "X"}},
        )
        assert r.status_code == 422
        body = r.json()
        # error envelope
        assert body["error"]["code"] == "http_422"
        # The HTTPException's dict detail gets stringified into ``message``;
        # the missing param names must appear somewhere in the payload.
        text = json.dumps(body, ensure_ascii=False)
        for key in ("steps", "expected", "actual"):
            assert key in text

    def test_render_template_not_found(self, client: TestClient) -> None:
        r = client.post(
            "/control-plane/templates/unknown/render",
            json={"params": {}},
        )
        assert r.status_code == 404

    def test_create_then_round_trip(self, client: TestClient) -> None:
        cr = client.post(
            "/control-plane/templates",
            json={
                "name": "rt",
                "body": "X={x}",
                "params": [{"name": "x", "label": "x"}],
            },
        )
        tid = cr.json()["id"]
        ren = client.post(
            f"/control-plane/templates/{tid}/render",
            json={"params": {"x": "42"}},
        )
        assert ren.status_code == 200
        assert ren.json()["rendered"] == "X=42"

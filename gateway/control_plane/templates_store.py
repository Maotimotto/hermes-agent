"""任务模板存储模块（V1.1 P1 任务模板）。

存储路径：``~/.hermes/control-plane-templates.json``。文件格式是一个 JSON list，
每个 element 为：

.. code-block:: json

    {
      "id": "refactor-module",
      "name": "重构模块",
      "description": "...",
      "body": "请帮我重构 {module}...",
      "params": [
        {"name": "module", "label": "模块名", "default": null, "placeholder": "..."}
      ]
    }

模块行为：

* 首次启动（文件不存在时）写入 3 个内置默认模板，之后不会再覆盖用户修改。
* 单进程内通过简单的内存缓存 + asyncio.Lock 序列化磁盘 IO。
* 测试用 ``monkeypatch`` 把 ``_default_path`` 换到 ``tmp_path``，避免污染真实目录。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ── Data classes ────────────────────────────────────────────────────────────


@dataclass
class TemplateParam:
    """单个模板参数。"""

    name: str
    label: str
    default: str | None = None
    placeholder: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None or k in ("name", "label")}


@dataclass
class Template:
    """任务模板。"""

    id: str
    name: str
    description: str
    body: str
    params: list[TemplateParam] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "body": self.body,
            "params": [p.to_dict() for p in self.params],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Template":
        params = [
            TemplateParam(
                name=p["name"],
                label=p.get("label") or p["name"],
                default=p.get("default"),
                placeholder=p.get("placeholder"),
            )
            for p in (data.get("params") or [])
        ]
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            body=data["body"],
            params=params,
        )


# ── Built-in defaults ───────────────────────────────────────────────────────


def _builtin_defaults() -> list[Template]:
    """V1.1 P1 三个内置模板。"""
    return [
        Template(
            id="refactor-module",
            name="重构模块",
            description="按既定接口重构指定模块并补单测",
            body=(
                "请帮我重构 {module} 模块。要求：\n"
                "- 保持外部接口不变\n"
                "- 抽出{abstraction}\n"
                "- 加单元测试\n\n"
                "附加要求：{extra}"
            ),
            params=[
                TemplateParam(name="module", label="模块路径", placeholder="例：agent/control_plane/store.py"),
                TemplateParam(name="abstraction", label="想抽出的抽象", placeholder="例：SessionStore 接口"),
                TemplateParam(name="extra", label="附加要求", default="无", placeholder="可留空"),
            ],
        ),
        Template(
            id="fix-bug",
            name="修复 bug",
            description="按描述定位并修复 bug",
            body=(
                "我遇到一个 bug：{description}\n\n"
                "复现步骤：{steps}\n"
                "期望行为：{expected}\n"
                "实际行为：{actual}\n\n"
                "请定位根因并修复。"
            ),
            params=[
                TemplateParam(name="description", label="问题简述"),
                TemplateParam(name="steps", label="复现步骤"),
                TemplateParam(name="expected", label="期望行为"),
                TemplateParam(name="actual", label="实际行为"),
            ],
        ),
        Template(
            id="add-tests",
            name="添加测试",
            description="为目标补全单元测试",
            body=(
                "为 {target} 补全测试，覆盖：\n"
                "- 正常路径\n"
                "- 边界 / 异常\n\n"
                "使用 {framework}。"
            ),
            params=[
                TemplateParam(name="target", label="测试目标", placeholder="模块/函数/类"),
                TemplateParam(name="framework", label="测试框架", default="pytest"),
            ],
        ),
    ]


# ── Path resolution ─────────────────────────────────────────────────────────


def _default_path() -> str:
    """返回模板存储文件的默认路径。

    暴露成模块级函数（而非常量），方便测试用 ``monkeypatch.setattr`` 替换。
    """
    return os.path.expanduser("~/.hermes/control-plane-templates.json")


# ── Store ───────────────────────────────────────────────────────────────────


class TemplatesStore:
    """文件支持的模板 store，进程内单例。

    并发：
      * 异步 API（``list/add/update/delete``）用 ``asyncio.Lock`` 串行化。
      * 同步加载用 ``threading.Lock`` 保护内存缓存。
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _default_path()
        self._cache: list[Template] | None = None
        self._sync_lock = threading.Lock()
        self._async_lock = asyncio.Lock()

    @property
    def path(self) -> str:
        return self._path

    # —— 内部 IO ——

    def _load_unlocked(self) -> list[Template]:
        if not os.path.exists(self._path):
            defaults = _builtin_defaults()
            self._write_unlocked(defaults)
            return defaults
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "[templates] failed to read %s (%s); falling back to defaults",
                self._path,
                exc,
            )
            defaults = _builtin_defaults()
            return defaults
        if not isinstance(raw, list):
            logger.warning(
                "[templates] %s is not a list; falling back to defaults", self._path
            )
            return _builtin_defaults()
        templates: list[Template] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                templates.append(Template.from_dict(item))
            except KeyError as exc:
                logger.warning("[templates] skipping malformed entry: missing %s", exc)
        return templates

    def _write_unlocked(self, templates: Iterable[Template]) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        payload = [t.to_dict() for t in templates]
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    def _load_cached(self) -> list[Template]:
        with self._sync_lock:
            if self._cache is None:
                self._cache = self._load_unlocked()
            return list(self._cache)

    def _save_and_cache(self, templates: list[Template]) -> None:
        with self._sync_lock:
            self._write_unlocked(templates)
            self._cache = list(templates)

    # —— public sync helpers (mostly for tests) ——

    def load_from_disk(self) -> list[Template]:
        """强制从磁盘重读，刷新缓存。"""
        with self._sync_lock:
            self._cache = self._load_unlocked()
            return list(self._cache)

    def reset_cache(self) -> None:
        with self._sync_lock:
            self._cache = None

    # —— public async API ——

    async def list(self) -> list[Template]:
        async with self._async_lock:
            return self._load_cached()

    async def get(self, template_id: str) -> Template | None:
        async with self._async_lock:
            for t in self._load_cached():
                if t.id == template_id:
                    return t
            return None

    async def add(
        self,
        *,
        name: str,
        description: str,
        body: str,
        params: list[TemplateParam] | None = None,
        template_id: str | None = None,
    ) -> Template:
        async with self._async_lock:
            current = self._load_cached()
            new_id = template_id or _new_id()
            if any(t.id == new_id for t in current):
                raise ValueError(f"Template id already exists: {new_id}")
            tpl = Template(
                id=new_id,
                name=name,
                description=description,
                body=body,
                params=list(params or []),
            )
            current.append(tpl)
            self._save_and_cache(current)
            return tpl

    async def update(
        self,
        template_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        body: str | None = None,
        params: list[TemplateParam] | None = None,
    ) -> Template | None:
        async with self._async_lock:
            current = self._load_cached()
            for i, t in enumerate(current):
                if t.id == template_id:
                    updated = Template(
                        id=t.id,
                        name=name if name is not None else t.name,
                        description=description if description is not None else t.description,
                        body=body if body is not None else t.body,
                        params=list(params) if params is not None else list(t.params),
                    )
                    current[i] = updated
                    self._save_and_cache(current)
                    return updated
            return None

    async def delete(self, template_id: str) -> bool:
        async with self._async_lock:
            current = self._load_cached()
            new_list = [t for t in current if t.id != template_id]
            if len(new_list) == len(current):
                return False
            self._save_and_cache(new_list)
            return True


# ── Render helpers ──────────────────────────────────────────────────────────


class MissingParamsError(ValueError):
    """渲染时传入的 params 缺少必填占位符。"""

    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"missing params: {', '.join(missing)}")
        self.missing = missing


def render_template(template: Template, params: dict[str, Any] | None) -> str:
    """把 ``{key}`` 占位符按 ``params`` 替换；缺参数抛 ``MissingParamsError``。

    解析规则：
      * 仅识别 ``{name}`` 形式（name 为标识符）。
      * ``{{`` / ``}}`` 转义保留为 ``{`` / ``}``。
      * 优先用调用方传入的值；否则取 template 参数定义里的 ``default``；
        都没有则收集为 missing。
    """
    import re

    provided = params or {}
    defaults = {p.name: p.default for p in template.params if p.default is not None}

    missing: list[str] = []
    used_keys: set[str] = set()

    PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")

    def _replace(match: "re.Match[str]") -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        key = match.group(1)
        used_keys.add(key)
        if key in provided and provided[key] is not None:
            return str(provided[key])
        if key in defaults:
            return str(defaults[key])
        missing.append(key)
        return ""

    rendered = PLACEHOLDER.sub(_replace, template.body)
    if missing:
        # 去重，保留首次出现顺序
        seen: list[str] = []
        for m in missing:
            if m not in seen:
                seen.append(m)
        raise MissingParamsError(seen)
    return rendered


def _new_id() -> str:
    return f"tpl_{uuid.uuid4().hex[:12]}"


__all__ = [
    "Template",
    "TemplateParam",
    "TemplatesStore",
    "MissingParamsError",
    "render_template",
]

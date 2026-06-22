"""Task template routes for the Daemon API control plane.

Endpoints:
  GET    /templates              — list all templates
  POST   /templates              — create a new template (server-issued id)
  PATCH  /templates/{id}         — update an existing template
  DELETE /templates/{id}         — delete a template
  POST   /templates/{id}/render  — render template body with provided params

Storage is delegated to :class:`TemplatesStore`; the singleton lives on
``AppState`` (``state.templates_store``) and is created lazily via
``get_templates_store`` so tests can monkey-patch the storage path.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from gateway.control_plane.deps import AppState, get_app_state
from gateway.control_plane.schemas import (
    TemplateCreate,
    TemplateListResponse,
    TemplateRenderRequest,
    TemplateRenderResponse,
    TemplateSchema,
    TemplateUpdate,
)
from gateway.control_plane.templates_store import (
    MissingParamsError,
    Template,
    TemplateParam,
    TemplatesStore,
    render_template,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])


# ── helpers ──────────────────────────────────────────────────────────────────


def get_templates_store(state: AppState = Depends(get_app_state)) -> TemplatesStore:
    """Return the per-app TemplatesStore, creating it on first access."""
    if getattr(state, "templates_store", None) is None:
        state.templates_store = TemplatesStore()
    return state.templates_store  # type: ignore[return-value]


def _to_schema(t: Template) -> TemplateSchema:
    return TemplateSchema(**t.to_dict())


def _params_from_schemas(items) -> list[TemplateParam]:
    return [
        TemplateParam(
            name=p.name,
            label=p.label,
            default=p.default,
            placeholder=p.placeholder,
        )
        for p in items
    ]


# ── GET /templates ──────────────────────────────────────────────────────────


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    store: TemplatesStore = Depends(get_templates_store),
) -> TemplateListResponse:
    items = await store.list()
    return TemplateListResponse(templates=[_to_schema(t) for t in items])


# ── POST /templates ─────────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=TemplateSchema)
async def create_template(
    body: TemplateCreate,
    store: TemplatesStore = Depends(get_templates_store),
) -> TemplateSchema:
    tpl = await store.add(
        name=body.name,
        description=body.description,
        body=body.body,
        params=_params_from_schemas(body.params),
    )
    return _to_schema(tpl)


# ── PATCH /templates/{id} ───────────────────────────────────────────────────


@router.patch("/{template_id}", response_model=TemplateSchema)
async def update_template(
    template_id: str,
    body: TemplateUpdate,
    store: TemplatesStore = Depends(get_templates_store),
) -> TemplateSchema:
    params = (
        _params_from_schemas(body.params) if body.params is not None else None
    )
    updated = await store.update(
        template_id,
        name=body.name,
        description=body.description,
        body=body.body,
        params=params,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
    return _to_schema(updated)


# ── DELETE /templates/{id} ──────────────────────────────────────────────────


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    store: TemplatesStore = Depends(get_templates_store),
) -> None:
    ok = await store.delete(template_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
    return None


# ── POST /templates/{id}/render ─────────────────────────────────────────────


@router.post("/{template_id}/render", response_model=TemplateRenderResponse)
async def render_template_endpoint(
    template_id: str,
    body: TemplateRenderRequest,
    store: TemplatesStore = Depends(get_templates_store),
) -> TemplateRenderResponse:
    tpl = await store.get(template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
    try:
        rendered = render_template(tpl, body.params)
    except MissingParamsError as exc:
        # 422 — request was syntactically valid but semantically incomplete.
        raise HTTPException(
            status_code=422,
            detail={
                "code": "missing_params",
                "missing": exc.missing,
                "message": str(exc),
            },
        )
    return TemplateRenderResponse(rendered=rendered)

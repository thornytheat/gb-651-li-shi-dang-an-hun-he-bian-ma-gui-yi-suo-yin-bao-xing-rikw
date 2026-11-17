from fastapi import APIRouter, Body, Request
from app.errors import DomainError
router = APIRouter(tags=["M4-内容归一"])

def _svc(request: Request):
    return request.app.state.content_service

def _require(payload: dict, *keys: str) -> None:
    missing = [k for k in keys if payload.get(k) is None]
    if missing:
        raise DomainError("INVALID_VALUE", "缺少字段: " + ",".join(missing))

@router.get("/content")
def page(request: Request):
    data = _svc(request).state()
    return request.app.state.templates.TemplateResponse(request, "m4_content.html", {"title": "内容归一", "data": data})

@router.get("/api/content/state")
def state(request: Request):
    return _svc(request).state()

@router.post("/api/content/load")
def load(request: Request):
    return _svc(request).load()

@router.post("/api/content/normalize")
def normalize(request: Request, payload: dict = Body(default={})):
    payload = payload or {}
    return _svc(request).normalize(profile=str(payload.get("profile", "standard")))

@router.post("/api/content/revise")
def revise(request: Request, payload: dict = Body(default={})):
    payload = payload or {}
    _require(payload, "archive_id", "line_no", "base_version")
    return _svc(request).revise(str(payload["archive_id"]), str(payload["line_no"]),
                                str(payload.get("value", "")), payload["base_version"])

@router.post("/api/content/undo")
def undo(request: Request, payload: dict = Body(default={})):
    payload = payload or {}
    _require(payload, "op_id")
    return _svc(request).undo(str(payload["op_id"]))

@router.post("/api/content/adjudicate")
def adjudicate(request: Request, payload: dict = Body(default={})):
    payload = payload or {}
    _require(payload, "undo_id", "archive_id", "line_no", "decision", "conflict_version")
    return _svc(request).adjudicate(str(payload["undo_id"]), str(payload["archive_id"]),
                                    str(payload["line_no"]), str(payload["decision"]),
                                    payload["conflict_version"])

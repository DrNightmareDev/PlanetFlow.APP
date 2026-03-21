from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.dependencies import require_account
from app.templates_env import templates

router = APIRouter(prefix="/pi", tags=["pi"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def pi_chains(
    request: Request,
    account=Depends(require_account),
):
    return templates.TemplateResponse("placeholder.html", {
        "request": request,
        "account": account,
        "title": "PI Chains",
        "message": "PI Chain Visualizer wird noch entwickelt.",
        "icon": "bi-diagram-3",
    })

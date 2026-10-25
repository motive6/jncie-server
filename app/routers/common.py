from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse

from ..security import verify_csrf


def redirect(url: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    params = []
    if msg:
        params.append("msg=" + quote(msg))
    if err:
        params.append("err=" + quote(err))
    if params:
        url += ("&" if "?" in url else "?") + "&".join(params)
    return RedirectResponse(url, status_code=303)


def check_csrf(request: Request, form) -> bool:
    return verify_csrf(request, str(form.get("csrf", "")))


def fv(form, key: str, default: str = "") -> str:
    value = form.get(key, default)
    return str(value).strip() if value is not None else default


def fv_lines(form, key: str) -> list[str]:
    return [line.strip() for line in fv(form, key).splitlines() if line.strip()]

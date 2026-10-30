import secrets
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import config
from .db import Coupon, Offer, Product, SearchTerm, SessionLocal, init_db
from .pricing import affiliate_link, brl

app = FastAPI(title="Ofertas ML")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["brl"] = brl
templates.env.globals["aff"] = affiliate_link


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with SessionLocal() as s:
        rows = (
            s.query(Offer, Product).join(Product, Product.id == Offer.product_id)
            .order_by(Offer.discount_pct.desc()).limit(100).all()
        )
    return templates.TemplateResponse(request, "index.html", {"rows": rows})


def _check(token: str) -> None:
    if not config.ADMIN_TOKEN or not secrets.compare_digest(token or "", config.ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="Acesso negado")


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, token: str = ""):
    _check(token)
    with SessionLocal() as s:
        coupons = s.query(Coupon).order_by(Coupon.id.desc()).all()
        terms = s.query(SearchTerm).order_by(SearchTerm.id.desc()).all()
    return templates.TemplateResponse(
        request, "admin.html",
        {"coupons": coupons, "terms": terms, "token": token, "fallback": config.SEARCH_TERMS},
    )


@app.post("/admin/coupons")
def add_coupon(
    token: str = Form(""), code: str = Form(...), percent: float = Form(0), fixed_amount: float = Form(0),
    max_discount: float = Form(0), min_purchase: float = Form(0), valid_until: str = Form(""),
    terms: str = Form(""), campaign_url: str = Form(""),
):
    _check(token)
    until = None
    if valid_until:
        until = datetime.strptime(valid_until, "%Y-%m-%d") + timedelta(hours=23, minutes=59)
    with SessionLocal() as s:
        s.add(Coupon(code=code.strip(), percent=percent, fixed_amount=fixed_amount, max_discount=max_discount,
                     min_purchase=min_purchase, valid_until=until, terms=terms, campaign_url=campaign_url))
        s.commit()
    return RedirectResponse(f"/admin?token={token}", status_code=303)


@app.post("/admin/coupons/{cid}/delete")
def delete_coupon(cid: int, token: str = Form("")):
    _check(token)
    with SessionLocal() as s:
        c = s.get(Coupon, cid)
        if c:
            s.delete(c)
            s.commit()
    return RedirectResponse(f"/admin?token={token}", status_code=303)


@app.post("/admin/terms")
def add_terms(token: str = Form(""), terms: str = Form(...)):
    _check(token)
    items = {t.strip() for t in terms.replace("\n", ",").split(",") if t.strip()}
    with SessionLocal() as s:
        existing = {t.term.lower() for t in s.query(SearchTerm).all()}
        for t in sorted(items):
            if t.lower() not in existing:
                s.add(SearchTerm(term=t[:200]))
        s.commit()
    return RedirectResponse(f"/admin?token={token}", status_code=303)


@app.post("/admin/terms/{tid}/toggle")
def toggle_term(tid: int, token: str = Form("")):
    _check(token)
    with SessionLocal() as s:
        t = s.get(SearchTerm, tid)
        if t:
            t.active = not t.active
            s.commit()
    return RedirectResponse(f"/admin?token={token}", status_code=303)


@app.post("/admin/terms/{tid}/delete")
def delete_term(tid: int, token: str = Form("")):
    _check(token)
    with SessionLocal() as s:
        t = s.get(SearchTerm, tid)
        if t:
            s.delete(t)
            s.commit()
    return RedirectResponse(f"/admin?token={token}", status_code=303)

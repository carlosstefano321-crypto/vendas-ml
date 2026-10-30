import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func

from . import config, worker
from .db import Coupon, Offer, Product, SearchTerm, Setting, SessionLocal, init_db, utcnow
from .pricing import affiliate_link, brl

app = FastAPI(title="Ofertas ML")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["brl"] = brl
templates.env.globals["aff"] = affiliate_link


def br_time(dt):
    return "—" if not dt else (dt - timedelta(hours=3)).strftime("%d/%m %H:%M")


templates.env.filters["br_time"] = br_time


# ---------- busca rodando dentro do site ----------
_lock = threading.Lock()
RUN = {"running": False, "result": None}


def _do_run() -> None:
    if not _lock.acquire(blocking=False):
        return
    try:
        RUN["running"] = True
        try:
            RUN["result"] = worker.run()
        except Exception as e:  # noqa: BLE001
            RUN["result"] = {"terms_ok": 0, "items": 0, "offers": 0,
                             "errors": [f"Erro inesperado: {str(e)[:300]}"], "finished": utcnow()}
    finally:
        RUN["running"] = False
        _lock.release()


def _auto_loop(minutes: int) -> None:
    time.sleep(90)
    while True:
        _do_run()
        time.sleep(minutes * 60)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    try:
        minutes = int(os.getenv("AUTO_REFRESH_MINUTES") or 30)
    except ValueError:
        minutes = 30
    if minutes > 0:
        threading.Thread(target=_auto_loop, args=(minutes,), daemon=True).start()


# ---------- acesso ----------
def is_admin(request: Request) -> bool:
    token = request.cookies.get("admin_token", "")
    return bool(config.ADMIN_TOKEN) and secrets.compare_digest(token.encode(), config.ADMIN_TOKEN.encode())


def need_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def page(request: Request, name: str, active: str, **ctx):
    return templates.TemplateResponse(request, name, {"admin": is_admin(request), "active": active, **ctx})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return page(request, "login.html", "login", error=False)


@app.post("/login")
def login(request: Request, token: str = Form("")):
    ok = bool(config.ADMIN_TOKEN) and secrets.compare_digest(token.encode(), config.ADMIN_TOKEN.encode())
    if not ok:
        resp = page(request, "login.html", "login", error=True)
        resp.status_code = 401
        return resp
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("admin_token", token, httponly=True, secure=True, samesite="lax", max_age=60 * 60 * 24 * 365)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("admin_token")
    return resp


@app.get("/admin")
def old_admin():
    return RedirectResponse("/cupons", status_code=303)


# ---------- páginas ----------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    need_admin(request)
    with SessionLocal() as s:
        stats = {
            "n_offers": s.query(func.count(Offer.product_id)).scalar() or 0,
            "best": s.query(func.max(Offer.discount_pct)).scalar(),
            "n_coupons": s.query(func.count(Coupon.id)).filter(Coupon.active.is_(True)).scalar() or 0,
            "n_terms": s.query(func.count(SearchTerm.id)).filter(SearchTerm.active.is_(True)).scalar() or 0,
            "last": s.query(func.max(Product.updated_at)).scalar(),
        }
    return page(request, "index.html", "inicio", **stats)


@app.get("/ofertas", response_class=HTMLResponse)
def ofertas(request: Request):
    need_admin(request)
    with SessionLocal() as s:
        rows = (
            s.query(Offer, Product).join(Product, Product.id == Offer.product_id)
            .order_by(Offer.discount_pct.desc()).limit(100).all()
        )
    return page(request, "ofertas.html", "ofertas", rows=rows)


@app.get("/cupons", response_class=HTMLResponse)
def cupons(request: Request):
    need_admin(request)
    with SessionLocal() as s:
        coupons = s.query(Coupon).order_by(Coupon.id.desc()).all()
    return page(request, "cupons.html", "cupons", coupons=coupons)


@app.post("/cupons")
def add_coupon(
    request: Request, code: str = Form(...), percent: float = Form(0), fixed_amount: float = Form(0),
    max_discount: float = Form(0), min_purchase: float = Form(0), valid_until: str = Form(""),
    terms: str = Form(""), campaign_url: str = Form(""),
):
    need_admin(request)
    until = None
    if valid_until:
        until = datetime.strptime(valid_until, "%Y-%m-%d") + timedelta(hours=23, minutes=59)
    with SessionLocal() as s:
        s.add(Coupon(code=code.strip(), percent=percent, fixed_amount=fixed_amount, max_discount=max_discount,
                     min_purchase=min_purchase, valid_until=until, terms=terms, campaign_url=campaign_url))
        s.commit()
    return RedirectResponse("/cupons", status_code=303)


@app.post("/cupons/{cid}/delete")
def delete_coupon(request: Request, cid: int):
    need_admin(request)
    with SessionLocal() as s:
        c = s.get(Coupon, cid)
        if c:
            s.delete(c)
            s.commit()
    return RedirectResponse("/cupons", status_code=303)


@app.get("/buscas", response_class=HTMLResponse)
def buscas(request: Request):
    need_admin(request)
    with SessionLocal() as s:
        terms = s.query(SearchTerm).order_by(SearchTerm.id.desc()).all()
        min_pct = worker.get_min_pct(s)
    return page(request, "buscas.html", "buscas", terms=terms, fallback=config.SEARCH_TERMS, min_pct=min_pct)


@app.post("/buscas")
def add_terms(request: Request, terms: str = Form(...)):
    need_admin(request)
    items = {t.strip() for t in terms.replace("\n", ",").split(",") if t.strip()}
    with SessionLocal() as s:
        existing = {t.term.lower() for t in s.query(SearchTerm).all()}
        for t in sorted(items):
            if t.lower() not in existing:
                s.add(SearchTerm(term=t[:200]))
        s.commit()
    return RedirectResponse("/buscas", status_code=303)


@app.post("/buscas/{tid}/toggle")
def toggle_term(request: Request, tid: int):
    need_admin(request)
    with SessionLocal() as s:
        t = s.get(SearchTerm, tid)
        if t:
            t.active = not t.active
            s.commit()
    return RedirectResponse("/buscas", status_code=303)


@app.post("/buscas/{tid}/delete")
def delete_term(request: Request, tid: int):
    need_admin(request)
    with SessionLocal() as s:
        t = s.get(SearchTerm, tid)
        if t:
            s.delete(t)
            s.commit()
    return RedirectResponse("/buscas", status_code=303)


@app.get("/status", response_class=HTMLResponse)
def status(request: Request):
    need_admin(request)
    with SessionLocal() as s:
        info = {
            "last": s.query(func.max(Product.updated_at)).scalar(),
            "n_products": s.query(func.count(Product.id)).scalar() or 0,
            "n_terms": s.query(func.count(SearchTerm.id)).filter(SearchTerm.active.is_(True)).scalar() or 0,
            "min_pct": worker.get_min_pct(s),
        }
    checks = [
        ("Credenciais do Mercado Livre", bool(config.ML_CLIENT_ID and config.ML_CLIENT_SECRET)),
        ("Link de afiliado configurado", config.AFFILIATE_URL_TEMPLATE.strip() not in ("", "{url}")),
        ("Telegram configurado", bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)),
    ]
    return page(request, "status.html", "status", checks=checks, run=RUN, **info)


@app.post("/atualizar")
def atualizar(request: Request):
    need_admin(request)
    if not RUN["running"]:
        RUN["running"] = True
        threading.Thread(target=_do_run, daemon=True).start()
    return RedirectResponse("/status", status_code=303)


@app.post("/config/desconto")
def set_min_discount(request: Request, pct: float = Form(...)):
    need_admin(request)
    pct = max(0.0, min(pct, 95.0))
    with SessionLocal() as s:
        row = s.get(Setting, "min_discount_pct")
        if row is None:
            s.add(Setting(key="min_discount_pct", value=str(pct)))
        else:
            row.value = str(pct)
        s.commit()
    return RedirectResponse("/buscas", status_code=303)

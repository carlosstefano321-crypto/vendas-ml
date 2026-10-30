"""Rotina de busca: coleta preços, aplica cupons, gera ofertas e (opcional) publica no Telegram.

É chamada pelo botão "Buscar agora" do site e pela atualização automática.
Também pode rodar sozinha:  python -m app.worker
"""
import html
import statistics
from datetime import timedelta

import requests

from . import config, ml_client
from .db import Coupon, Offer, PricePoint, Product, SearchTerm, Setting, SessionLocal, init_db, utcnow
from .pricing import affiliate_link, best_deal, brl


def _explain(e: Exception) -> str:
    resp = getattr(e, "response", None)
    if resp is not None:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    return str(e)[:200]


def get_terms(s) -> list[str]:
    """Termos cadastrados no site; se não houver nenhum, usa a variável SEARCH_TERMS."""
    terms = [t.term for t in s.query(SearchTerm).filter(SearchTerm.active.is_(True)).order_by(SearchTerm.id).all()]
    return terms or config.SEARCH_TERMS


def get_min_pct(s) -> float:
    row = s.get(Setting, "min_discount_pct")
    try:
        return float(row.value) if row and row.value != "" else config.MIN_DISCOUNT_PCT
    except ValueError:
        return config.MIN_DISCOUNT_PCT


def collect(s, run_start, errors: list) -> tuple[int, int]:
    ok_terms, n_items = 0, 0
    for term in get_terms(s):
        try:
            items = ml_client.search(term, config.RESULTS_PER_TERM)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Busca '{term}': {_explain(e)}")
            continue
        ok_terms += 1
        n_items += len(items)
        for it in items:
            p = s.get(Product, it["id"])
            if p is None:
                p = Product(id=it["id"])
                s.add(p)
            p.title, p.price, p.original_price = it["title"], it["price"], it["original_price"]
            p.permalink, p.thumbnail, p.free_shipping = it["permalink"], it["thumbnail"], it["free_shipping"]
            p.term, p.updated_at = term, run_start
            s.flush()
            s.add(PricePoint(product_id=p.id, price=p.price, ts=run_start))
        s.commit()
    return ok_terms, n_items


def reference_price(s, p, run_start):
    since = run_start - timedelta(days=30)
    prices = [
        row.price
        for row in s.query(PricePoint)
        .filter(PricePoint.product_id == p.id, PricePoint.ts >= since, PricePoint.ts < run_start)
        .all()
    ]
    if len(prices) >= 3:
        return statistics.median(prices)
    if p.original_price and p.original_price > p.price:
        return p.original_price
    return None


def build_offers(s, run_start, min_pct: float) -> int:
    now = utcnow()
    coupons = s.query(Coupon).filter(Coupon.active.is_(True)).all()
    keep = set()
    for p in s.query(Product).filter(Product.updated_at >= run_start).all():
        ref = reference_price(s, p, run_start)
        if not ref:
            continue
        final, coupon = best_deal(p.price, p.term, coupons, now)
        pct = (ref - final) / ref * 100
        if pct < min_pct:
            continue
        keep.add(p.id)
        code = coupon.code if coupon else None
        o = s.get(Offer, p.id)
        if o is None:
            s.add(Offer(product_id=p.id, coupon_code=code, price=p.price, final_price=final,
                        reference_price=ref, discount_pct=round(pct, 1), created_at=now))
        else:
            if abs(o.final_price - final) > 0.01 or o.coupon_code != code:
                o.posted = False
            o.coupon_code, o.price, o.final_price = code, p.price, final
            o.reference_price, o.discount_pct = ref, round(pct, 1)
    for o in s.query(Offer).all():
        if o.product_id not in keep:
            s.delete(o)
    s.commit()
    return len(keep)


def post_telegram(s, errors: list) -> None:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    pending = (
        s.query(Offer).filter(Offer.posted.is_(False)).order_by(Offer.discount_pct.desc())
        .limit(config.MAX_TELEGRAM_PER_RUN).all()
    )
    for o in pending:
        p = s.get(Product, o.product_id)
        lines = [f"🔥 <b>{html.escape(p.title)}</b>", ""]
        if o.coupon_code:
            lines += [f"💰 De {brl(o.reference_price)} por <b>{brl(o.final_price)}</b> com cupom",
                      f"🏷 Cupom: <code>{html.escape(o.coupon_code)}</code>"]
        else:
            lines += [f"💰 De {brl(o.reference_price)} por <b>{brl(o.final_price)}</b>"]
        lines += [f"📉 {o.discount_pct:.0f}% abaixo do preço de referência", "",
                  f'🛒 <a href="{html.escape(affiliate_link(p.permalink))}">Ver no Mercado Livre</a>', "",
                  "<i>Preços e cupons podem mudar a qualquer momento.</i>"]
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": config.TELEGRAM_CHAT_ID, "text": "\n".join(lines),
                      "parse_mode": "HTML", "disable_web_page_preview": False},
                timeout=20,
            )
            r.raise_for_status()
            o.posted = True
            s.commit()
        except Exception as e:  # noqa: BLE001
            errors.append("Telegram: falha ao publicar (confira o token e o ID do canal).")
            break


def cleanup(s) -> None:
    s.query(PricePoint).filter(PricePoint.ts < utcnow() - timedelta(days=60)).delete()
    s.commit()


def run() -> dict:
    init_db()
    run_start = utcnow()
    errors: list = []
    result = {"terms_ok": 0, "items": 0, "offers": 0, "errors": errors, "finished": None}
    with SessionLocal() as s:
        ok, n_items = collect(s, run_start, errors)
        result["terms_ok"], result["items"] = ok, n_items
        if ok == 0:
            errors.append("Nenhuma busca funcionou; ofertas anteriores mantidas.")
        else:
            result["offers"] = build_offers(s, run_start, get_min_pct(s))
            post_telegram(s, errors)
            cleanup(s)
    result["finished"] = utcnow()
    return result


if __name__ == "__main__":
    print(run())

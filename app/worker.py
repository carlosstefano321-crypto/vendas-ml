"""Rotina automática: busca preços, aplica cupons, gera ofertas e publica no Telegram.

Rode com:  python -m app.worker
"""
import html
import statistics
from datetime import timedelta

import requests

from . import config, ml_client
from .db import Coupon, Offer, PricePoint, Product, SearchTerm, SessionLocal, init_db, utcnow
from .pricing import affiliate_link, best_deal, brl


def get_terms(s) -> list[str]:
    """Termos cadastrados no /admin; se nao houver nenhum, usa a variavel SEARCH_TERMS."""
    terms = [t.term for t in s.query(SearchTerm).filter(SearchTerm.active.is_(True)).order_by(SearchTerm.id).all()]
    return terms or config.SEARCH_TERMS


def collect(s, run_start) -> int:
    ok_terms = 0
    for term in get_terms(s):
        try:
            items = ml_client.search(term, config.RESULTS_PER_TERM)
        except Exception as e:  # noqa: BLE001
            print(f"[erro] busca '{term}': {e}")
            continue
        ok_terms += 1
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
    return ok_terms


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


def build_offers(s, run_start) -> None:
    now = utcnow()
    coupons = s.query(Coupon).filter(Coupon.active.is_(True)).all()
    keep = set()
    for p in s.query(Product).filter(Product.updated_at >= run_start).all():
        ref = reference_price(s, p, run_start)
        if not ref:
            continue
        final, coupon = best_deal(p.price, p.term, coupons, now)
        pct = (ref - final) / ref * 100
        if pct < config.MIN_DISCOUNT_PCT:
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


def post_telegram(s) -> None:
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
            print(f"[erro] telegram: {e}")


def cleanup(s) -> None:
    s.query(PricePoint).filter(PricePoint.ts < utcnow() - timedelta(days=60)).delete()
    s.commit()


def run() -> None:
    init_db()
    run_start = utcnow()
    with SessionLocal() as s:
        if collect(s, run_start) == 0:
            print("Nenhuma busca funcionou; ofertas anteriores mantidas.")
            return
        build_offers(s, run_start)
        post_telegram(s)
        cleanup(s)
    print("Rotina concluída.")


if __name__ == "__main__":
    run()

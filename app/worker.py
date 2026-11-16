"""Rotina de busca: coleta preços, aplica cupons, gera ofertas e (opcional) publica no Telegram.

É chamada pelo botão "Buscar agora" do site e pela atualização automática.
Também pode rodar sozinha:  python -m app.worker
"""
import html
import statistics
from datetime import timedelta

import requests

from . import config, ml_client
from .db import Coupon, Offer, PricePoint, Product, ReviewQueue, SearchTerm, Setting, SessionLocal, init_db, utcnow
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


def build_message(product, offer) -> str:
    lines = [f"🔥 {product.title}", ""]
    if offer.coupon_code:
        lines += [f"💰 De {brl(offer.reference_price)} por {brl(offer.final_price)} com cupom",
                  f"🏷 Cupom: {offer.coupon_code}"]
    else:
        lines += [f"💰 De {brl(offer.reference_price)} por {brl(offer.final_price)}"]
    lines += [f"📉 {offer.discount_pct:.0f}% abaixo do preço de referência", "",
              f"🛒 {affiliate_link(product.permalink)}", "",
              "Preços e cupons podem mudar a qualquer momento."]
    return "\n".join(lines)


def build_offers(s, run_start, min_pct: float) -> int:
    now = utcnow()
    coupons = s.query(Coupon).filter(Coupon.active.is_(True)).all()
    count = 0
    for product in s.query(Product).filter(Product.updated_at >= run_start).all():
        reference = reference_price(s, product, run_start)
        if not reference:
            continue
        final, coupon = best_deal(product.price, product.term, coupons, now)
        discount_pct = (reference - final) / reference * 100
        if discount_pct < min_pct:
            continue
        code = coupon.code if coupon else None
        offer = s.get(Offer, product.id)
        changed = offer is None
        if offer is None:
            offer = Offer(product_id=product.id, coupon_code=code, price=product.price,
                          final_price=final, reference_price=reference,
                          discount_pct=round(discount_pct, 1), created_at=now)
            s.add(offer)
        else:
            changed = abs(offer.final_price - final) > 0.01 or offer.coupon_code != code
            offer.coupon_code, offer.price, offer.final_price = code, product.price, final
            offer.reference_price, offer.discount_pct = reference, round(discount_pct, 1)
            if changed:
                offer.posted = False
        review = s.get(ReviewQueue, product.id)
        if review is None:
            s.add(ReviewQueue(product_id=product.id, message=build_message(product, offer),
                              status="pending", created_at=now, updated_at=now))
        elif changed and review.status != "published":
            review.message = build_message(product, offer)
            review.status = "pending"
            review.updated_at = now
            review.reviewed_at = None
            review.error = ""
        count += 1
    # Uma busca parcial não deve apagar ofertas encontradas em buscas anteriores.
    s.commit()
    return count


def publish_telegram(s, review: ReviewQueue) -> None:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        raise RuntimeError("Configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID antes de publicar.")
    offer = s.get(Offer, review.product_id)
    if offer is None:
        raise RuntimeError("A oferta não existe mais.")
    response = requests.post(
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": config.TELEGRAM_CHAT_ID, "text": review.message,
              "disable_web_page_preview": False},
        timeout=20,
    )
    response.raise_for_status()
    review.status = "published"
    review.published_at = utcnow()
    review.reviewed_at = utcnow()
    review.error = ""
    offer.posted = True
    s.commit()

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
            # Publicação manual: a busca apenas cria itens na fila de revisão.
            cleanup(s)
    result["finished"] = utcnow()
    return result


if __name__ == "__main__":
    print(run())

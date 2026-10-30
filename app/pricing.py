from datetime import datetime

from . import config


def coupon_applies(c, price: float, term: str, now: datetime) -> bool:
    if not c.active:
        return False
    if c.valid_until and c.valid_until < now:
        return False
    if price < (c.min_purchase or 0):
        return False
    terms = [t.strip().lower() for t in (c.terms or "").split(",") if t.strip()]
    if terms and term.lower() not in terms:
        return False
    return True


def coupon_discount(c, price: float) -> float:
    d = c.fixed_amount if (c.fixed_amount or 0) > 0 else price * (c.percent or 0) / 100
    if (c.max_discount or 0) > 0:
        d = min(d, c.max_discount)
    return round(min(d, price), 2)


def best_deal(price: float, term: str, coupons, now: datetime):
    """Retorna (preço_final, cupom_ou_None)."""
    best_price, best_coupon = price, None
    for c in coupons:
        if coupon_applies(c, price, term, now):
            final = round(price - coupon_discount(c, price), 2)
            if final < best_price:
                best_price, best_coupon = final, c
    return best_price, best_coupon


def affiliate_link(url: str) -> str:
    return config.AFFILIATE_URL_TEMPLATE.replace("{url}", url)


def brl(v: float) -> str:
    s = f"{v:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")

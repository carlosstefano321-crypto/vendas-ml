"""Cliente da API do Mercado Livre.

ATENÇÃO: confirme em developers.mercadolivre.com.br quais endpoints seu app pode usar.
Se a busca exigir permissões extras, ajuste apenas este arquivo.
"""
import time

import requests

from . import config

API = "https://api.mercadolibre.com"
_token = {"value": None, "exp": 0.0}


def _get_token() -> str | None:
    if not (config.ML_CLIENT_ID and config.ML_CLIENT_SECRET):
        return None
    if _token["value"] and time.time() < _token["exp"] - 60:
        return _token["value"]
    r = requests.post(
        f"{API}/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": config.ML_CLIENT_ID,
            "client_secret": config.ML_CLIENT_SECRET,
        },
        headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    _token["value"] = data["access_token"]
    _token["exp"] = time.time() + int(data.get("expires_in", 3600))
    return _token["value"]


def search(term: str, limit: int) -> list[dict]:
    headers = {}
    token = _get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(
        f"{API}/sites/{config.ML_SITE}/search",
        params={"q": term, "limit": limit},
        headers=headers,
        timeout=20,
    )
    r.raise_for_status()
    items = []
    for it in r.json().get("results", []):
        if it.get("condition") not in (None, "new"):
            continue
        price = it.get("price")
        if not price:
            continue
        thumb = (it.get("thumbnail") or "").replace("http://", "https://")
        items.append(
            {
                "id": it["id"],
                "title": it.get("title", ""),
                "price": float(price),
                "original_price": float(it["original_price"]) if it.get("original_price") else None,
                "permalink": it.get("permalink", ""),
                "thumbnail": thumb,
                "free_shipping": bool((it.get("shipping") or {}).get("free_shipping")),
            }
        )
    return items

"""Cliente da API do Mercado Livre.

A busca geral de anúncios é tentada primeiro como recurso público. Se a API
exigir autenticação, o cliente tenta novamente com o token da aplicação.
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


def _search_request(term: str, limit: int, token: str | None = None):
    headers = {"accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.get(
        f"{API}/sites/{config.ML_SITE}/search",
        params={"q": term, "limit": limit},
        headers=headers,
        timeout=20,
    )


def search(term: str, limit: int) -> list[dict]:
    # Não envia token desnecessariamente: a busca geral é tratada como recurso público.
    r = _search_request(term, limit)

    # Algumas permissões/recursos podem exigir autenticação. Nesse caso, tenta
    # novamente com o token da aplicação, se as credenciais estiverem configuradas.
    if r.status_code in (401, 403):
        token = _get_token()
        if token:
            authenticated = _search_request(term, limit, token)
            if authenticated.status_code not in (401, 403):
                r = authenticated
            else:
                # Mantém a resposta autenticada para que o diagnóstico registre
                # o status e o request-id da última tentativa.
                r = authenticated

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

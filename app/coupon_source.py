"""Diagnóstico público da página de cupons do Mercado Livre.

Não usa cookies, login, tokens ou dados da conta do usuário. Esta etapa identifica
se a página entrega os dados no HTML ou se depende de chamadas feitas pelo JavaScript.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

COUPON_PAGE = "https://www.mercadolivre.com.br/cupons/filter"
API_HINTS = ("/api/", "api.mercadolibre.com", "/coupons", "/coupon", "/cupons", "/promotion", "/promotions", "graphql")


class _PageInspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.in_title = False
        self.scripts = []
        self.inline_script = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == "title":
            self.in_title = True
        elif tag.lower() == "script":
            src = attrs.get("src")
            if src:
                self.scripts.append(src)
        elif tag.lower() == "a":
            href = attrs.get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        # Captura texto inline de scripts sem guardar o HTML completo.
        if data and any(hint in data.lower() for hint in API_HINTS):
            self.inline_script.append(data[:250000])


def inspect_public_coupon_page() -> dict:
    response = requests.get(
        COUPON_PAGE,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MaxZoneCouponResearch/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
        },
        timeout=25,
        allow_redirects=True,
    )
    response.raise_for_status()

    parser = _PageInspector()
    parser.feed(response.text)

    candidates = set()
    sources = []
    for raw in parser.scripts:
        absolute = urljoin(response.url, raw)
        parsed = urlparse(absolute)
        sources.append(f"{parsed.scheme}://{parsed.netloc}{parsed.path}")
    for raw in parser.links:
        absolute = urljoin(response.url, raw)
        parsed = urlparse(absolute)
        value = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if any(hint in value.lower() for hint in API_HINTS):
            candidates.add(value)
    for script in parser.inline_script:
        for match in re.findall(r"""https?://[^\s"'<>\\]+|/(?:api/|coupons?/?|cupons?/?|promotions?/|graphql)[^\s"'<>\\]*""", script, re.I):
            absolute = urljoin(response.url, match)
            parsed = urlparse(absolute)
            value = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if any(hint in value.lower() for hint in API_HINTS):
                candidates.add(value)

    html_lower = response.text.lower()
    markers = {
        "texto_cupom": html_lower.count("cupom"),
        "texto_produtos_selecionados": html_lower.count("produtos selecionados"),
        "texto_conferir": html_lower.count("conferir"),
        "texto_novos": html_lower.count("novos"),
    }
    return {
        "url": response.url,
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "html_bytes": len(response.content),
        "title": " ".join(" ".join(parser.title_parts).split())[:180],
        "script_count": len(sources),
        "script_sources": list(dict.fromkeys(sources))[:30],
        "candidate_endpoints": sorted(candidates)[:40],
        "page_markers": markers,
        "message": (
            "A página respondeu sem sessão. Os dados parecem estar no HTML ou em recursos identificáveis."
            if markers["texto_cupom"] or candidates
            else "A página respondeu, mas o HTML não revelou os dados dos cupons. Provavelmente precisamos identificar as chamadas Fetch/XHR do navegador."
        ),
    }

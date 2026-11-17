"""Inspeciona a página pública de cupons e seus bundles JavaScript.

Não utiliza cookies, tokens ou sessão pessoal. Limites de tamanho e domínio evitam
transformar o diagnóstico em um crawler aberto.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

COUPON_PAGE = "https://www.mercadolivre.com.br/cupons/filter"
API_HINTS = (
    "/api/", "api.mercadolibre.com", "/coupons", "/coupon", "/cupons",
    "/promotion", "/promotions", "graphql", "user-products",
)
MAX_HTML_BYTES = 4_000_000
MAX_SCRIPT_BYTES = 2_000_000
MAX_SCRIPTS = 12
TIMEOUT = 18


class _PageInspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.in_title = False
        self.scripts = []
        self.inline_data = []
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
        if data and len(data) <= MAX_SCRIPT_BYTES:
            self.inline_data.append(data)


def _allowed_asset(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = (
        host == "mercadolivre.com.br"
        or host.endswith(".mercadolivre.com.br")
        or host.endswith(".mlstatic.com")
        or host.endswith(".mlcdn.com.br")
        or host.endswith(".mercadolibre.com")
    )
    return parsed.scheme == "https" and allowed


def _endpoint_candidates(text: str, base_url: str) -> set[str]:
    found = set()
    pattern = r"""https?://[^\s"'<>\\]+|/(?:[^\s"'<>\\]{1,180})"""
    for match in re.findall(pattern, text, re.I):
        candidate = match.rstrip("),;]")
        if not any(hint in candidate.lower() for hint in API_HINTS):
            continue
        absolute = urljoin(base_url, candidate)
        parsed = urlparse(absolute)
        if parsed.scheme in ("https",) and parsed.hostname:
            # Remove query strings: podem conter identificadores ou parâmetros voláteis.
            found.add(f"{parsed.scheme}://{parsed.netloc}{parsed.path}"[:500])
    return found


def inspect_public_coupon_page() -> dict:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; MaxZoneCouponResearch/1.1)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
    })
    response = session.get(COUPON_PAGE, timeout=TIMEOUT, allow_redirects=True)
    response.raise_for_status()
    if len(response.content) > MAX_HTML_BYTES:
        raise ValueError("A página retornou HTML acima do limite de diagnóstico.")

    parser = _PageInspector()
    parser.feed(response.text)
    candidates = set()
    for raw in parser.links:
        candidates.update(_endpoint_candidates(raw, response.url))
    for inline in parser.inline_data:
        candidates.update(_endpoint_candidates(inline, response.url))

    script_urls = []
    for raw in parser.scripts:
        absolute = urljoin(response.url, raw)
        if _allowed_asset(absolute) and absolute not in script_urls:
            script_urls.append(absolute)

    inspected_scripts = []
    script_errors = []
    for url in script_urls[:MAX_SCRIPTS]:
        try:
            asset = session.get(url, timeout=TIMEOUT, allow_redirects=True, stream=True)
            asset.raise_for_status()
            chunks = []
            total = 0
            for chunk in asset.iter_content(65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_SCRIPT_BYTES:
                    break
                chunks.append(chunk)
            body = b"".join(chunks).decode("utf-8", errors="replace")
            discovered = _endpoint_candidates(body, asset.url)
            candidates.update(discovered)
            inspected_scripts.append({
                "url": url,
                "http_status": asset.status_code,
                "bytes_scanned": total,
                "candidate_count": len(discovered),
            })
        except requests.RequestException as exc:
            script_errors.append(f"{urlparse(url).path}: {type(exc).__name__}")

    html_lower = response.text.lower()
    markers = {
        "texto_cupom": html_lower.count("cupom"),
        "texto_produtos_selecionados": html_lower.count("produtos selecionados"),
        "texto_conferir": html_lower.count("conferir"),
        "texto_novos": html_lower.count("novos"),
    }
    endpoints = sorted(candidates)[:60]
    return {
        "url": response.url,
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "html_bytes": len(response.content),
        "title": " ".join(" ".join(parser.title_parts).split())[:180],
        "script_count": len(script_urls),
        "scripts_inspected": inspected_scripts,
        "script_errors": script_errors[:10],
        "candidate_endpoints": endpoints,
        "page_markers": markers,
        "message": (
            "A página pública respondeu e os arquivos JavaScript foram inspecionados. "
            "Os endpoints encontrados ainda precisam ser validados antes de importar cupons."
            if endpoints else
            "A página respondeu, mas não revelou endpoints claros no HTML ou nos scripts. "
            "A próxima etapa será capturar a chamada Fetch/XHR feita ao abrir um cupom."
        ),
    }

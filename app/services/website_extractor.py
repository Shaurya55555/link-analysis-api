"""Fetches a site safely (SSRF-checked on every redirect) and extracts clean content."""
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.utils.validators import UnsafeURLError, validate_url

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LinkAnalysisBot/1.0)", "Accept": "text/html,*/*;q=0.8"}
MAX_REDIRECTS = 5
EXTRA_PAGE_KEYWORDS = ("about", "service", "product", "solution", "offer", "what-we-do", "pricing", "feature")
NOISE_TAGS = ("script", "style", "noscript", "svg", "iframe", "form", "footer", "header", "nav", "aside", "template")


class FetchError(Exception):
    """Site is unreachable, blocked, or not HTML."""


@dataclass
class Page:
    url: str
    title: str = ""
    meta_description: str = ""
    og_site_name: str = ""
    headings: List[str] = field(default_factory=list)
    text: str = ""
    nav_links: List[tuple] = field(default_factory=list)


@dataclass
class ExtractedSite:
    url: str
    pages: List[Page]

    def to_prompt_text(self, max_chars: int) -> str:
        parts, budget = [], max_chars
        for i, p in enumerate(self.pages):
            block = [f"### PAGE {i + 1}: {p.url}"]
            if p.title:
                block.append(f"Title: {p.title}")
            if p.og_site_name:
                block.append(f"Site name: {p.og_site_name}")
            if p.meta_description:
                block.append(f"Meta description: {p.meta_description}")
            if p.headings:
                block.append("Headings: " + " | ".join(p.headings[:25]))
            share = budget if i == len(self.pages) - 1 else int(budget * 0.6)
            body = p.text[: max(share - 400, 500)]
            block.append("Content: " + body)
            chunk = "\n".join(block)
            parts.append(chunk)
            budget -= len(chunk)
            if budget <= 500:
                break
        return "\n\n".join(parts)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def safe_get(url: str, client: httpx.Client) -> httpx.Response:
    """GET with manual redirect handling so each hop is re-validated against SSRF rules."""
    settings = get_settings()
    current = validate_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        with client.stream("GET", current, headers=HEADERS, follow_redirects=False) as resp:
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise FetchError("Redirect without a Location header")
                current = validate_url(urljoin(current, location))
                continue
            ctype = resp.headers.get("content-type", "")
            if ctype and "html" not in ctype and "xml" not in ctype:
                raise FetchError(f"URL is not an HTML page (content-type: {ctype})")
            chunks, size = [], 0
            for chunk in resp.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > settings.max_html_bytes:
                    break  # truncate oversized pages instead of failing
            final = httpx.Response(resp.status_code, headers={"content-type": ctype},
                                   content=b"".join(chunks), request=resp.request)
            final.encoding = resp.encoding or "utf-8"
            return final
    raise FetchError("Too many redirects")


def parse_page(url: str, html: str) -> Page:
    soup = BeautifulSoup(html, "html.parser")
    page = Page(url=url)
    page.title = _squash(soup.title.get_text()) if soup.title else ""

    def meta(**attrs) -> str:
        tag = soup.find("meta", attrs=attrs)
        return _squash(tag.get("content", "")) if tag else ""

    page.meta_description = meta(name="description") or meta(property="og:description")
    page.og_site_name = meta(property="og:site_name")

    # Nav links are collected before noise removal strips <nav>.
    base_host = urlparse(url).netloc
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"]).split("#")[0]
        if urlparse(href).netloc != base_host or href in seen or href.rstrip("/") == url.rstrip("/"):
            continue
        seen.add(href)
        page.nav_links.append((href, _squash(a.get_text())[:60]))

    for tag in soup(NOISE_TAGS):
        tag.decompose()
    page.headings = [_squash(h.get_text()) for h in soup.find_all(["h1", "h2", "h3"]) if _squash(h.get_text())]
    main = soup.find("main") or soup.body or soup
    lines, seen_lines = [], set()
    for el in main.find_all(["p", "li", "h1", "h2", "h3", "h4", "td", "dd", "blockquote"]):
        t = _squash(el.get_text(" "))
        if len(t) < 25 or t in seen_lines:  # drop menu crumbs and duplicates (saves tokens)
            continue
        seen_lines.add(t)
        lines.append(t)
    page.text = " ".join(lines)
    return page


def _pick_extra_pages(page: Page, limit: int = 2) -> List[str]:
    picked = []
    for href, label in page.nav_links:
        hay = (href + " " + label).lower()
        if any(k in hay for k in EXTRA_PAGE_KEYWORDS):
            picked.append(href)
        if len(picked) == limit:
            break
    return picked


def extract_site(url: str, client: Optional[httpx.Client] = None) -> ExtractedSite:
    settings = get_settings()
    own_client = client is None
    client = client or httpx.Client(timeout=settings.fetch_timeout_s)
    try:
        try:
            resp = safe_get(url, client)
        except httpx.TimeoutException as exc:
            raise FetchError("The website took too long to respond") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"The website could not be reached: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise FetchError(f"The website returned HTTP {resp.status_code}")
        home = parse_page(str(resp.request.url), resp.text)
        pages = [home]
        for extra in _pick_extra_pages(home):
            try:
                r = safe_get(extra, client)
                if r.status_code < 400:
                    pages.append(parse_page(str(r.request.url), r.text))
            except (UnsafeURLError, FetchError, httpx.HTTPError):
                continue  # extra pages are best-effort
        total_text = sum(len(p.text) for p in pages)
        if total_text < 200 and not home.meta_description:
            raise FetchError("The page has too little readable content (it may be rendered by JavaScript or blocked)")
        return ExtractedSite(url=home.url, pages=pages)
    finally:
        if own_client:
            client.close()

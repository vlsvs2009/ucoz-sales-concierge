"""Site crawler: sitemap + internal links -> clean text pages -> chunks.

Tuned for uCoz sites (skips service pages, strips boilerplate shared across pages).
"""
import asyncio
import re
from collections import Counter
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse, urldefrag

import httpx
from bs4 import BeautifulSoup

MAX_PAGES = 150
CONCURRENCY = 8
TIMEOUT = 12.0
CHUNK_SIZE = 700
CHUNK_OVERLAP = 80

UA = "Mozilla/5.0 (compatible; uCozSalesConcierge/0.1; +https://www.ucoz.ru)"

SKIP_PATTERNS = re.compile(
    r"(/register|/login|/logout|/search|/rss|/sitemap|/index/8|/index/3|/index/40|/index/10|"
    r"/index/sub|/index/\d+-\d+-\d+-\d+|\?|/_|/tags/|/feed|/print|/stat/|/dir/0|/board/0)",
    re.I,
)
SKIP_EXT = re.compile(r"\.(jpg|jpeg|png|gif|webp|svg|pdf|zip|rar|doc|docx|xls|xlsx|mp3|mp4|avi|css|js|xml|ico)$", re.I)

NOISE_TAGS = ["script", "style", "noscript", "iframe", "svg", "form", "select", "button", "input"]


def _norm(url: str) -> str:
    url, _ = urldefrag(url)
    url = url.strip()
    if url.endswith("/index/") or url.endswith("/index"):
        url = url.rsplit("/index", 1)[0] + "/"
    if url.count("/") <= 2:  # bare host
        url += "/"
    return url


def _key(url: str) -> str:
    return url.lower().rstrip("/")


def _same_site(u: str, host: str) -> bool:
    h = urlparse(u).netloc.lower()
    return h == host or h == "www." + host or host == "www." + h


def _extract(html: str, url: str) -> tuple[str, list[str], list[str]]:
    """Returns (title, text lines, links)."""
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string if soup.title and soup.title.string else "").strip()
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title = og["content"].strip() or title
    desc = soup.find("meta", attrs={"name": "description"})
    desc_text = (desc.get("content") or "").strip() if desc else ""

    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        links.append(urljoin(url, href))

    for t in soup(NOISE_TAGS):
        t.decompose()
    # uCoz admin bar / counters
    for t in soup.select("#uNetBar, .u-star-rating, .uStar, #uCozCounter, .ucoz-counter, [id^='uTopBar']"):
        t.decompose()

    body = soup.body or soup
    # Insert newlines around block elements
    for tag in body.find_all(["p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "section", "article", "dt", "dd"]):
        tag.insert_before("\n")
        tag.insert_after("\n")
    for h in body.find_all(["h1", "h2", "h3", "h4"]):
        h.insert_before("\n## ")
    text = body.get_text(" ")
    lines = []
    for raw in text.split("\n"):
        line = re.sub(r"[ \t\xa0]+", " ", raw).strip()
        if len(line) < 3:
            continue
        lines.append(line)
    if desc_text:
        lines.insert(0, desc_text)
    return title, lines, links


def chunk_text(title: str, url: str, text: str) -> list[str]:
    """Greedy chunking by paragraphs, ~CHUNK_SIZE chars, with title prefix."""
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 1 > CHUNK_SIZE and buf:
            chunks.append(buf.strip())
            buf = buf[-CHUNK_OVERLAP:] + " " if CHUNK_OVERLAP else ""
        buf += p + "\n"
    if buf.strip():
        chunks.append(buf.strip())
    return [f"[{title}]\n{c}" for c in chunks if len(c) > 40]


async def crawl(start_url: str, progress: Optional[Callable[[str], None]] = None, max_pages: int = MAX_PAGES) -> list[dict]:
    if not start_url.startswith("http"):
        start_url = "https://" + start_url
    parsed = urlparse(start_url)
    host = parsed.netloc.lower()
    base = f"{parsed.scheme}://{parsed.netloc}"

    seen: set[str] = set()
    queue: list[str] = [_norm(start_url)]
    results: dict[str, dict] = {}

    async with httpx.AsyncClient(headers={"User-Agent": UA, "Accept-Language": "ru,en"}, follow_redirects=True, timeout=TIMEOUT, verify=False) as client:
        # sitemap(s)
        for sm in ("/sitemap.xml", "/sitemap-shop.xml", "/sitemap-forum.xml"):
            try:
                r = await client.get(base + sm)
                if r.status_code == 200 and "<loc>" in r.text:
                    for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text):
                        if loc.endswith(".xml"):
                            try:
                                r2 = await client.get(loc)
                                for loc2 in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r2.text):
                                    queue.append(_norm(loc2))
                            except Exception:
                                pass
                        else:
                            queue.append(_norm(loc))
            except Exception:
                pass
        if progress:
            progress(f"sitemap: {len(queue)} url")

        sem = asyncio.Semaphore(CONCURRENCY)

        async def fetch(u: str):
            async with sem:
                try:
                    r = await client.get(u)
                    ctype = r.headers.get("content-type", "")
                    if r.status_code != 200 or "html" not in ctype:
                        return u, None, []
                    return str(r.url), r.text, []
                except Exception:
                    return u, None, []

        while queue and len(results) < max_pages:
            batch = []
            while queue and len(batch) < CONCURRENCY * 2:
                u = queue.pop(0)
                if _key(u) in seen or not _same_site(u, host) or SKIP_PATTERNS.search(u) or SKIP_EXT.search(u):
                    continue
                seen.add(_key(u))
                batch.append(u)
            if not batch:
                break
            for u, html, _ in await asyncio.gather(*(fetch(b) for b in batch)):
                if not html or _key(u) in {_key(k) for k in results}:
                    continue
                seen.add(_key(u))
                title, lines, links = _extract(html, u)
                results[u] = {"url": u, "title": title or u, "lines": lines}
                for l in links:
                    ln = _norm(l)
                    if _key(ln) not in seen and _same_site(ln, host) and not SKIP_PATTERNS.search(ln) and not SKIP_EXT.search(ln):
                        queue.append(ln)
                if len(results) >= max_pages:
                    break
            if progress:
                progress(f"страниц: {len(results)}, в очереди: {len(queue)}")

    # Strip boilerplate: lines that appear on >= 40% of pages (menus, footers) — but keep them once as "site profile"
    pages = list(results.values())
    if not pages:
        return []
    counter = Counter()
    for p in pages:
        for l in set(p["lines"]):
            counter[l] += 1
    threshold = max(2, int(len(pages) * 0.4)) if len(pages) > 3 else 10**9
    boilerplate = {l for l, c in counter.items() if c >= threshold}

    out = []
    for p in pages:
        body = [l for l in p["lines"] if l not in boilerplate]
        text = "\n".join(body)
        out.append({"url": p["url"], "title": p["title"], "text": text})

    # Site profile page: boilerplate joined (contacts, menu, footer) attached to the home page
    home = out[0]
    bp_text = "\n".join(sorted(boilerplate, key=len, reverse=True)[:80])
    if bp_text:
        home["text"] = (home["text"] + "\n\n## Общая информация сайта (шапка/подвал/меню)\n" + bp_text).strip()
    return out

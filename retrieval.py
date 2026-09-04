"""BM25 retrieval over site chunks, in-memory per site, with light RU/EN stemming."""
import re
import threading
from typing import Optional

from rank_bm25 import BM25Okapi

import db

_TOKEN = re.compile(r"[a-zа-яё0-9]+", re.I)
_RU_ENDINGS = (
    "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "ешь", "ишь", "ете", "ите", "ует", "ать", "ять", "ить",
    "ть", "ая", "яя", "ое", "ее", "ые", "ие", "ой", "ей", "ый", "ий", "ым", "им", "ом", "ем", "ах", "ях", "ов", "ев",
    "ам", "ям", "ах", "ую", "юю", "ся", "сь", "ла", "ло", "ли", "ть", "а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й",
)
_EN_ENDINGS = ("ing", "ies", "es", "ed", "s")


def stem(w: str) -> str:
    if len(w) <= 3:
        return w
    if re.match(r"[а-яё]", w):
        for e in _RU_ENDINGS:
            if w.endswith(e) and len(w) - len(e) >= 3:
                return w[: -len(e)]
        return w
    for e in _EN_ENDINGS:
        if w.endswith(e) and len(w) - len(e) >= 3:
            return w[: -len(e)]
    return w


def tokenize(text: str) -> list[str]:
    return [stem(t.lower().replace("ё", "е")) for t in _TOKEN.findall(text)]


class SiteIndex:
    def __init__(self, site_id: str):
        self.site_id = site_id
        self.chunks = db.get_chunks(site_id)
        corpus = [tokenize(c["text"]) for c in self.chunks] or [["пусто"]]
        self.bm25 = BM25Okapi(corpus)
        self.page_urls = {c["url"] for c in self.chunks}

    def search(self, query: str, k: int = 8) -> list[dict]:
        if not self.chunks:
            return []
        q = tokenize(query)
        if not q:
            return []
        scores = self.bm25.get_scores(q)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in order[:k]:
            if scores[i] <= 0:
                break
            c = dict(self.chunks[i])
            c["score"] = float(scores[i])
            out.append(c)
        return out


_cache: dict[str, SiteIndex] = {}
_lock = threading.Lock()


def get_index(site_id: str) -> SiteIndex:
    with _lock:
        idx = _cache.get(site_id)
        if idx is None:
            idx = SiteIndex(site_id)
            _cache[site_id] = idx
        return idx


def invalidate(site_id: str) -> None:
    with _lock:
        _cache.pop(site_id, None)


def site_profile(site_id: str, max_chars: int = 3500) -> str:
    """Home page text (contains contacts/menu/footer boilerplate) as always-on context."""
    site = db.get_site(site_id)
    if not site:
        return ""
    pages = db.get_pages(site_id)
    if not pages:
        return ""
    home = db.get_page(site_id, pages[0]["url"])
    text = (home or {}).get("text", "")
    return text[:max_chars]

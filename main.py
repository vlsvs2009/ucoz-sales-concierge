"""AI Sales Concierge for uCoz — FastAPI backend."""
import asyncio
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
import json
import queue
import threading
from pydantic import BaseModel

import agent, crawler, db, retrieval

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "demo")
STATIC = Path(__file__).parent

app = FastAPI(title="uCoz AI Sales Concierge", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---------- tiny rate limit (per IP, per minute) ----------
_hits: dict[str, list[float]] = defaultdict(list)
RATE = int(os.environ.get("RATE_PER_MIN", "30"))


def _ratelimit(ip: str):
    now = time.time()
    _hits[ip] = [t for t in _hits[ip] if now - t < 60]
    if len(_hits[ip]) >= RATE:
        raise HTTPException(429, "Слишком много сообщений, подождите минуту")
    _hits[ip].append(now)


def _admin(request: Request):
    tok = request.headers.get("x-admin-token") or request.query_params.get("token") or request.cookies.get("admin_token")
    if tok != ADMIN_TOKEN:
        raise HTTPException(401, "admin token required")


# ---------- models ----------
class SiteIn(BaseModel):
    url: str
    name: Optional[str] = None


class ConfigIn(BaseModel):
    name: Optional[str] = None
    config: dict


class ChatIn(BaseModel):
    site_id: str
    message: str
    conversation_id: Optional[str] = None
    page_url: Optional[str] = None


# ---------- indexing ----------
async def _index_site(site_id: str):
    site = db.get_site(site_id)
    if not site:
        return
    db.update_site(site_id, status="indexing", status_detail="старт")

    def progress(msg: str):
        db.update_site(site_id, status_detail=msg)

    try:
        pages = await crawler.crawl(site["url"], progress)
        if not pages:
            db.update_site(site_id, status="error", status_detail="не удалось загрузить ни одной страницы")
            return
        chunks = [crawler.chunk_text(p["title"], p["url"], p["text"]) for p in pages]
        db.replace_pages(site_id, pages, chunks)
        retrieval.invalidate(site_id)
        # derive site name from home title if not set by user
        if site.get("name") in (None, "", site["url"]):
            parts = [x.strip() for x in re.split(r" [-|–—] ", pages[0]["title"]) if x.strip()]
            parts = [x for x in parts if x.lower() not in ("главная страница", "главная", "ucoz", "home", "main page")] or parts
            t = (max(parts, key=len) if parts else "")[:80]
            if t:
                db.update_site(site_id, name=t)
        db.update_site(site_id, status_detail=f"готово: {len(pages)} страниц, {sum(len(c) for c in chunks)} фрагментов")
    except Exception as e:  # noqa
        db.update_site(site_id, status="error", status_detail=f"ошибка: {e}")


# ---------- public API ----------
@app.get("/health")
def health():
    return {"ok": True, "model": agent.MODEL, "mock": agent.MOCK}


@app.post("/api/chat")
def api_chat(body: ChatIn, request: Request):
    _ratelimit(request.client.host if request.client else "?")
    if not body.message.strip():
        raise HTTPException(400, "empty message")
    site = db.get_site(body.site_id)
    if not site:
        raise HTTPException(404, "site not found")
    if site["status"] != "ready":
        raise HTTPException(409, "сайт ещё индексируется")
    meta = {"ip": request.client.host if request.client else None, "ua": request.headers.get("user-agent", "")[:200], "page": body.page_url}
    try:
        out = agent.chat(body.site_id, body.conversation_id, body.message.strip()[:2000], meta)
    except Exception as e:  # noqa
        raise HTTPException(500, f"agent error: {e}")
    if out.get("lead"):
        out["lead"] = {"id": out["lead"]["id"], "temperature": out["lead"]["temperature"]}
    return out


@app.post("/api/chat/stream")
def api_chat_stream(body: ChatIn, request: Request):
    """SSE: events `delta` (text chunks) then `done` (full result JSON)."""
    _ratelimit(request.client.host if request.client else "?")
    site = db.get_site(body.site_id)
    if not site or not body.message.strip():
        raise HTTPException(400, "bad request")
    if site["status"] != "ready":
        raise HTTPException(409, "сайт ещё индексируется")
    meta = {"ip": request.client.host if request.client else None, "ua": request.headers.get("user-agent", "")[:200], "page": body.page_url}
    q: queue.Queue = queue.Queue()

    def work():
        try:
            out = agent.chat(body.site_id, body.conversation_id, body.message.strip()[:2000], meta, on_text=lambda t: q.put(("delta", t)))
            if out.get("lead"):
                out["lead"] = {"id": out["lead"]["id"], "temperature": out["lead"]["temperature"]}
            q.put(("done", out))
        except Exception as e:  # noqa
            q.put(("error", str(e)))
        q.put(None)

    threading.Thread(target=work, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            ev, data = item
            yield f"event: {ev}\ndata: {json.dumps(data if ev != 'delta' else {'t': data}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/widget-config/{site_id}")
def widget_config(site_id: str):
    site = db.get_site(site_id)
    if not site:
        raise HTTPException(404)
    c = site["config"]
    return {"site_id": site_id, "name": site.get("name"), "agent_name": c["agent_name"], "welcome": c["welcome"],
            "quick_replies": c["quick_replies"], "accent": c["accent"], "ready": site["status"] == "ready"}


@app.get("/widget.js")
def widget_js():
    return FileResponse(STATIC / "widget.js", media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=300"})


# ---------- admin API ----------
@app.get("/api/sites")
def api_sites(request: Request):
    _admin(request)
    sites = db.list_sites()
    for s in sites:
        s.pop("config_json", None)
        s["stats"] = db.stats(s["id"])
    return sites


@app.post("/api/sites")
async def api_create_site(body: SiteIn, request: Request, bg: BackgroundTasks):
    _admin(request)
    url = body.url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    site = db.create_site(url, body.name)
    bg.add_task(_index_site, site["id"])
    site.pop("config_json", None)
    return site


@app.get("/api/sites/{site_id}")
def api_site(site_id: str, request: Request):
    _admin(request)
    s = db.get_site(site_id)
    if not s:
        raise HTTPException(404)
    s.pop("config_json", None)
    s["stats"] = db.stats(site_id)
    s["pages"] = db.get_pages(site_id)
    return s


@app.post("/api/sites/{site_id}/reindex")
async def api_reindex(site_id: str, request: Request, bg: BackgroundTasks):
    _admin(request)
    if not db.get_site(site_id):
        raise HTTPException(404)
    bg.add_task(_index_site, site_id)
    return {"ok": True}


@app.put("/api/sites/{site_id}/config")
def api_config(site_id: str, body: ConfigIn, request: Request):
    _admin(request)
    s = db.get_site(site_id)
    if not s:
        raise HTTPException(404)
    cfg = {**s["config"], **body.config}
    fields = {"config": cfg}
    if body.name:
        fields["name"] = body.name
    db.update_site(site_id, **fields)
    return {"ok": True, "config": cfg}


@app.delete("/api/sites/{site_id}")
def api_delete(site_id: str, request: Request):
    _admin(request)
    db.delete_site(site_id)
    retrieval.invalidate(site_id)
    return {"ok": True}


@app.get("/api/sites/{site_id}/leads")
def api_leads(site_id: str, request: Request):
    _admin(request)
    return db.list_leads(site_id)


@app.get("/api/sites/{site_id}/conversations")
def api_convs(site_id: str, request: Request):
    _admin(request)
    return db.list_conversations(site_id)


@app.get("/api/sites/{site_id}/search")
def api_search(site_id: str, q: str, request: Request):
    """Debug: what retrieval returns for a query."""
    _admin(request)
    return retrieval.get_index(site_id).search(q, k=8)


# ---------- pages ----------
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/admin")


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page(request: Request):
    tok = request.query_params.get("token") or request.cookies.get("admin_token")
    if tok != ADMIN_TOKEN:
        return HTMLResponse((STATIC / "login.html").read_text(encoding="utf-8"))
    resp = HTMLResponse((STATIC / "admin.html").read_text(encoding="utf-8"))
    resp.set_cookie("admin_token", tok, httponly=False, samesite="lax", max_age=86400 * 7)
    return resp


@app.get("/admin/site/{site_id}", response_class=HTMLResponse, include_in_schema=False)
def admin_site_page(site_id: str, request: Request):
    tok = request.query_params.get("token") or request.cookies.get("admin_token")
    if tok != ADMIN_TOKEN:
        return RedirectResponse("/admin")
    html = (STATIC / "site.html").read_text(encoding="utf-8").replace("__SITE_ID__", site_id)
    return HTMLResponse(html)


@app.get("/demo/{site_id}", response_class=HTMLResponse, include_in_schema=False)
def demo_page(site_id: str, request: Request):
    """Fallback demo page: a neutral page with the widget for this site."""
    s = db.get_site(site_id)
    if not s:
        raise HTTPException(404)
    pages = db.get_pages(site_id)[:12]
    links = "".join(f'<li><a href="{p["url"]}" target="_blank">{p["title"]}</a></li>' for p in pages)
    html = (STATIC / "demo.html").read_text(encoding="utf-8")
    html = html.replace("__SITE_ID__", site_id).replace("__SITE_NAME__", s.get("name") or s["url"]).replace("__SITE_URL__", s["url"]).replace("__LINKS__", links)
    return HTMLResponse(html)


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /admin\n")

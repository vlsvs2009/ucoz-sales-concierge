"""The sales concierge agent: retrieval + Claude with a lead-capture tool."""
import json
import os
import re
import time
from typing import Any

import db, retrieval

MODEL = os.environ.get("MODEL", "claude-sonnet-4-5")
MOCK = os.environ.get("LLM_MOCK", "0") == "1"
MAX_HISTORY = 16  # messages kept in context
TOP_K = int(os.environ.get("TOP_K", "8"))

_client = None


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


CAPTURE_LEAD_TOOL = {
    "name": "capture_lead",
    "description": (
        "Сохранить лид (заявку) посетителя, когда он оставил контакт (телефон или email) и понятна его потребность. "
        "Вызывай сразу, как только получил контакт — не жди конца разговора. Один раз на диалог, если контакт не изменился."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Имя посетителя, если назвал"},
            "phone": {"type": "string", "description": "Телефон, как написал посетитель"},
            "email": {"type": "string", "description": "Email, если дал"},
            "need": {"type": "string", "description": "Что нужно посетителю, коротко (услуга/товар + детали)"},
            "summary": {"type": "string", "description": "Резюме диалога для менеджера: 2–4 предложения, факты, срочность, что обещано"},
            "temperature": {"type": "string", "enum": ["hot", "warm", "cold"], "description": "hot — готов купить/записаться сейчас; warm — есть потребность, сроки не ясны; cold — просто узнаёт"},
        },
        "required": ["need", "summary", "temperature"],
    },
}


def build_system(site: dict, profile: str) -> str:
    cfg = site["config"]
    qual = "\n".join(f"- {q}" for q in cfg.get("qualification", []))
    extra = cfg.get("extra_knowledge") or ""
    return f"""Ты — {cfg.get('agent_name', 'консультант')} сайта «{site.get('name') or site['url']}» ({site['url']}). Твоя роль — AI Sales Concierge: помогать посетителю, отвечать на вопросы по содержимому сайта, уточнять потребность и доводить до контакта или следующего шага.

## Железные правила (нарушать нельзя)
1. Отвечай ТОЛЬКО на основе фрагментов сайта, которые тебе переданы в сообщении (блок «Фрагменты сайта»), профиля сайта и дополнительных знаний ниже. Ничего не выдумывай.
2. Цены, сроки, гарантии, скидки, наличие, адреса и часы работы называй только если они дословно есть в переданных фрагментах. Если их нет — так и скажи: «На сайте нет точной информации об этом, уточню у менеджера — оставьте телефон» и предложи контакт.
3. Если фрагменты не относятся к вопросу или помечено, что контента по вопросу нет — честно скажи, что на сайте этого нет, и предложи связаться с менеджером. Не подменяй ответ общими знаниями.
4. Не обещай ничего от имени компании сверх написанного на сайте. Не давай медицинских/юридических/финансовых советов.
5. Когда используешь информацию из фрагмента, ставь в конце предложения ссылку вида [1], [2] — номер фрагмента.

## Как вести разговор
- Тон: {cfg.get('tone', 'дружелюбный, деловой, кратко')}. Язык — язык посетителя (по умолчанию русский). Отвечай коротко: 2–5 предложений, без воды. Списки — только если перечисляешь варианты.
- Сначала отвечай на вопрос, потом задавай ОДИН уточняющий вопрос по квалификации. Не устраивай анкету.
- Квалификация (что нужно выяснить по ходу):
{qual}
- Следующий шаг: {cfg.get('cta', 'предложить оставить телефон для связи с менеджером')}. Предлагай его, когда потребность понятна или когда ответить точно нельзя.
- Как только посетитель написал телефон или email — вызови инструмент capture_lead, затем подтверди: «{cfg.get('handoff', 'Менеджер свяжется с вами.')}»
- Если посетитель уходит от темы сайта (погода, политика, другие компании) — мягко верни к теме.

## Профиль сайта (главная страница, контакты, меню)
{profile or '(нет данных)'}

## Дополнительные знания от владельца сайта
{extra or '(нет)'}
"""


def _context_block(chunks: list[dict]) -> str:
    if not chunks:
        return "Фрагменты сайта: по этому вопросу подходящего контента на сайте НЕ НАЙДЕНО. Скажи об этом честно и предложи контакт с менеджером."
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(f"[{i}] Страница: {c['title']} — {c['url']}\n{c['text']}")
    return "Фрагменты сайта (нумерация для ссылок):\n\n" + "\n\n".join(parts)


def _to_api_messages(history: list[dict], user_text: str, context: str) -> list[dict]:
    msgs = []
    for m in history[-MAX_HISTORY:]:
        if m["role"] in ("user", "assistant") and m.get("content"):
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": f"{context}\n\n---\nСообщение посетителя: {user_text}"})
    return msgs


def _mock_reply(chunks: list[dict], user_text: str) -> str:
    if re.search(r"\+?\d[\d\s\-()]{8,}", user_text):
        return "MOCK: спасибо, контакт записал [lead]"
    if not chunks:
        return "MOCK: на сайте нет информации по этому вопросу. Оставьте телефон — менеджер уточнит."
    return f"MOCK: нашёл на странице «{chunks[0]['title']}»: {chunks[0]['text'][:200]}... [1] Подскажите, когда вам удобно?"


def chat(site_id: str, conversation_id: str | None, user_text: str, visitor_meta: dict | None = None, on_text=None) -> dict:
    site = db.get_site(site_id)
    if not site:
        raise ValueError("site not found")
    conv = db.get_conversation(conversation_id) if conversation_id else None
    if not conv or conv["site_id"] != site_id:
        conv = db.create_conversation(site_id, visitor_meta)
    history = conv["messages"]

    idx = retrieval.get_index(site_id)
    # query = current + previous user message (helps with short follow-ups like "а в субботу?")
    prev_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    chunks = idx.search(user_text + " " + prev_user[:200], k=TOP_K)
    # keep only reasonably relevant ones (relative threshold)
    if chunks:
        top = chunks[0]["score"]
        chunks = [c for c in chunks if c["score"] >= max(0.15 * top, 0.5)]
    context = _context_block(chunks)
    system = build_system(site, retrieval.site_profile(site_id))
    api_messages = _to_api_messages(history, user_text, context)

    lead = None
    t0 = time.time()
    if MOCK:
        reply_text = _mock_reply(chunks, user_text)
        if on_text:
            for w in reply_text.split(" "):
                on_text(w + " "); time.sleep(0.03)
        if "[lead]" in reply_text:
            lead = db.create_lead(site_id, conv["id"], {"phone": user_text, "need": "mock", "summary": "mock lead", "temperature": "warm"})
        usage = {}
    else:
        reply_text, lead, usage = _run_claude(site, conv["id"], system, api_messages, history, on_text)

    cited = sorted({int(n) for grp in re.findall(r"\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]", reply_text) for n in re.split(r"\s*,\s*", grp)})
    sources, seen = [], set()
    for n in cited:
        if 1 <= n <= len(chunks) and chunks[n - 1]["url"] not in seen:
            seen.add(chunks[n - 1]["url"])
            sources.append({"title": chunks[n - 1]["title"], "url": chunks[n - 1]["url"]})
    clean = re.sub(r"\s*\[\d{1,2}(?:\s*,\s*\d{1,2})*\]", "", reply_text).strip()

    history.append({"role": "user", "content": user_text, "ts": time.time()})
    history.append({"role": "assistant", "content": clean, "sources": sources, "ts": time.time(),
                    "lead_id": lead["id"] if lead else None, "latency": round(time.time() - t0, 2), "usage": usage})
    db.save_messages(conv["id"], history)
    return {"conversation_id": conv["id"], "reply": clean, "sources": sources, "lead": lead,
            "latency": round(time.time() - t0, 2)}


def _serialize_blocks(content) -> list[dict]:
    out = []
    for b in content:
        if b.type == "text":
            if b.text:
                out.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out


def _run_claude(site: dict, cid: str, system: str, messages: list[dict], history: list[dict], on_text=None):
    """Tool loop: model may call capture_lead once or more; returns (text, lead, usage).
    on_text(delta) is called with streamed text deltas when provided."""
    lead = None
    usage_total: dict[str, int] = {}
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    final_text = ""
    for _ in range(3):
        kwargs = dict(model=MODEL, max_tokens=600, system=system_blocks,
                      tools=[CAPTURE_LEAD_TOOL], messages=messages)
        if on_text:
            with client().messages.stream(**kwargs) as stream:
                for delta in stream.text_stream:
                    on_text(delta)
                resp = stream.get_final_message()
        else:
            resp = client().messages.create(**kwargs)
        u = resp.usage
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            usage_total[k] = usage_total.get(k, 0) + (getattr(u, k, 0) or 0)
        text_parts = [b.text for b in resp.content if b.type == "text"]
        final_text += "".join(text_parts)
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            return final_text.strip(), lead, usage_total
        if on_text and text_parts:
            on_text("\n")
            final_text += "\n"
        messages = messages + [{"role": "assistant", "content": _serialize_blocks(resp.content)}]
        results = []
        for tu in tool_uses:
            if tu.name == "capture_lead":
                data = dict(tu.input)
                if not (data.get("phone") or data.get("email")):
                    results.append({"type": "tool_result", "tool_use_id": tu.id,
                                    "content": "Ошибка: нет телефона или email. Сначала попроси контакт у посетителя, потом вызови снова."})
                    continue
                lead = db.create_lead(site["id"], cid, data)
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": f"Лид сохранён (id {lead['id']}). Коротко подтверди посетителю и назови следующий шаг."})
            else:
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "unknown tool"})
        messages = messages + [{"role": "user", "content": results}]
    return (final_text.strip() or "Спасибо! Передал ваш запрос менеджеру."), lead, usage_total

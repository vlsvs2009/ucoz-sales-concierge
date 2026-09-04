/* uCoz AI Sales Concierge — embeddable widget. No dependencies.
   Usage: <script src="https://HOST/widget.js" data-site="SITE_ID" defer></script> */
(function () {
  if (window.__ucozConcierge) return;
  window.__ucozConcierge = true;

  var script = document.currentScript || (function () { var s = document.getElementsByTagName('script'); return s[s.length - 1]; })();
  var SITE = script.getAttribute('data-site');
  var HOST = script.getAttribute('data-host') || script.src.replace(/\/widget\.js.*$/, '');
  if (!SITE) { console.warn('[concierge] data-site is missing'); return; }

  var cfg = { agent_name: 'Консультант', welcome: 'Здравствуйте! Чем помочь?', quick_replies: [], accent: '#2563eb', name: '' };
  var convId = null;
  try { convId = localStorage.getItem('ucz_conv_' + SITE); } catch (e) {}
  var opened = false, busy = false, firstOpen = true;

  var css = "\
.ucz-btn{position:fixed;right:20px;bottom:20px;z-index:2147483000;width:60px;height:60px;border-radius:30px;border:0;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,.22);display:flex;align-items:center;justify-content:center;transition:transform .15s}\
.ucz-btn:hover{transform:scale(1.06)}\
.ucz-btn svg{width:28px;height:28px;fill:#fff}\
.ucz-badge{position:absolute;top:-4px;right:-4px;background:#ef4444;color:#fff;font:600 11px/18px system-ui;border-radius:9px;padding:0 5px;min-width:18px;text-align:center}\
.ucz-panel{position:fixed;right:20px;bottom:92px;z-index:2147483000;width:380px;max-width:calc(100vw - 24px);height:560px;max-height:calc(100vh - 110px);background:#fff;border-radius:16px;box-shadow:0 16px 48px rgba(0,0,0,.25);display:none;flex-direction:column;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;font-size:14px;color:#111}\
.ucz-panel.open{display:flex;animation:ucz-in .18s ease-out}\
@keyframes ucz-in{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}\
.ucz-head{padding:14px 16px;color:#fff;display:flex;align-items:center;gap:10px}\
.ucz-ava{width:36px;height:36px;border-radius:18px;background:rgba(255,255,255,.25);display:flex;align-items:center;justify-content:center;font-weight:700}\
.ucz-head b{display:block;font-size:15px}.ucz-head small{opacity:.85;font-size:12px}\
.ucz-close{margin-left:auto;background:none;border:0;color:#fff;font-size:22px;cursor:pointer;line-height:1}\
.ucz-msgs{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px;background:#f6f7f9}\
.ucz-m{max-width:85%;padding:10px 13px;border-radius:14px;line-height:1.45;white-space:pre-wrap;word-wrap:break-word}\
.ucz-m.a{background:#fff;border:1px solid #e5e7eb;align-self:flex-start;border-bottom-left-radius:4px}\
.ucz-m.u{color:#fff;align-self:flex-end;border-bottom-right-radius:4px}\
.ucz-src{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}\
.ucz-src a{font-size:11px;color:#374151;background:#f3f4f6;border:1px solid #e5e7eb;border-radius:8px;padding:2px 8px;text-decoration:none;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\
.ucz-src a:hover{background:#e5e7eb}\
.ucz-lead{align-self:center;font-size:12px;color:#065f46;background:#d1fae5;border-radius:10px;padding:5px 12px}\
.ucz-quick{display:flex;flex-wrap:wrap;gap:6px;padding:0 14px 8px;background:#f6f7f9}\
.ucz-quick button{border:1px solid;background:#fff;border-radius:16px;padding:6px 12px;font-size:13px;cursor:pointer}\
.ucz-typing{align-self:flex-start;background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:10px 14px;display:flex;gap:4px}\
.ucz-typing i{width:7px;height:7px;border-radius:4px;background:#9ca3af;animation:ucz-b 1s infinite}.ucz-typing i:nth-child(2){animation-delay:.15s}.ucz-typing i:nth-child(3){animation-delay:.3s}\
@keyframes ucz-b{0%,80%,100%{opacity:.3}40%{opacity:1}}\
.ucz-form{display:flex;gap:8px;padding:10px;border-top:1px solid #e5e7eb;background:#fff}\
.ucz-form textarea{flex:1;resize:none;border:1px solid #d1d5db;border-radius:10px;padding:9px 12px;font:inherit;height:40px;max-height:100px;outline:none}\
.ucz-form textarea:focus{border-color:#9ca3af}\
.ucz-form button{border:0;color:#fff;border-radius:10px;width:44px;cursor:pointer;display:flex;align-items:center;justify-content:center}\
.ucz-form button:disabled{opacity:.5;cursor:default}\
.ucz-foot{font-size:10px;color:#9ca3af;text-align:center;padding:0 0 6px;background:#fff}\
@media (max-width:480px){.ucz-panel{right:8px;bottom:84px;height:calc(100vh - 100px)}}";

  var style = document.createElement('style'); style.textContent = css; document.head.appendChild(style);

  var btn = document.createElement('button'); btn.className = 'ucz-btn'; btn.setAttribute('aria-label', 'Открыть чат');
  btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M20 2H4a2 2 0 0 0-2 2v18l4-4h14a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2zm-2 12H6v-2h12v2zm0-3H6V9h12v2zm0-3H6V6h12v2z"/></svg><span class="ucz-badge">1</span>';
  var panel = document.createElement('div'); panel.className = 'ucz-panel';
  panel.innerHTML = '<div class="ucz-head"><div class="ucz-ava">AI</div><div><b class="ucz-title">Консультант</b><small>Отвечает по информации с сайта</small></div><button class="ucz-close" aria-label="Закрыть">×</button></div>' +
    '<div class="ucz-msgs"></div><div class="ucz-quick"></div>' +
    '<form class="ucz-form"><textarea placeholder="Напишите вопрос…" rows="1"></textarea><button type="submit" aria-label="Отправить"><svg width="20" height="20" viewBox="0 0 24 24" fill="#fff"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg></button></form>' +
    '<div class="ucz-foot">AI-консультант · uCoz</div>';
  document.body.appendChild(btn); document.body.appendChild(panel);

  var msgs = panel.querySelector('.ucz-msgs'), quick = panel.querySelector('.ucz-quick'), form = panel.querySelector('.ucz-form'),
      ta = form.querySelector('textarea'), send = form.querySelector('button'), title = panel.querySelector('.ucz-title');

  function paint() {
    btn.style.background = cfg.accent; panel.querySelector('.ucz-head').style.background = cfg.accent;
    send.style.background = cfg.accent; title.textContent = cfg.agent_name + (cfg.name ? ' · ' + cfg.name : '');
    quick.innerHTML = '';
    (cfg.quick_replies || []).forEach(function (q) {
      var b = document.createElement('button'); b.textContent = q; b.style.borderColor = cfg.accent; b.style.color = cfg.accent;
      b.onclick = function () { ask(q); }; quick.appendChild(b);
    });
  }

  function add(role, text, sources) {
    var d = document.createElement('div'); d.className = 'ucz-m ' + (role === 'user' ? 'u' : 'a'); d.textContent = text;
    if (role === 'user') d.style.background = cfg.accent;
    if (sources && sources.length) {
      var s = document.createElement('div'); s.className = 'ucz-src';
      sources.forEach(function (src) { var a = document.createElement('a'); a.href = src.url; a.target = '_blank'; a.textContent = '↗ ' + (src.title || src.url); s.appendChild(a); });
      d.appendChild(s);
    }
    msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight; return d;
  }
  function leadNote() { var d = document.createElement('div'); d.className = 'ucz-lead'; d.textContent = '✓ Заявка передана менеджеру'; msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight; }
  function typing(on) {
    var t = msgs.querySelector('.ucz-typing');
    if (on && !t) { t = document.createElement('div'); t.className = 'ucz-typing'; t.innerHTML = '<i></i><i></i><i></i>'; msgs.appendChild(t); msgs.scrollTop = msgs.scrollHeight; }
    if (!on && t) t.remove();
  }

  function ask(text) {
    if (busy || !text) return;
    busy = true; send.disabled = true; quick.style.display = 'none';
    add('user', text); ta.value = ''; typing(true);
    var body = JSON.stringify({ site_id: SITE, message: text, conversation_id: convId, page_url: location.href });
    var bubble = null, acc = '';
    function finish(j) {
      typing(false); convId = j.conversation_id;
      try { localStorage.setItem('ucz_conv_' + SITE, convId); } catch (e) {}
      if (bubble) bubble.remove();
      add('assistant', j.reply, j.sources);
      if (j.lead) leadNote();
    }
    function fail(e) { typing(false); if (bubble) bubble.remove(); add('assistant', 'Не удалось получить ответ (' + e.message + '). Попробуйте ещё раз.'); }
    function done() { busy = false; send.disabled = false; ta.focus(); }
    function fallback() {
      return fetch(HOST + '/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || r.status); return j; }); })
        .then(finish);
    }
    var canStream = window.ReadableStream && window.TextDecoder;
    (canStream ? fetch(HOST + '/api/chat/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (j) { throw new Error(j.detail || r.status); });
        var reader = r.body.getReader(), dec = new TextDecoder(), buf = '', result = null;
        function pump() {
          return reader.read().then(function (x) {
            if (x.done) { if (!result) throw new Error('empty'); return result; }
            buf += dec.decode(x.value, { stream: true });
            var parts = buf.split('\n\n'); buf = parts.pop();
            parts.forEach(function (blk) {
              var ev = (blk.match(/^event: (\w+)/m) || [])[1], data = (blk.match(/^data: (.*)$/m) || [])[1];
              if (!ev || !data) return;
              var j = JSON.parse(data);
              if (ev === 'delta') {
                typing(false); acc += j.t;
                if (!bubble) bubble = add('assistant', '');
                bubble.textContent = acc.replace(/\s*\[\d{1,2}\]/g, ''); msgs.scrollTop = msgs.scrollHeight;
              } else if (ev === 'done') result = j;
              else if (ev === 'error') throw new Error(j);
            });
            return pump();
          });
        }
        return pump();
      }).then(finish) : fallback())
      .catch(function (e) { if (canStream && !bubble && /empty|Failed to fetch/.test(e.message)) return fallback().catch(fail); fail(e); })
      .then(done);
  }

  form.addEventListener('submit', function (e) { e.preventDefault(); ask(ta.value.trim()); });
  ta.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(ta.value.trim()); } });
  function toggle(open) {
    opened = open; panel.classList.toggle('open', open); btn.querySelector('.ucz-badge').style.display = 'none';
    if (open && firstOpen) { firstOpen = false; add('assistant', cfg.welcome); ta.focus(); }
  }
  btn.onclick = function () { toggle(!opened); };
  panel.querySelector('.ucz-close').onclick = function () { toggle(false); };

  fetch(HOST + '/api/widget-config/' + SITE).then(function (r) { return r.json(); }).then(function (j) {
    for (var k in j) cfg[k] = j[k]; paint();
    // Auto-open on demo pages after a short delay
    if (/[?&]concierge=open/.test(location.search)) setTimeout(function () { toggle(true); }, 600);
  }).catch(function () { paint(); });
})();

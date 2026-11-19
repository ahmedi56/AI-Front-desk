// Customer chat widget. Plain JavaScript, no outside files (our security rules forbid them).
(function () {
  var log = document.getElementById('log'), form = document.getElementById('f'), input = document.getElementById('t');
  var send = document.getElementById('send'), chips = document.getElementById('chips'), closeBtn = document.getElementById('close');
  var slug = document.body.getAttribute('data-slug'), sid = null, busy = false, typing = null;
  var inFrame = false;
  try { inFrame = window.self !== window.top; } catch (e) { inFrame = true; }
  if (inFrame) document.body.classList.add('in-frame');
  try { sid = sessionStorage.getItem('fd_sid_' + slug); } catch (e) {}

  function add(cls, text) {
    var d = document.createElement('div'); d.className = 'msg pop ' + cls; d.textContent = text;
    log.appendChild(d); log.scrollTop = log.scrollHeight;
  }
  function showTyping(on) {
    if (on && !typing) {
      typing = document.createElement('div'); typing.className = 'typing'; typing.setAttribute('aria-label', 'The assistant is typing');
      typing.innerHTML = '<span></span><span></span><span></span>';
      log.appendChild(typing); log.scrollTop = log.scrollHeight;
    } else if (!on && typing) { typing.remove(); typing = null; }
  }
  function ask(text) {
    text = (text || '').trim(); if (!text || busy) return;
    busy = true; send.disabled = true; if (chips) chips.hidden = true;
    add('customer', text); input.value = ''; showTyping(true);
    fetch('/api/chat/' + encodeURIComponent(slug) + '/message', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: text, session_id: sid})
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.session_id) { sid = j.session_id; try { sessionStorage.setItem('fd_sid_' + slug, sid); } catch (e) {} }
      showTyping(false); add('agent', j.reply || j.error || 'Sorry, something went wrong. Please call us.');
    }).catch(function () {
      showTyping(false); add('agent', 'Sorry, something went wrong. Please call us.');
    }).then(function () { busy = false; send.disabled = false; input.focus(); });
  }
  form.addEventListener('submit', function (e) { e.preventDefault(); ask(input.value); });
  if (chips) chips.addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (b) ask(b.getAttribute('data-text'));
  });
  if (closeBtn) closeBtn.addEventListener('click', function () {
    try { window.parent.postMessage({fd: 'close'}, '*'); } catch (e) {}
  });
  add('agent', document.body.getAttribute('data-hello'));
})();

/* AI Front Desk - chat bubble for a client's website.
   The client pastes ONE line before </body>:
     <script src="https://YOUR-SERVER/static/embed.js" data-business="their-short-name" async></script>
   The chat itself runs in a sealed frame on our server, so the client's website never sees the conversation code. */
(function () {
  var me = document.currentScript;
  if (!me || window.__fdChat) return; window.__fdChat = true;
  var slug = me.getAttribute('data-business'); if (!slug) return;
  var origin = new URL(me.src, location.href).origin;
  var color = me.getAttribute('data-color') || '#14233b';
  var label = me.getAttribute('data-label') || 'Chat with us';
  var demo = me.hasAttribute('data-demo');  // our own product page only: the chat then says it is a demo

  var link = document.createElement('link'); link.rel = 'stylesheet'; link.href = origin + '/static/embed.css';
  document.head.appendChild(link);

  var frame = document.createElement('iframe');
  frame.id = 'fd-frame'; frame.title = 'Chat'; frame.setAttribute('loading', 'lazy');
  var btn = document.createElement('button');
  btn.id = 'fd-btn'; btn.type = 'button'; btn.setAttribute('aria-expanded', 'false');
  btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>';
  btn.appendChild(document.createTextNode(label));
  btn.style.background = color;

  function setOpen(open) {
    if (open && !frame.src) frame.src = origin + '/widget/' + encodeURIComponent(slug) + (demo ? '?demo=1' : '');
    frame.classList.toggle('open', open); btn.setAttribute('aria-expanded', String(open));
  }
  btn.addEventListener('click', function () { setOpen(!frame.classList.contains('open')); });
  // Any button or link on the page marked with data-fd-open opens the chat too (for example "Try the live demo").
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('[data-fd-open]') : null;
    if (el) { e.preventDefault(); setOpen(true); }
  });
  window.addEventListener('message', function (e) {
    if (e.origin === origin && e.data && e.data.fd === 'close') { setOpen(false); btn.focus(); }
  });
  document.body.appendChild(frame); document.body.appendChild(btn);
})();

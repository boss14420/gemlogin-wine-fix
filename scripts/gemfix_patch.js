// Installed at the app's main.js entry (before any startup runs).
//
// WHY: on an UNPRIVILEGED Linux/Wine account, binding a port < 1024 is
// EACCES-denied. GemLogin's internal UI/API server binds 1010, so it never
// starts -> ERR_CONNECTION_REFUSED -> gray window. This hook:
//   1) redirects that server's bind 1010 -> 49009,
//   2) rewrites every http://localhost:1010 reference to 49009 so the whole
//      app (page, images, API, Node-side http) works on the working port.
(function () {
  if (process.env.GEMFIX) return 'already';
  process.env.GEMFIX = '1';
  const log = (...a) => { try { console.log('[gemfix]', ...a); } catch (e) {} };

  const PRIV = 'http://localhost:1010';
  const NEW = 'http://localhost:49009';
  const wp = (p) => (p < 1024 ? 48000 + (p - 1) : p);
  const isPriv = (p) => typeof p === 'number' && p > 0 && p < 1024;
  const rw = (u) => (typeof u === 'string' && u.indexOf(PRIV) !== -1) ? u.split(PRIV).join(NEW) : u;

  // 1) redirect server binds on privileged ports
  try {
    const net = require('net');
    const orig = net.Server.prototype.listen;
    net.Server.prototype.listen = function () {
      const a = Array.prototype.slice.call(arguments);
      let port = null;
      if (typeof a[0] === 'number') port = a[0];
      else if (a[0] && typeof a[0] === 'object' && typeof a[0].port === 'number') port = a[0].port;
      if (isPriv(port)) {
        log('redirect bind', port, '->', wp(port));
        if (typeof a[0] === 'number') a[0] = wp(port);
        else a[0].port = wp(port);
      }
      const r = orig.apply(this, a);
      this.once('error', (e) => log('LISTEN ERROR', e.code, e.message));
      this.once('listening', () => { try { log('LISTEN OK', JSON.stringify(this.address())); } catch (e) {} });
      return r;
    };
    log('net bind-redirect installed');
  } catch (e) { log('net hook err', e.message); }

  // 2) main-process (Node) outbound HTTP rewrite
  try {
    const http = require('http');
    const https = require('https');
    const fixReq = (mod) => {
      const wrap = (orig) => function (url, opts, cb) {
        try {
          if (typeof url === 'string') url = rw(url);
          else if (url && typeof url === 'object') {
            // ONLY rewrite explicit localhost ports 1010/1011. Never add a
            // port for implicit 443/80 — remote vendor calls (app.gemlogin.vn,
            // hcm.s3storage.vn, …) must keep their native port or every
            // license check / download breaks (ECONNREFUSED :48442).
            const host = String(url.hostname || url.host || '');
            if (/^(localhost|127\.0\.0\.1|\[?::1\]?)$/i.test(host) &&
                (url.port === 1010 || url.port === '1010' || url.port === 1011 || url.port === '1011')) {
              url.port = String(wp(parseInt(url.port, 10)));
            }
          }
        } catch (e) {}
        if (typeof opts === 'function') { cb = opts; opts = null; }
        return orig.call(this, url, opts, cb);
      };
      mod.request = wrap(mod.request);
      if (mod.get) mod.get = wrap(mod.get);
    };
    fixReq(http);
    fixReq(https);
    if (typeof globalThis.fetch === 'function') {
      const of = globalThis.fetch;
      globalThis.fetch = function (input, init) {
        try {
          if (typeof input === 'string') input = rw(input);
          else if (input && typeof input === 'object' && 'url' in input && typeof input.url === 'string')
            input = Object.assign({}, input, { url: rw(input.url) });
        } catch (e) {}
        return of.call(this, input, init);
      };
    }
    log('main http/fetch rewrite installed');
  } catch (e) { log('main http hook err', e.message); }

  // 3) renderer-side fix: fetch / XHR / <img> / background rewrite
  const RENDER = `
    (function () {
      if (window.__gemfixR) return;
      window.__gemfixR = true;
      const PRIV = ${JSON.stringify(PRIV)}, NEW = ${JSON.stringify(NEW)};
      const rw = (u) => (typeof u === 'string' && u.indexOf(PRIV) !== -1) ? u.split(PRIV).join(NEW) : u;
      try {
        const of = window.fetch;
        window.fetch = function (input, init) {
          try {
            if (typeof input === 'string') input = rw(input);
            else if (typeof Request !== 'undefined' && input instanceof Request) input = new Request(rw(input.url), init);
            else if (input && typeof input === 'object' && typeof input.url === 'string' && typeof Request !== 'undefined') input = new Request(rw(input.url), init);
          } catch (e) {}
          return of.call(this, input, init);
        };
      } catch (e) {}
      try {
        const oo = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function () {
          const args = Array.prototype.slice.call(arguments);
          if (args.length > 1) args[1] = rw(args[1]);
          return oo.apply(this, args);
        };
      } catch (e) {}
      const fix = (root) => {
        (root.querySelectorAll('img, source, [style]') || []).forEach((el) => {
          const s = el.getAttribute && el.getAttribute('src');
          if (s && s.indexOf(PRIV) !== -1) el.setAttribute('src', rw(s));
          const ss = el.getAttribute && el.getAttribute('srcset');
          if (ss && ss.indexOf(PRIV) !== -1) el.setAttribute('srcset', rw(ss));
          if (el.style && el.style.backgroundImage && el.style.backgroundImage.indexOf(PRIV) !== -1)
            el.style.backgroundImage = rw(el.style.backgroundImage);
        });
      };
      fix(document);
      try {
        const mo = new MutationObserver(() => fix(document));
        mo.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['src', 'srcset', 'style'] });
      } catch (e) {}
      window.__gemfixDone = true;
    })();
  `;
  const installRendererFix = (wc) => {
    if (!wc || wc.__gemfixR) return;
    wc.__gemfixR = true;
    try {
      wc.executeJavaScript(RENDER, (err) => { if (err) log('renderer inject err', err && err.message); });
      log('renderer fix installed');
    } catch (e) { log('renderer inject err', e.message); }
  };

  // 4) window plumbing: loadURL rewrite + renderer fix
  try {
    const electron = require('electron');
    const BW = electron.BrowserWindow;
    if (BW && BW.prototype && BW.prototype.loadURL) {
      const o = BW.prototype.loadURL;
      BW.prototype.loadURL = function (u, ...rest) { return o.call(this, rw(u), ...rest); };
    }
    if (BW && BW.prototype && BW.prototype.loadFile) {
      const o = BW.prototype.loadFile;
      BW.prototype.loadFile = function (u, ...rest) { return o.call(this, rw(u), ...rest); };
    }
    const app = electron.app;
    if (app && app.on) {
      app.on('browser-window-created', (e, w) => {
        try { installRendererFix(w.webContents); } catch (e2) {}
      });
    }
    // also catch windows created before the hook fully settles
    const t = setInterval(() => {
      try {
        const ws = electron.BrowserWindow.getAllWindows();
        ws.forEach((w) => {
          if (w.webContents) installRendererFix(w.webContents);
        });
      } catch (e) {}
    }, 1500);
    app && app.on('quit', () => clearInterval(t));
    log('window plumbing installed');
  } catch (e) { log('plumbing err', e.message); }

  // 5) global exception logging
  process.on('uncaughtException', (e) => log('UNCAUGHT', e.message));
  process.on('unhandledRejection', (e) => log('UNHANDLED-REJECTION', String(e && e.message || e).slice(0, 200)));

  return 'gemfix redirect ready';
})()

"""Rende l'app installabile come PWA ("Aggiungi a Home") su mobile.

Due vincoli di Streamlit Cloud da aggirare:

1. Non abbiamo accesso all'`<head>` del documento servito da Streamlit. I
   componenti pero' girano in un iframe *same-origin*, quindi da li' possiamo
   iniettare nel `<head>` della pagina padre il link al manifest e i meta Apple.

2. Lo static serving (`enableStaticServing`) NON funziona su Streamlit Cloud:
   /app/static/<file> ricade sulla SPA e restituisce l'index.html, non il file.
   Verificato su browser reale. Per questo manifest e icone NON sono serviti da
   Streamlit ma da **jsDelivr**, il CDN che espone i file del repo GitHub
   pubblico con content-type corretto e header CORS (necessari per un manifest
   cross-origin). Usiamo un tag git immutabile (`pwa-v1`) cosi' la cache di
   jsDelivr non serve mai una versione vecchia; per aggiornare gli asset si
   crea un nuovo tag (pwa-v2, ...) e si aggiorna _ASSET_TAG qui sotto.

Inoltre, su Streamlit Cloud la pagina e' annidata: il documento TOP e' un
wrapper di Streamlit Cloud (che possiede il proprio <link rel=manifest> ->
/-/build/manifest.json, nome "Streamlit"); l'app vera gira in un iframe
"streamlitApp"; il nostro components.html crea un terzo iframe. Chrome legge il
manifest del documento TOP, e onora SOLO il primo <link rel=manifest>. Percio'
NON aggiungiamo un nuovo link (verrebbe ignorato): risaliamo a window.top e
SOVRASCRIVIAMO l'href del manifest gia' presente (+ apple-touch-icon, theme).
Tutti i frame sono same-origin, quindi l'accesso a window.top.document e'
consentito (verificato su browser reale).

Il problema: l'override arriva solo dopo l'avvio dell'app (~6s, e mai se l'app
e' ibernata), e chi installa prima si ritrova l'icona Streamlit. Per questo
c'e' un banner "Installa AV Assistant" che compare SOLO dopo l'override, cioe'
quando installare da' di sicuro l'icona AV:
- Chrome/Android: bottone che apre il prompt nativo (evento
  `beforeinstallprompt`, catturato sul documento top). Se il prompt non arriva
  entro qualche secondo, il banner mostra le istruzioni manuali (menu ⋮).
- iOS: Safari non ha prompt, il banner mostra "Condividi -> Aggiungi alla
  schermata Home".
Non compare se l'app e' gia' aperta dalla Home (display-mode standalone) o se
l'utente l'ha chiuso negli ultimi 14 giorni (localStorage).

Come in lib/voice_input.py, il codice gira nel realm del documento top (via
<script> iniettato), non nell'iframe del componente: l'iframe puo' essere
rimontato ad ogni rerun, mentre il listener di `beforeinstallprompt` e il
banner devono sopravvivere.

Cosa NON si ottiene (limite piattaforma, non del codice): l'offline, che
richiederebbe un service worker con scope "/" non ospitabile su Streamlit
Cloud. L'app richiede comunque la rete.

La pagina di lancio statica https://av-assistant.github.io/ (repo
av-assistant/av-assistant.github.io) e' il link d'ingresso da condividere:
ha icone AV nell'HTML e rimanda qui, dove il banner guida l'installazione.
"""
import json

import streamlit.components.v1 as components

_REPO = "francesco-brisacane/av-assistant"
_ASSET_TAG = "pwa-v1"  # tag git immutabile che contiene static/manifest.json + icone
_CDN = f"https://cdn.jsdelivr.net/gh/{_REPO}@{_ASSET_TAG}/static"

_MANIFEST_URL = f"{_CDN}/manifest.json"
_APPLE_ICON_URL = f"{_CDN}/apple-touch-icon.png"
_THEME_COLOR = "#000000"
_APP_TITLE = "AV Assistant"

_TEMPLATE = """
<script>
(function () {
  var CFG = __CFG__;
  var win;
  try { win = window.top; void win.document; } catch (e) {
    try { win = window.parent; void win.document; } catch (e2) { return; }
  }
  if (!win.document || !win.document.head) return;

  // Aggiorna la config (lingua/testi) anche se lo script e' gia' iniettato.
  win.__avPwaCfg = win.JSON.parse(JSON.stringify(CFG));
  if (!win.__avPwaLoaded) {
    win.__avPwaLoaded = true;
    // Lo script iniettato gira nel realm del top (vedi docstring del modulo).
    var s = win.document.createElement("script");
    s.textContent = "(" + main.toString() + ")(window);";
    win.document.head.appendChild(s);
  }
  if (win.__avPwaRefresh) win.__avPwaRefresh();

  function main(win) {
    var doc = win.document;
    var DISMISS_KEY = "av_pwa_banner_dismissed_at";
    var DISMISS_MS = 14 * 24 * 3600 * 1000;
    var FALLBACK_MS = 4000;  // attesa del prompt nativo prima delle istruzioni manuali

    var deferredPrompt = null;
    var installed = false;
    var fallbackReady = false;
    var fallbackTimer = null;

    var ua = win.navigator.userAgent;
    var isIOS = /iPhone|iPad|iPod/.test(ua) ||
      (win.navigator.platform === "MacIntel" && win.navigator.maxTouchPoints > 1);
    var isAndroid = /Android/.test(ua);

    // Registrato PRIMA di cambiare il manifest: Chrome rivaluta l'installabilita'
    // dopo il cambio e l'evento deve trovarci gia' in ascolto.
    win.addEventListener("beforeinstallprompt", function (e) {
      e.preventDefault();  // niente mini-infobar di Chrome, usiamo il nostro banner
      deferredPrompt = e;
      render();
    });
    win.addEventListener("appinstalled", function () {
      installed = true;
      render();
    });

    function cfg() { return win.__avPwaCfg || {}; }

    function applyManifest() {
      var c = cfg();
      // Chrome onora solo il PRIMO <link rel=manifest>: riusiamo quello
      // esistente (di Streamlit) sovrascrivendone l'href, non aggiungendone uno.
      var link = doc.querySelector('link[rel="manifest"]');
      if (!link) {
        link = doc.createElement("link");
        link.setAttribute("rel", "manifest");
        doc.head.appendChild(link);
      }
      link.setAttribute("crossorigin", "anonymous");  // fetch CORS (jsDelivr)
      if (link.getAttribute("href") !== c.manifest_url) {
        link.setAttribute("href", c.manifest_url);
      }

      // apple-touch-icon: sovrascrivi tutti quelli esistenti, o creane uno.
      var icons = doc.querySelectorAll('link[rel~="apple-touch-icon"]');
      if (icons.length) {
        icons.forEach(function (n) { n.setAttribute("href", c.apple_icon_url); });
      } else {
        var a = doc.createElement("link");
        a.setAttribute("rel", "apple-touch-icon");
        a.setAttribute("href", c.apple_icon_url);
        doc.head.appendChild(a);
      }

      setMeta("theme-color", c.theme_color);
      setMeta("mobile-web-app-capable", "yes");
      setMeta("apple-mobile-web-app-capable", "yes");
      setMeta("apple-mobile-web-app-status-bar-style", "black");
      setMeta("apple-mobile-web-app-title", c.app_title);

      // Da qui in poi installare da' l'icona AV: parte l'attesa del prompt.
      if (!fallbackTimer) {
        fallbackTimer = win.setTimeout(function () {
          fallbackReady = true;
          render();
        }, FALLBACK_MS);
      }
    }

    function setMeta(name, content) {
      var m = doc.querySelector('meta[name="' + name + '"]');
      if (!m) {
        m = doc.createElement("meta");
        m.setAttribute("name", name);
        doc.head.appendChild(m);
      }
      m.setAttribute("content", content);
    }

    function isStandalone() {
      return ["standalone", "fullscreen", "minimal-ui"].some(function (m) {
        return win.matchMedia("(display-mode: " + m + ")").matches;
      }) || win.navigator.standalone === true;
    }

    function isDismissed() {
      try {
        var t = Number(win.localStorage.getItem(DISMISS_KEY));
        return t > 0 && Date.now() - t < DISMISS_MS;
      } catch (e) { return false; }
    }

    function mode() {
      if (installed || isStandalone() || isDismissed()) return null;
      if (deferredPrompt) return "prompt";
      if (!fallbackReady) return null;
      if (isIOS) return "ios";
      if (isAndroid) return "android";
      return null;  // desktop senza prompt nativo: niente banner
    }

    function ensureStyle() {
      if (doc.getElementById("av-pwa-style")) return;
      var st = doc.createElement("style");
      st.id = "av-pwa-style";
      st.textContent =
        "#av-pwa-banner{position:fixed;z-index:2147483000;left:12px;right:12px;" +
        "top:calc(env(safe-area-inset-top, 0px) + 60px);max-width:480px;margin:0 auto;" +
        "display:flex;align-items:center;gap:12px;padding:12px 12px 12px 14px;" +
        "background:#111;color:#fff;border:1px solid #333;border-radius:14px;" +
        "box-shadow:0 8px 24px rgba(0,0,0,.5);" +
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}" +
        "#av-pwa-banner img{width:40px;height:40px;border-radius:10px;flex-shrink:0}" +
        "#av-pwa-banner .av-pwa-text{flex:1;min-width:0;font-size:13px;line-height:1.35;color:#ccc}" +
        "#av-pwa-banner .av-pwa-text strong{display:block;font-size:15px;color:#fff;margin-bottom:2px}" +
        "#av-pwa-banner .av-pwa-install{flex-shrink:0;padding:8px 14px;border:none;border-radius:10px;" +
        "background:#fff;color:#000;font-weight:600;font-size:14px;cursor:pointer;font-family:inherit}" +
        "#av-pwa-banner .av-pwa-close{flex-shrink:0;width:28px;height:28px;padding:0;border:none;" +
        "background:transparent;color:#888;font-size:20px;line-height:28px;cursor:pointer}" +
        "#av-pwa-banner .av-pwa-close:hover{color:#fff}";
      doc.head.appendChild(st);
    }

    function render() {
      var m = mode();
      var el = doc.getElementById("av-pwa-banner");
      if (!m) {
        if (el) el.remove();
        return;
      }
      var c = cfg();
      ensureStyle();
      if (!el) {
        el = doc.createElement("div");
        el.id = "av-pwa-banner";
        el.setAttribute("role", "dialog");
        doc.body.appendChild(el);
      }
      el.innerHTML = "";

      var img = doc.createElement("img");
      img.src = c.apple_icon_url;
      img.alt = "";
      el.appendChild(img);

      var text = doc.createElement("div");
      text.className = "av-pwa-text";
      var title = doc.createElement("strong");
      title.textContent = c.title;
      text.appendChild(title);
      text.appendChild(doc.createTextNode(
        m === "prompt" ? c.text : m === "ios" ? c.ios : c.android));
      el.appendChild(text);

      if (m === "prompt") {
        var btn = doc.createElement("button");
        btn.type = "button";
        btn.className = "av-pwa-install";
        btn.textContent = c.button;
        btn.addEventListener("click", install);
        el.appendChild(btn);
      }

      var close = doc.createElement("button");
      close.type = "button";
      close.className = "av-pwa-close";
      close.setAttribute("aria-label", c.close);
      close.textContent = "\\u00d7";
      close.addEventListener("click", function () {
        try { win.localStorage.setItem(DISMISS_KEY, String(Date.now())); } catch (e) {}
        render();
      });
      el.appendChild(close);
    }

    function install() {
      if (!deferredPrompt) return;
      var p = deferredPrompt;
      deferredPrompt = null;  // il prompt nativo si puo' usare una volta sola
      p.prompt();
      p.userChoice.then(function (choice) {
        if (choice && choice.outcome === "accepted") installed = true;
        render();
      }, render);
    }

    win.__avPwaRefresh = function () {
      applyManifest();
      render();
    };
  }
})();
</script>
"""


def inject_pwa(ui_text: dict) -> None:
    """Applica manifest/icone AV al documento top e gestisce il banner Installa.

    Idempotente: la prima chiamata inietta lo script nel documento top, le
    successive riapplicano il manifest e aggiornano i testi (es. al cambio
    lingua). Da chiamare in app.py dopo pg.run().
    """
    cfg = {
        "manifest_url": _MANIFEST_URL,
        "apple_icon_url": _APPLE_ICON_URL,
        "theme_color": _THEME_COLOR,
        "app_title": _APP_TITLE,
        "title": ui_text.get("pwa_install_title", _APP_TITLE),
        "text": ui_text.get("pwa_install_text", ""),
        "button": ui_text.get("pwa_install_button", ""),
        "android": ui_text.get("pwa_install_android", ""),
        "ios": ui_text.get("pwa_install_ios", ""),
        "close": ui_text.get("pwa_install_close", ""),
    }
    # height=0: l'iframe del componente non deve occupare spazio nel layout.
    components.html(_TEMPLATE.replace("__CFG__", json.dumps(cfg)), height=0)

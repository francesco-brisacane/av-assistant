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

Cosa si ottiene: icona AV + apertura standalone su iOS ("Aggiungi a Home") e
Android ("Aggiungi a schermata Home" dal menu).

Cosa NON si ottiene (limite piattaforma, non del codice): il prompt automatico
"Installa app" di Chrome e l'offline, che richiederebbero un service worker con
scope "/" non ospitabile su Streamlit Cloud. L'app richiede comunque la rete.
"""
import streamlit.components.v1 as components

_REPO = "francesco-brisacane/av-assistant"
_ASSET_TAG = "pwa-v1"  # tag git immutabile che contiene static/manifest.json + icone
_CDN = f"https://cdn.jsdelivr.net/gh/{_REPO}@{_ASSET_TAG}/static"

_MANIFEST_URL = f"{_CDN}/manifest.json"
_APPLE_ICON_URL = f"{_CDN}/apple-touch-icon.png"
_THEME_COLOR = "#000000"
_APP_TITLE = "AV Assistant"


def inject_pwa() -> None:
    """Sovrascrive il manifest del documento top per puntarlo agli asset AV.

    Risale a window.top (il wrapper Streamlit Cloud, o la pagina stessa in
    locale) e riscrive href del <link rel=manifest> esistente + apple-touch-icon
    + meta. Idempotente per natura (riscrive in-place ad ogni rerun). Da chiamare
    in app.py dopo pg.run().
    """
    html = f"""
    <script>
      (function() {{
        var doc;
        try {{ doc = window.top.document; }} catch (e) {{
          try {{ doc = window.parent.document; }} catch (e2) {{ return; }}
        }}
        if (!doc || !doc.head) return;

        // Chrome onora solo il PRIMO <link rel=manifest>: riusiamo quello
        // esistente (di Streamlit) sovrascrivendone l'href, non aggiungendone uno.
        var link = doc.querySelector('link[rel="manifest"]');
        if (!link) {{
          link = doc.createElement("link");
          link.setAttribute("rel", "manifest");
          doc.head.appendChild(link);
        }}
        link.setAttribute("crossorigin", "anonymous");  // fetch CORS (jsDelivr)
        if (link.getAttribute("href") !== "{_MANIFEST_URL}") {{
          link.setAttribute("href", "{_MANIFEST_URL}");
        }}

        // apple-touch-icon: sovrascrivi tutti quelli esistenti, o creane uno.
        var icons = doc.querySelectorAll('link[rel~="apple-touch-icon"]');
        if (icons.length) {{
          icons.forEach(function (n) {{ n.setAttribute("href", "{_APPLE_ICON_URL}"); }});
        }} else {{
          var a = doc.createElement("link");
          a.setAttribute("rel", "apple-touch-icon");
          a.setAttribute("href", "{_APPLE_ICON_URL}");
          doc.head.appendChild(a);
        }}

        function setMeta(name, content) {{
          var m = doc.querySelector('meta[name="' + name + '"]');
          if (!m) {{
            m = doc.createElement("meta");
            m.setAttribute("name", name);
            doc.head.appendChild(m);
          }}
          m.setAttribute("content", content);
        }}
        setMeta("theme-color", "{_THEME_COLOR}");
        setMeta("mobile-web-app-capable", "yes");
        setMeta("apple-mobile-web-app-capable", "yes");
        setMeta("apple-mobile-web-app-status-bar-style", "black");
        setMeta("apple-mobile-web-app-title", "{_APP_TITLE}");
      }})();
    </script>
    """
    # height=0: l'iframe del componente non deve occupare spazio nel layout.
    components.html(html, height=0)

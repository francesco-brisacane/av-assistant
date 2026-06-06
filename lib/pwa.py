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
    """Inietta manifest + meta tag PWA nell'`<head>` della pagina padre.

    Idempotente: se i tag sono gia' presenti non fa nulla (gestisce i rerun di
    Streamlit). Da chiamare una volta in app.py, dopo pg.run().
    """
    html = f"""
    <script>
      (function() {{
        var doc = window.parent.document;
        if (doc.getElementById("av-pwa-manifest")) return;

        function meta(name, content) {{
          var m = doc.createElement("meta");
          m.setAttribute("name", name);
          m.setAttribute("content", content);
          doc.head.appendChild(m);
        }}

        // Manifest cross-origin (jsDelivr): serve crossorigin per la fetch CORS.
        var link = doc.createElement("link");
        link.id = "av-pwa-manifest";
        link.rel = "manifest";
        link.crossOrigin = "anonymous";
        link.href = "{_MANIFEST_URL}";
        doc.head.appendChild(link);

        var apple = doc.createElement("link");
        apple.rel = "apple-touch-icon";
        apple.href = "{_APPLE_ICON_URL}";
        doc.head.appendChild(apple);

        meta("theme-color", "{_THEME_COLOR}");
        meta("mobile-web-app-capable", "yes");
        meta("apple-mobile-web-app-capable", "yes");
        meta("apple-mobile-web-app-status-bar-style", "black");
        meta("apple-mobile-web-app-title", "{_APP_TITLE}");
      }})();
    </script>
    """
    # height=0: l'iframe del componente non deve occupare spazio nel layout.
    components.html(html, height=0)

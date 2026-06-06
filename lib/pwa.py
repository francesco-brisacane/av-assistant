"""Rende l'app installabile come PWA ("Aggiungi a Home") su mobile.

Vincolo Streamlit: non abbiamo accesso all'`<head>` del documento servito da
Streamlit, ne' agli header HTTP (su Streamlit Cloud). I componenti pero' girano
in un iframe *same-origin*, quindi da li' possiamo iniettare nel `<head>` della
pagina padre il link al manifest e i meta tag Apple.

Cosa si ottiene:
- iOS/Safari: "Condividi -> Aggiungi a Home", apertura a schermo intero, icona AV.
- Android/Chrome: "Aggiungi alla schermata Home" dal menu, icona + apertura standalone.

Cosa NON si ottiene su Streamlit Cloud (limite della piattaforma, non del codice):
- prompt automatico "Installa app" di Chrome e funzionamento offline: richiedono
  un service worker con scope "/" e l'header Service-Worker-Allowed, non
  impostabili su Streamlit Cloud. L'app comunque richiede rete (Firestore/LLM),
  quindi l'offline non sarebbe utile.

Prerequisito: `enableStaticServing = true` in .streamlit/config.toml e i file
in static/ (manifest.json + icone), serviti da Streamlit sotto /app/static/.
"""
import streamlit.components.v1 as components

# I file statici di Streamlit sono serviti sotto /app/static/.
_MANIFEST_URL = "/app/static/manifest.json"
_APPLE_ICON_URL = "/app/static/apple-touch-icon.png"
_THEME_COLOR = "#000000"
_APP_TITLE = "AV Assistant"


def inject_pwa() -> None:
    """Inietta manifest + meta tag PWA nell'`<head>` della pagina padre.

    Idempotente: se i tag sono gia' presenti non fa nulla (gestisce i rerun di
    Streamlit). Da chiamare una volta, presto, in app.py.
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

        var link = doc.createElement("link");
        link.id = "av-pwa-manifest";
        link.rel = "manifest";
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

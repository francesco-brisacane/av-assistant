"""Bottone microfono nella chat: dettatura vocale via Web Speech API del browser.

Nessun modello STT lato server: usiamo `SpeechRecognition` nativo del browser
(Chrome/Edge: `webkitSpeechRecognition`, trascrizione fatta dai server Google;
Safari macOS/iOS 14.5+: motore Apple/Siri). Su browser senza supporto (es.
Firefox) il bottone semplicemente non compare.

Come per lib/pwa.py, non possiamo toccare il DOM di Streamlit da Python: il
componente gira in un iframe same-origin e da li' inietta uno <script> nel
documento padre (il frame dell'app, dove vive st.chat_input). Il codice gira
quindi nel realm del padre, per due motivi:
  1. il permesso microfono viene chiesto per il frame dell'app, non per l'iframe
     del componente (che non ha `allow="microphone"`);
  2. l'iframe del componente puo' essere rimontato ad ogni rerun, mentre il
     riconoscimento in corso e i listener devono sopravvivere.

Il bottone viene inserito accanto al tasto invio della chat input; un
setInterval lo re-inserisce se React rimonta la chat input. Il testo dettato
viene scritto nella textarea (con il setter nativo + evento `input`, cosi' lo
stato React di Streamlit si aggiorna) ma NON inviato: l'utente lo rivede e
preme invio.
"""
import json

import streamlit.components.v1 as components

_SPEECH_LANG = {"IT": "it-IT", "EN": "en-US"}

_TEMPLATE = """
<script>
(function () {
  var CFG = __CFG__;
  var win;
  try { win = window.parent; void win.document; } catch (e) { return; }

  // Aggiorna la config (lingua/label) anche se lo script e' gia' iniettato.
  win.__avVoiceCfg = win.JSON.parse(JSON.stringify(CFG));
  if (win.__avVoiceLoaded) return;
  win.__avVoiceLoaded = true;

  function main(win) {
    var doc = win.document;
    var Rec = win.SpeechRecognition || win.webkitSpeechRecognition;
    if (!Rec) return;  // browser non supportato: nessun bottone

    var MIC_SVG = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<rect x="9" y="2" width="6" height="12" rx="3"/>' +
      '<path d="M5 10v1a7 7 0 0 0 14 0v-1"/><line x1="12" y1="18" x2="12" y2="22"/></svg>';

    var style = doc.createElement("style");
    // pointer-events:auto: il contenitore dei bottoni della chat input ha
    // pointer-events:none (sta sopra la textarea), senza i click passerebbero sotto.
    style.textContent =
      "#av-voice-btn{pointer-events:auto;display:inline-flex;align-items:center;justify-content:center;" +
      "width:2rem;height:2rem;margin:0 .25rem;padding:0;border:none;border-radius:.5rem;" +
      "background:transparent;color:inherit;opacity:.6;cursor:pointer;flex-shrink:0;align-self:center}" +
      "#av-voice-btn:hover{opacity:1}" +
      "#av-voice-btn.av-listening{opacity:1;color:#ff4b4b;animation:av-voice-pulse 1.2s ease-in-out infinite}" +
      "@keyframes av-voice-pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.15)}}" +
      "#av-voice-msg{position:absolute;bottom:calc(100% + .5rem);right:0;max-width:20rem;" +
      "padding:.4rem .7rem;border-radius:.5rem;background:#262730;color:#fff;font-size:.85rem;" +
      "box-shadow:0 2px 8px rgba(0,0,0,.25);z-index:1000}";
    doc.head.appendChild(style);

    var rec = null, listening = false, baseText = "", msgTimer = null;

    function cfg() { return win.__avVoiceCfg || {}; }
    function textarea() { return doc.querySelector('[data-testid="stChatInputTextArea"]'); }

    function setText(ta, value) {
      // Setter nativo + evento input: unico modo di aggiornare un campo
      // controllato da React senza che lo sovrascriva al render successivo.
      var setter = Object.getOwnPropertyDescriptor(win.HTMLTextAreaElement.prototype, "value").set;
      setter.call(ta, value);
      ta.dispatchEvent(new win.Event("input", { bubbles: true }));
    }

    function showMsg(text) {
      var root = doc.querySelector('[data-testid="stChatInput"]');
      if (!root) return;
      var m = doc.getElementById("av-voice-msg");
      if (!m) {
        m = doc.createElement("div");
        m.id = "av-voice-msg";
        root.style.position = root.style.position || "relative";
        root.appendChild(m);
      }
      m.textContent = text;
      clearTimeout(msgTimer);
      msgTimer = setTimeout(function () { if (m.parentNode) m.parentNode.removeChild(m); }, 4000);
    }

    function render() {
      var b = doc.getElementById("av-voice-btn");
      if (!b) return;
      b.classList.toggle("av-listening", listening);
      var label = listening ? cfg().label_stop : cfg().label_start;
      b.title = label || "";
      b.setAttribute("aria-label", label || "");
      b.setAttribute("aria-pressed", listening ? "true" : "false");
    }

    function start() {
      var ta = textarea();
      if (!ta) return;
      baseText = ta.value ? ta.value.replace(/\\s*$/, " ") : "";
      rec = new Rec();
      rec.lang = cfg().lang || "it-IT";
      rec.interimResults = true;
      rec.continuous = false;  // continuous e' instabile su mobile (Android/iOS)
      rec.maxAlternatives = 1;

      rec.onresult = function (ev) {
        var t = "";
        for (var i = 0; i < ev.results.length; i++) t += ev.results[i][0].transcript;
        var cur = textarea();
        if (cur) setText(cur, baseText + t);
      };
      rec.onerror = function (ev) {
        if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
          // Su http non-localhost (es. IP di rete da telefono) il browser nega
          // sempre il microfono, a prescindere dai permessi del sito.
          showMsg(win.isSecureContext ? cfg().err_permission : cfg().err_insecure);
        } else if (ev.error !== "no-speech" && ev.error !== "aborted") {
          showMsg(cfg().err_generic + " (" + ev.error + ")");
        }
      };
      rec.onend = function () {
        listening = false;
        rec = null;
        render();
        var cur = textarea();
        if (cur) cur.focus();
      };

      try {
        rec.start();
        listening = true;
      } catch (e) {
        rec = null;
        showMsg(cfg().err_generic);
      }
      render();
    }

    function stop() { if (rec) rec.stop(); }

    function ensureButton() {
      var submit = doc.querySelector('[data-testid="stChatInputSubmitButton"]');
      if (!submit) return;
      var b = doc.getElementById("av-voice-btn");
      if (b && b.nextSibling === submit) return;
      if (!b) {
        b = doc.createElement("button");
        b.id = "av-voice-btn";
        b.type = "button";
        b.innerHTML = MIC_SVG;
        b.addEventListener("click", function (e) {
          e.preventDefault();
          listening ? stop() : start();
        });
      }
      submit.parentNode.insertBefore(b, submit);
      render();
    }

    ensureButton();
    setInterval(ensureButton, 1000);
  }

  // Lo script iniettato gira nel realm del padre (vedi docstring del modulo).
  var s = win.document.createElement("script");
  s.textContent = "(" + main.toString() + ")(window);";
  win.document.head.appendChild(s);
})();
</script>
"""


def inject_voice_input(lang_code: str, ui_text: dict) -> None:
    """Aggiunge il bottone microfono alla st.chat_input della pagina.

    Idempotente: la prima chiamata inietta lo script nel frame dell'app, le
    successive aggiornano solo lingua e label (es. al cambio lingua).
    """
    cfg = {
        "lang": _SPEECH_LANG.get(lang_code, "en-US"),
        "label_start": ui_text.get("voice_start", ""),
        "label_stop": ui_text.get("voice_stop", ""),
        "err_permission": ui_text.get("voice_error_permission", ""),
        "err_insecure": ui_text.get("voice_error_insecure", ""),
        "err_generic": ui_text.get("voice_error_generic", ""),
    }
    # height=0: l'iframe del componente non deve occupare spazio nel layout.
    components.html(_TEMPLATE.replace("__CFG__", json.dumps(cfg)), height=0)

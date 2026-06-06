"""Genera le icone PWA da assets/logo.webp.

Il logo AV e' bianco su sfondo trasparente: lo componiamo su sfondo nero
(#000000, coerente col tema dark dell'app) con un margine di sicurezza cosi'
le icone funzionano anche come "maskable" su Android (zona sicura centrale).

Rigenerare dopo aver cambiato il logo:
    venv/Scripts/python.exe assets/gen_pwa_icons.py
"""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "logo.webp"
OUT = ROOT / "static"
BG = (0, 0, 0, 255)          # nero brand
PADDING_RATIO = 0.18         # margine -> safe zone per maskable

# (filename, size, padding_ratio)
TARGETS = [
    ("icon-192.png", 192, PADDING_RATIO),
    ("icon-512.png", 512, PADDING_RATIO),
    ("apple-touch-icon.png", 180, 0.12),  # iOS arrotonda gli angoli, meno padding
]


def build(size: int, padding_ratio: float) -> Image.Image:
    logo = Image.open(SRC).convert("RGBA")
    canvas = Image.new("RGBA", (size, size), BG)
    inner = int(size * (1 - 2 * padding_ratio))
    # Ridimensiona il logo mantenendo le proporzioni dentro il box "inner"
    lw, lh = logo.size
    scale = min(inner / lw, inner / lh)
    new = (max(1, int(lw * scale)), max(1, int(lh * scale)))
    logo = logo.resize(new, Image.LANCZOS)
    pos = ((size - new[0]) // 2, (size - new[1]) // 2)
    canvas.paste(logo, pos, logo)
    return canvas.convert("RGB")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, size, pad in TARGETS:
        img = build(size, pad)
        img.save(OUT / name, "PNG")
        print(f"  scritto {name} ({size}x{size})")


if __name__ == "__main__":
    main()

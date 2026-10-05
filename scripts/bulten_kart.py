#!/usr/bin/env python3
"""
metrometre — bülten paylaşım kartı (Open Graph görseli) üretici.

bulten/bultenler.json'daki her sayı için 1200×630 bir PNG üretir:
  bulten/<YYYY-AA>.png

Kartta: "Aylık bülten" etiketi, sayının ayı, öne çıkan hatların kod
rozetleri ve sağda İstanbul raylı sistem ağı (öne çıkan hatlar vurgulu).
bulten/<YYYY-AA>.html sayfasının og:image etiketi bu görseli gösterir.

bultenler.json'da her sayı için:
  "ay":     "2026-09"                       (zorunlu)
  "baslik": "M12 son düzlüğe girdi, …"      (arşiv ve harita düğmesi için; kartta yer almaz)
  "hatlar": [6, 70, 49, 69]                 (isteğe bağlı: vurgulanacak OBJECTID'ler)

Kullanım (repo kökünden):
  python scripts/bulten_kart.py               # kartı eksik ya da içeriği değişen sayılar
  python scripts/bulten_kart.py --hepsi       # tümünü yeniden üret
  python scripts/bulten_kart.py --ay 2026-09  # yalnızca bir sayı

Hangi kartın hangi içerikle üretildiği bulten/kartlar.json'da tutulur; ay ve
vurgulanan hatlar değişmedikçe kart yeniden üretilmez (gereksiz commit olmaz).

Bağımlılıklar ve font: scripts/og_cards.py ile aynı (playwright + Inter;
FONT_DIR verilirse font gömülür).
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIST_FILE = ROOT / "bulten" / "bultenler.json"
STAMP_FILE = ROOT / "bulten" / "kartlar.json"

# Ortak yardımcılar hat kartı üreticisinden alınır (tek kaynak)
_spec = importlib.util.spec_from_file_location("og_cards", Path(__file__).with_name("og_cards.py"))
og = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(og)

W, H = og.W, og.H
MAP_W = 540


def network_svg(features, highlight, mw, mh):
    """Tüm ağ; yapımı süren hatlar renkli, öne çıkanlar halelı ve kalın."""
    yapim = [f for f in features if str(f["properties"].get("PR_DURUM")) in og.YAPIM]
    frame = [c for f in yapim for l in og.parts(f["geometry"]) for c in l]
    xs = [c[0] * og.K for c in frame]
    ys = [c[1] for c in frame]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    span = max((max(xs) - min(xs)) / mw, (max(ys) - min(ys)) / mh) * 1.18
    s = 1.0 / span

    def d(line):
        pts = [((c[0] * og.K - cx) * s + mw / 2, (cy - c[1]) * s + mh / 2) for c in line]
        return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    base, other, halo, top = [], [], [], []
    for f in features:
        p = f["properties"]
        dur = str(p.get("PR_DURUM"))
        if dur == "0":
            continue
        color = og.norm_hex(p.get("RENK_HEX"))
        hl = p.get("OBJECTID") in highlight
        for l in og.parts(f["geometry"]):
            if len(l) < 2:
                continue
            if hl:
                halo.append(f'<path d="{d(l)}" stroke="{color}" stroke-opacity=".22" stroke-width="20"/>')
                top.append(f'<path d="{d(l)}" stroke="{color}" stroke-width="8"/>')
            elif dur in og.YAPIM:
                other.append(f'<path d="{d(l)}" stroke="{color}" stroke-opacity=".55" stroke-width="4.5"/>')
            elif dur == "1":
                base.append(f'<path d="{d(l)}" stroke="#C9C9C6" stroke-width="2.5"/>')
    return (f'<svg width="{mw}" height="{mh}" viewBox="0 0 {mw} {mh}" fill="none" '
            f'stroke-linecap="round" stroke-linejoin="round">'
            f'{"".join(base)}{"".join(other)}{"".join(halo)}{"".join(top)}</svg>')


def luminance(hex_color):
    """WCAG göreli parlaklık (0 = siyah, 1 = beyaz)."""
    h = hex_color.lstrip("#")
    def ch(v):
        v = int(v, 16) / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(h[i:i + 2]) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def chips_html(features, highlight):
    seen, out = set(), []
    by_id = {f["properties"].get("OBJECTID"): f["properties"] for f in features}
    for oid in highlight:
        p = by_id.get(oid)
        if not p:
            continue
        kod = str(p.get("KOD") or "").split(" ")[0].strip()
        color = og.norm_hex(p.get("RENK_HEX"))
        if (kod, color) in seen:
            continue
        seen.add((kod, color))
        # açık renkli hatlarda (M12, T7, M7) koyu, diğerlerinde beyaz yazı
        fg = "#1A1A1A" if luminance(color) > 0.4 else "#FFFFFF"
        out.append(f'<span class="chip" style="background:{color};color:{fg}">{og.esc(kod)}</span>')
    return "".join(out)


def card_html(issue, features, logo_uri):
    ay = issue["ay"]
    highlight = [int(x) for x in issue.get("hatlar", []) if str(x).strip().isdigit()]
    return f"""<!doctype html><html><head><meta charset="utf-8">{og.font_css()}<style>
*{{margin:0;box-sizing:border-box}}
body{{width:{W}px;height:{H}px;background:#fff;font-family:Inter,sans-serif;position:relative;overflow:hidden;color:#111}}
.map{{position:absolute;right:0;top:0;width:{MAP_W}px;height:{H}px;background:#F5F5F3;overflow:hidden}}
.map:before{{content:'';position:absolute;inset:0;box-shadow:inset 24px 0 24px -24px rgba(0,0,0,.10)}}
.bar{{position:absolute;left:0;top:0;bottom:0;width:14px;background:#B4271F}}
.l{{position:absolute;left:68px;top:56px;width:{W - MAP_W - 68 - 52}px;bottom:56px;display:flex;flex-direction:column}}
.logo img{{height:30px;display:block}}
.logo b{{font-size:28px;font-weight:800;letter-spacing:-.03em}}
.eyebrow{{margin-top:44px;font-size:18px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#B4271F}}
.eyebrow{{margin-top:auto}}
.month{{margin-top:8px;font-size:104px;line-height:.95;font-weight:800;letter-spacing:-.05em}}
.sub{{margin-top:18px;font-size:28px;line-height:1.25;font-weight:600;letter-spacing:-.02em;color:#6B6B6B}}
.chips{{margin-top:44px;margin-bottom:auto;display:flex;flex-wrap:wrap;gap:10px}}
.chip{{display:inline-flex;align-items:center;justify-content:center;min-width:64px;height:38px;padding:0 12px;border-radius:8px;font-size:20px;font-weight:800;letter-spacing:-.01em}}
.foot{{margin-top:26px;padding-top:18px;border-top:2px solid #111;display:flex;justify-content:space-between;align-items:baseline}}
.foot span{{font-size:19px;font-weight:600;color:#6B6B6B}}
.foot b{{font-size:21px;font-weight:700;color:#B4271F}}
</style></head><body>
<div class="map">{network_svg(features, highlight, MAP_W, H)}</div>
<div class="bar"></div>
<div class="l">
  <div class="logo">{f'<img src="{logo_uri}">' if logo_uri else '<b>metrometre</b>'}</div>
  <div class="eyebrow">Aylık bülten</div>
  <div class="month">{og.esc(og.ym_long(ay))}</div>
  <div class="sub">Raylı sistem projelerinde<br>ayın gelişmeleri</div>
  <div class="chips">{chips_html(features, highlight)}</div>
  <div class="foot"><span>İstanbul ve çevresi</span><b>metrometre.com/bulten</b></div>
</div>
</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ay", default="", help="yalnızca bu sayı (YYYY-AA)")
    ap.add_argument("--hepsi", action="store_true", help="değişmemiş olanlar dahil hepsini üret")
    a = ap.parse_args()

    issues = json.loads(LIST_FILE.read_text(encoding="utf-8")).get("bultenler", [])
    issues = [i for i in issues if og.ym_of(i.get("ay")) and (not a.ay or i["ay"] == a.ay)]
    try:
        stamps = json.loads(STAMP_FILE.read_text(encoding="utf-8"))
    except Exception:
        stamps = {}

    def stamp(i):
        key = json.dumps([i.get("ay"), i.get("hatlar", [])], ensure_ascii=False)
        return hashlib.sha1(key.encode()).hexdigest()[:10]

    todo = [i for i in issues
            if a.hepsi or a.ay or stamps.get(i["ay"]) != stamp(i)
            or not (ROOT / "bulten" / f"{i['ay']}.png").exists()]
    if not todo:
        print("Kartlar güncel.")
        return 0
    issues = todo

    features = json.loads((ROOT / og.HAT_FILE).read_text(encoding="utf-8"))["features"]
    features = [f for f in features if f.get("geometry")]
    logo = ROOT / og.LOGO_FILE
    logo_uri = og.data_uri(logo, "image/png") if logo.exists() else None

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for issue in issues:
            out = ROOT / "bulten" / f"{issue['ay']}.png"
            page.set_content(card_html(issue, features, logo_uri), wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(out))
            stamps[issue["ay"]] = stamp(issue)
            print(f"  ✓ {out.relative_to(ROOT)}")
        browser.close()
    STAMP_FILE.write_text(json.dumps(dict(sorted(stamps.items())), ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
metrometre — hat başına paylaşım kartı (Open Graph görseli) üretici.

Yapımı süren her hat (PR_DURUM 2/3/4) için 1200×630 bir PNG üretir:
hat adı, güncel fiziki ilerleme, veri dönemi, tahmini açılış ve hattı
vurgulayan küçük bir harita. X / WhatsApp / LinkedIn'de metrometre.com/hat/<id>
bağlantısı paylaşıldığında önizleme olarak bu görsel görünür.

Çıktılar (repo köküne göre):
  og/<OBJECTID>.png   — kart görselleri
  og/lines.json       — tüm hatların kısa özeti (ad, durum, ilerleme, veri dönemi,
                        açılış) + kartı olanlar için "card" sürümü. Cloudflare
                        Function bağlantı önizlemesini bu küçük dosyadan kurar;
                        görsel adresine ?v=<card> ekleyerek sosyal ağların eski
                        kartı önbellekten göstermesini engeller.

Kullanım:
  python scripts/og_cards.py                 # repo kökünden
  python scripts/og_cards.py --only 6,69     # yalnızca belirli OBJECTID'ler

Bağımlılıklar: playwright (chromium). Font için Inter: FONT_DIR ortam değişkeni
@fontsource/inter/files klasörünü gösteriyorsa gömülür, yoksa Google Fonts'tan
yüklenir.
"""
import argparse
import base64
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HAT_FILE = "RAYLI_SISTEM_HAT_LN.geojson"
OVERLAY_FILE = "overlay.csv"
LOGO_FILE = "metrometre_logotype.png"
YAPIM = {"2", "3", "4"}
W, H = 1200, 630

TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
             "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
DURUM_LABEL = {"2": "Test aşamasında", "3": "İnşa aşamasında", "4": "Beklemede"}


# ───────────────────────── yardımcılar ─────────────────────────
def norm_header(h):
    """Başlık eşleştirme: TR harf + boşluk/altçizgi duyarsız (sitedeki sheetNorm ile aynı)."""
    s = str(h or "").strip().lower()
    for a, b in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c")):
        s = s.replace(a, b)
    return re.sub(r"[\s_]+", "", s)


def parse_pct(raw):
    s = str(raw or "").strip()
    if not s or s.startswith("("):
        return None
    try:
        v = float(s.replace("%", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return max(0.0, min(100.0, v))


def ym_of(raw):
    s = str(raw or "").strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})$", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return None


def ym_long(ym):
    y, mo = ym.split("-")
    return f"{TR_MONTHS[int(mo) - 1]} {y}"


def fmt_tr_date(raw):
    """Sitedeki fmtTRDate ile aynı kurallar."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if re.fullmatch(r"[-–—]+", s):
        return "Belirsiz"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return f"{int(m.group(3))} {TR_MONTHS[int(m.group(2)) - 1]} {m.group(1)}"
    m = re.match(r"^(\d{4})-(\d{1,2})$", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{TR_MONTHS[int(m.group(2)) - 1]} {m.group(1)}"
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", s)
    if m:
        return f"{int(m.group(1))} {TR_MONTHS[int(m.group(2)) - 1]} {m.group(3)}"
    m = re.match(r"^(\d{1,2})\.(\d{4})$", s)
    if m:
        return f"{TR_MONTHS[int(m.group(1)) - 1]} {m.group(2)}"
    return s


def pct_text(v):
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def norm_hex(h, default="#9B9B9B"):
    s = str(h or "").strip().lstrip("#")
    return "#" + s if s else default


def readable(hex_color, max_lum=0.30):
    """Açık renkler (ör. M12 sarı-yeşil) beyaz zeminde okunaklı olsun diye koyulaştırılır."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    f = 1.0
    # Kanallar f ile ölçeklenince bağıl parlaklık ~f^2.2 ile düşer; büyük metin için
    # beyaz zeminde ~3:1 kontrast (bağıl parlaklık ≤ 0.30) hedeflenir.
    while lum * f ** 2.2 > max_lum and f > 0.35:
        f -= 0.02
    return "#" + "".join(f"{int(c * 255 * f):02x}" for c in (r, g, b))


def esc(s):
    return html.escape(str(s or ""), quote=True)


# ───────────────────────── veri ─────────────────────────
def load_overlay(path):
    """OBJECTID → {pct, ym, acilis} (her alan, dolu olduğu en güncel aydan)."""
    out = {}
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return out
    head = [norm_header(h) for h in rows[0]]

    def col(*names):
        for n in names:
            if n in head:
                return head.index(n)
        return -1

    i_oid = col("objectid", "object_id", "oid")
    i_ym = col("yilay", "donem", "ay")
    i_pct = col("ilerleme", "fizikiilerleme", "yuzde", "oran", "pct")
    i_ac = col("tahminiacilis", "acilis", "acilistarihi", "pr_acilis")
    if i_oid < 0 or i_ym < 0:
        return out
    for r in rows[1:]:
        if len(r) <= max(i_oid, i_ym):
            continue
        oid = r[i_oid].strip()
        ym = ym_of(r[i_ym])
        if not oid or not ym:
            continue
        rec = out.setdefault(oid, {})
        pct = parse_pct(r[i_pct]) if i_pct >= 0 and i_pct < len(r) else None
        if pct is not None and ym >= rec.get("ym", ""):
            rec["pct"], rec["ym"] = pct, ym
        ac = r[i_ac].strip() if i_ac >= 0 and i_ac < len(r) else ""
        if ac and ym >= rec.get("ac_ym", ""):
            rec["acilis"], rec["ac_ym"] = ac, ym
    return out


def parts(geom):
    if not geom:
        return []
    t = geom.get("type")
    if t == "LineString":
        return [geom["coordinates"]]
    if t == "MultiLineString":
        return geom["coordinates"]
    return []


# ───────────────────────── harita (SVG) ─────────────────────────
K = math.cos(math.radians(41.03))  # boylam ölçeği (İstanbul enlemi)


def map_svg(features, target, mw, mh):
    """Hedef hattı merkezde, diğer hatları soluk çizen SVG."""
    tcoords = [c for l in parts(target["geometry"]) for c in l]
    xs = [c[0] * K for c in tcoords]
    ys = [c[1] for c in tcoords]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    span_x = max(max(xs) - min(xs), 0.001)
    span_y = max(max(ys) - min(ys), 0.001)
    # Kısa hatlar aşırı yakınlaşmasın: en az ~9 km'lik görüş alanı
    min_span = 0.05
    span = max(span_x / mw, span_y / mh) * 1.55
    span = max(span, min_span / min(mw, mh))
    s = 1.0 / span  # derece → piksel

    def P(c):
        return ((c[0] * K - cx) * s + mw / 2, (cy - c[1]) * s + mh / 2)

    def d(line):
        pts = [P(c) for c in line]
        return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    def visible(line):
        return any(-50 <= P(c)[0] <= mw + 50 and -50 <= P(c)[1] <= mh + 50 for c in line[::3] + [line[-1]])

    bg, fg = [], []
    tid = target["properties"].get("OBJECTID")
    for f in features:
        p = f["properties"]
        if p.get("OBJECTID") == tid or str(p.get("PR_DURUM")) == "0":
            continue
        dur = str(p.get("PR_DURUM"))
        for l in parts(f["geometry"]):
            if len(l) < 2 or not visible(l):
                continue
            if dur == "1":
                bg.append(f'<path d="{d(l)}" stroke="{norm_hex(p.get("RENK_HEX"))}" stroke-opacity=".30" stroke-width="3.5"/>')
            elif dur in YAPIM:
                bg.append(f'<path d="{d(l)}" stroke="{norm_hex(p.get("RENK_HEX"))}" stroke-opacity=".45" stroke-width="4"/>')
            else:
                bg.append(f'<path d="{d(l)}" stroke="#BDBDBD" stroke-width="2" stroke-dasharray="5 6"/>')
    color = norm_hex(target["properties"].get("RENK_HEX"))
    for l in parts(target["geometry"]):
        if len(l) < 2:
            continue
        fg.append(f'<path d="{d(l)}" stroke="{color}" stroke-opacity=".22" stroke-width="26"/>')
        fg.append(f'<path d="{d(l)}" stroke="{color}" stroke-width="11"/>')
    return (f'<svg width="{mw}" height="{mh}" viewBox="0 0 {mw} {mh}" fill="none" '
            f'stroke-linecap="round" stroke-linejoin="round">{"".join(bg)}{"".join(fg)}</svg>')


# ───────────────────────── kaynaklar ─────────────────────────
def data_uri(path_or_bytes, mime):
    b = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    return f"data:{mime};base64,{base64.b64encode(b).decode()}"


_icon_cache = {}


def fetch_icon(url):
    """Hat sembolünü (Wikimedia SVG) indirip gömer; erişilemezse None."""
    if not url:
        return None
    if url in _icon_cache:
        return _icon_cache[url]
    uri = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "metrometre-og-cards/1.0 (https://metrometre.com)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read()
            ctype = r.headers.get("Content-Type", "").split(";")[0] or (
                "image/svg+xml" if url.lower().endswith(".svg") else "image/png")
            uri = data_uri(body, ctype)
    except Exception as e:  # ağ yoksa renkli daire + kod kullanılır
        print(f"  ikon alınamadı ({e.__class__.__name__}): {url}", file=sys.stderr)
    _icon_cache[url] = uri
    return uri


def font_css():
    fdir = os.environ.get("FONT_DIR")
    if fdir and Path(fdir).is_dir():
        css = []
        for w in (400, 500, 600, 700, 800):
            for sub, rng in (("latin", None), ("latin-ext", "U+0100-024F, U+1E00-1EFF")):
                fp = Path(fdir) / f"inter-{sub}-{w}-normal.woff2"
                if fp.exists():
                    css.append("@font-face{font-family:Inter;font-weight:%d;src:url(%s)%s}"
                               % (w, data_uri(fp, "font/woff2"), f";unicode-range:{rng}" if rng else ""))
        if css:
            return "<style>" + "".join(css) + "</style>"
    return ('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800'
            '&display=block" rel="stylesheet">')


# ───────────────────────── kart HTML ─────────────────────────
def card_html(feat, info, features, logo_uri):
    p = feat["properties"]
    color = norm_hex(p.get("RENK_HEX"))
    kod = str(p.get("KOD") or "").strip()
    ad = str(p.get("AD") or "").strip()
    pct = info["pct"]
    ym = info.get("ym")
    acilis = fmt_tr_date(info.get("acilis")) or "—"
    durum = DURUM_LABEL.get(str(p.get("PR_DURUM")), "")
    icon = fetch_icon(p.get("IKON_URL"))
    icon_html = (f'<img class="ico" src="{icon}">' if icon else
                 f'<div class="ico fb" style="background:{color}">{esc(kod[:3])}</div>')
    name_size = 50 if len(ad) <= 26 else 42 if len(ad) <= 36 else 36
    asof = f"{ym_long(ym)} itibarıyla" if ym else ""
    mapw = 500
    return f"""<!doctype html><html><head><meta charset="utf-8">{font_css()}<style>
*{{margin:0;box-sizing:border-box}}
body{{width:{W}px;height:{H}px;background:#fff;font-family:Inter,sans-serif;position:relative;overflow:hidden;color:#111}}
.bar{{position:absolute;left:0;top:0;bottom:0;width:14px;background:{color}}}
.map{{position:absolute;right:0;top:0;width:{mapw}px;height:{H}px;background:#F5F5F3;overflow:hidden}}
.map:before{{content:'';position:absolute;inset:0;box-shadow:inset 24px 0 24px -24px rgba(0,0,0,.10)}}
.l{{position:absolute;left:68px;top:56px;width:{W - mapw - 68 - 48}px;bottom:56px;display:flex;flex-direction:column}}
.head{{display:flex;align-items:center;gap:16px}}
.ico{{width:64px;height:64px;border-radius:50%;object-fit:cover;flex-shrink:0}}
.fb{{display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:22px;letter-spacing:-.03em}}
.pill{{font-size:18px;font-weight:600;padding:6px 14px;border-radius:999px;background:#EFF6FF;color:#1E40AF}}
.name{{margin-top:22px;font-size:{name_size}px;line-height:1.1;font-weight:800;letter-spacing:-.03em}}
.big{{margin-top:auto;display:flex;align-items:flex-end;gap:18px}}
.pct{{font-size:128px;line-height:.86;font-weight:800;letter-spacing:-.05em;color:{readable(color)}}}
.pct small{{font-size:72px;letter-spacing:-.02em}}
.pl{{padding-bottom:10px;font-size:20px;line-height:1.35;color:#6B6B6B;font-weight:500}}
.pl b{{display:block;color:#111;font-weight:700;font-size:22px}}
.prog{{margin-top:22px;height:14px;border-radius:7px;background:#ECECEC;overflow:hidden}}
.prog i{{display:block;height:100%;width:{pct:.2f}%;background:{color};border-radius:7px}}
.foot{{margin-top:30px;display:flex;align-items:flex-end;justify-content:space-between}}
.ac{{font-size:16px;font-weight:700;color:#757575;text-transform:uppercase;letter-spacing:.06em}}
.ac b{{display:block;margin-top:6px;font-size:28px;color:#111;text-transform:none;letter-spacing:-.01em}}
.brand{{text-align:right}}
.brand img{{height:30px;display:block;margin-left:auto}}
.brand span{{display:block;margin-top:8px;font-size:17px;font-weight:600;color:#B4271F}}
</style></head><body>
<div class="map">{map_svg(features, feat, mapw, H)}</div>
<div class="bar"></div>
<div class="l">
  <div class="head">{icon_html}{f'<span class="pill">{esc(durum)}</span>' if durum else ''}</div>
  <div class="name">{esc(ad)}</div>
  <div class="big">
    <div class="pct"><small>%</small>{round(pct)}</div>
    <div class="pl"><b>fiziki ilerleme</b>{esc(asof)}</div>
  </div>
  <div class="prog"><i></i></div>
  <div class="foot">
    <div class="ac">Tahmini açılış<b>{esc(acilis)}</b></div>
    <div class="brand">{f'<img src="{logo_uri}">' if logo_uri else '<b>metrometre</b>'}<span>metrometre.com</span></div>
  </div>
</div>
</body></html>"""


# ───────────────────────── ana akış ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT), help="GeoJSON/CSV/logo klasörü (varsayılan: repo kökü)")
    ap.add_argument("--out", default=str(ROOT / "og"), help="çıktı klasörü")
    ap.add_argument("--only", default="", help="virgülle OBJECTID listesi")
    a = ap.parse_args()

    data = Path(a.data_dir)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    features = json.loads((data / HAT_FILE).read_text(encoding="utf-8"))["features"]
    features = [f for f in features if f.get("geometry")]
    overlay = load_overlay(data / OVERLAY_FILE)
    logo = data / LOGO_FILE
    logo_uri = data_uri(logo, "image/png") if logo.exists() else None
    only = {s.strip() for s in a.only.split(",") if s.strip()}

    # ── Tüm hatların özet bilgisi (Cloudflare Function bağlantı önizlemesi için) ──
    lines, targets = {}, []
    for f in features:
        p = f["properties"]
        oid = str(p.get("OBJECTID"))
        dur = str(p.get("PR_DURUM"))
        ov = overlay.get(oid, {})
        rec = {"kod": str(p.get("KOD") or "").strip(), "ad": str(p.get("AD") or "").strip(), "durum": dur}
        if dur in YAPIM:
            pct = ov.get("pct")
            if pct is None:
                try:
                    pct = float(p.get("PR_ILERLEME"))
                except (TypeError, ValueError):
                    pct = None
            acilis = ov.get("acilis") or p.get("PR_ACILIS")
            rec.update({"pct": round(pct, 2) if pct is not None else None, "ym": ov.get("ym"),
                        "acilis": fmt_tr_date(acilis)})
            if pct is not None and (not only or oid in only):
                targets.append((oid, f, {"pct": pct, "ym": ov.get("ym"), "acilis": acilis}))
        elif dur in {"0", "1"}:
            rec["acilis"] = fmt_tr_date(p.get("ACILIS"))
        else:
            rec["acilis"] = fmt_tr_date(p.get("PR_ACILIS"))
        lines[oid] = rec

    lines_path = out / "lines.json"
    try:
        prev = json.loads(lines_path.read_text(encoding="utf-8")).get("lines", {})
    except Exception:
        prev = {}

    from playwright.sync_api import sync_playwright
    rendered = set()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for oid, f, info in targets:
            page.set_content(card_html(f, info, features, logo_uri), wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(out / f"{oid}.png"))
            # Sürüm: kartın içeriğini belirleyen alanlardan türetilir; içerik
            # değişmedikçe aynı kalır → sosyal ağ önbelleği gereksiz yere bozulmaz.
            key = json.dumps([f["properties"].get("AD"), round(info["pct"]), info.get("ym"),
                              fmt_tr_date(info.get("acilis")), f["properties"].get("PR_DURUM")],
                             ensure_ascii=False)
            lines[oid]["card"] = hashlib.sha1(key.encode()).hexdigest()[:10]
            rendered.add(oid)
            print(f"  ✓ {oid:>4}  {lines[oid]['kod']}  {lines[oid]['ad']}  %{round(info['pct'])}")
        browser.close()

    # --only ile üretilmeyen ama kartı hâlâ duran hatların sürümünü koru
    for oid, rec in lines.items():
        if oid not in rendered and (out / f"{oid}.png").exists() and rec["durum"] in YAPIM:
            if prev.get(oid, {}).get("card"):
                rec["card"] = prev[oid]["card"]
    # Artık yapımda olmayan (veya silinen) hatların eski kartlarını temizle
    for old in out.glob("*.png"):
        if not lines.get(old.stem, {}).get("card"):
            old.unlink()
            print(f"  – eski kart silindi: {old.name}")

    yms = [r.get("ym") for r in lines.values() if r.get("ym")]
    payload = {"veri_donemi": max(yms) if yms else None, "lines": lines}
    lines_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")
    print(f"{len(rendered)} kart üretildi, {len(lines)} hat özeti yazıldı → {out}")


if __name__ == "__main__":
    main()

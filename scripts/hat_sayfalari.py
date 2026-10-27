#!/usr/bin/env python3
"""
metrometre — statik hat sayfası üretici (taslak)

Repodaki verilerden her hat için arama motorlarının okuyabileceği, JS'ye
bağımlı olmayan bir HTML sayfası üretir. Şimdilik şablon incelemesi için
yalnızca istenen hat(lar) üretilir:

    python scripts/hat_sayfalari.py                 # bütün sayfalar + sitemap + robots + 404
    python scripts/hat_sayfalari.py --id 6 --id 61  # yalnız seçilen hatlar (önizleme)

Çıktı (repo kökünde):
  hat-sayfalari/<OBJECTID>.html   hat sayfaları; worker /hat/<id>-<slug> adresinde sunar
  hat-sayfalari/manifest.json     id → slug, lastmod (içerik değişmedikçe tarih korunur)
  hatlar/index.html               /hatlar/ dizini; hatlar/sayfalar.json haritadaki "Hat sayfası" düğmesi için
  hakkinda/index.html             /hakkinda/ — metin scripts/hakkinda_icerik.html'den okunur
  bulten/*.html                   yalnız başlık: ortak başlıkla (site_header) değiştirilir
  sitemap.xml, robots.txt, 404.html

Girdi:
  overlay.csv                      aylık ilerleme / durum / açılış / açıklama
  RAYLI_SISTEM_HAT_LN.geojson      hat geometrisi ve künye alanları
  RAYLI_SISTEM_DURAK_PT.geojson    istasyonlar (sıra ve aktarmalar buradan türetilir)
  og/lines.json                    paylaşım kartı sürümü (og:image)
"""
import argparse, csv, hashlib, html, json, math, re, datetime as dt
from urllib.parse import quote
from zoneinfo import ZoneInfo

BUGUN = dt.datetime.now(ZoneInfo('Europe/Istanbul')).date()
from pathlib import Path

SITE = "https://metrometre.com"
TR_MONTHS = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
             'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']
TR_MONTHS_SHORT = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz',
                   'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara']
DURUM_LABEL = {
    '0': 'Devre dışı', '1': 'İşletmede', '2': 'Test aşamasında', '3': 'İnşa aşamasında',
    '4': 'Beklemede', '5': 'Uygulama projesi tamamlandı', '6': 'Uygulama projesi sürüyor',
    '7': 'Etüt projesi tamamlandı', '8': 'Etüt projesi sürüyor',
}
YAPIM = {'2', '3', '4'}
TUR_LABEL = {'1': 'Banliyö', '2': 'Metro', '3': 'Tramvay', '4': 'Füniküler', '5': 'Teleferik'}

# Kesin hedef tarih ayrı bir sütunda tutulmaz: TahminiAcilis gün düzeyinde girildiğinde
# (2026-12-19 ya da Sheets'in yayımladığı 19.12.2026) sayfa geri sayım gösterir.

# Halk arasında kullanılan adlar (başlık, açıklama ve SSS'de geçer; aramada eşleşme için)
TAKMA_AD = {'61': 'Gebze–Darıca Metrosu', '259': 'Körfezray'}

# Bulunma eki kuralla türetilemeyen iller (index.html IL_AYAR ile aynı)
IL_YER = {'İstanbul': "İstanbul'da", 'Kocaeli': "Kocaeli'nde"}
VARSAYILAN_IL = 'İstanbul'

# Hat kodundan okunur ad (B1 → Marmaray gibi)
KOD_AD = {'B1': 'Marmaray', 'T': 'Tramvay'}

def tire(s):
    """Hat adındaki ayırıcı tireyi uzun tireye çevirir; 'Mescid-i Selam' gibi eklere dokunmaz."""
    return re.sub(r'-(?![a-zçğıöşü]\b)', '–', s or '')

def kod_goster(kod):
    """'M1 (Kocaeli)' → 'M1'. İl bilgisi sayfada ayrıca yazılır."""
    return re.sub(r'\s*\([^)]*\)\s*$', '', kod or '').strip()

AKTARMA_M = 350          # işletmedeki hatla bu mesafe içindeyse "aktarma"
PLANLI_AKTARMA_M = 350   # proje / yapım hattıyla bu mesafe içindeyse "planlanan aktarma"

esc = lambda s: html.escape(str(s if s is not None else ''), quote=True)


# ───────────────────────── yardımcılar ─────────────────────────
def tr_lower(s):
    return s.replace('İ', 'i').replace('I', 'ı').lower()

def slugify(s):
    s = tr_lower(s)
    for a, b in zip('ıışğüöçâîû', 'iisguocaiu'):
        s = s.replace(a, b)
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:60]

def il_yer(il):
    return IL_YER.get(il) or bulunma(il)

def bulunma(ad):
    """Türkçe bulunma eki: İstanbul'da, Kocaeli'de, Ankara'da, Sivas'ta."""
    w = tr_lower(ad)
    son = next((c for c in reversed(w) if c in 'aıoueiöü'), 'a')
    v = 'a' if son in 'aıou' else 'e'
    d = 't' if w[-1] in 'fstkçşhp' else 'd'
    return f"{ad}'{d}{v}"

def ym_long(ym):
    m = re.match(r'^(\d{4})-(\d{2})$', ym or '')
    return f"{TR_MONTHS[int(m[2]) - 1]} {m[1]}" if m else (ym or '')

def ym_short(ym):
    y, m = ym.split('-')
    return f"{TR_MONTHS_SHORT[int(m) - 1]}"

def date_long(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.day} {TR_MONTHS[d.month - 1]} {d.year}"

def parse_pct(raw):
    s = (raw or '').strip()
    if not s or s.startswith('('):
        return None
    try:
        return max(0.0, min(100.0, float(s.replace('%', '').replace(' ', '').replace(',', '.'))))
    except ValueError:
        return None

def fmt_pct(v):
    r = round(v, 1)
    return (f"{r:.0f}" if r == int(r) else f"{r:.1f}").replace('.', ',')

def fmt_km(v):
    return f"{v:.1f}".replace('.', ',')

def sentences(text, n):
    """İlk n cümle. '60. Yıl' gibi sayıdan sonra gelen noktada bölmez."""
    parts = re.split(r'(?<=[a-zçğıöşü\)"’\'])\.\s+(?=[A-ZÇĞİÖŞÜ0-9])', text.strip())
    out = '. '.join(parts[:n]).strip()
    return out if out.endswith('.') else out + '.'

def darken_for_contrast(hex_, target=3.0):
    """Açık hat renklerini (M12 #C9D200 gibi) beyaz zeminde grafik çizgisi olarak
    okunur kılmak için aynı tonu koyulaştırır (WCAG grafik kontrastı ≥ 3:1)."""
    def lum(r, g, b):
        def ch(c):
            c /= 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    k = 1.0
    while k > 0.2:
        rr, gg, bb = (round(c * k) for c in (r, g, b))
        if 1.05 / (lum(rr, gg, bb) + 0.05) >= target:
            return f"#{rr:02X}{gg:02X}{bb:02X}"
        k -= 0.02
    return hex_

def icon(url, kod, color, size):
    fb = f'<span class="icon-fb" hidden style="background:{esc(color or "#888")}">{esc(kod)}</span>'
    if not url:
        return f'<span class="line-icon" style="--s:{size}px">{fb.replace(" hidden", "")}</span>'
    return (f'<span class="line-icon" style="--s:{size}px"><img src="{esc(url)}" alt="{esc(kod)}" '
            f'width="{size}" height="{size}" loading="lazy" '
            f'onerror="this.style.display=\'none\';this.nextElementSibling.hidden=false">{fb}</span>')


# ───────────────────────── ortak başlık ─────────────────────────
# Bütün sayfalarda aynı başlık: logo + slogan, sağda sabit sırayla Harita · Hatlar · Bülten · Hakkında.
# Bulunulan bölüm dolu (kırmızı) görünür; dar ekranda hepsi simgeli yuvarlak düğmeye döner.
# index.html (harita) aynı işaretlemeyi elle taşır; burayı değiştirirseniz orayı da güncelleyin.
NAV_IKON = {
    'harita':   '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/>',
    'hatlar':   '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1.2"/><circle cx="4.5" cy="12" r="1.2"/><circle cx="4.5" cy="18" r="1.2"/>',
    'bulten':   '<path d="M4 5h13v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><path d="M17 9h3v10a2 2 0 0 1-2 2"/><path d="M8 9h5M8 13h5M8 17h3"/>',
    'hakkinda': '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.5v.01"/>',
}
NAV = [('harita', '/', 'Harita', 'Harita: bütün hatlar'),
       ('hatlar', '/hatlar/', 'Hatlar', 'Bütün hatlar: ilerleme ve tahmini açılış'),
       ('bulten', '/bulten/', 'Bülten', 'Aylık bülten'),
       ('hakkinda', '/hakkinda/', 'Hakkında', 'metrometre hakkında')]

def site_header(aktif=None):
    items = []
    for key, href, lbl, title in NAV:
        cur = ' aria-current="page"' if key == aktif else ''
        cls = 'nav-btn icon' if key == 'hakkinda' else 'nav-btn'
        items.append(f'<a class="{cls}" href="{href}"{cur} title="{title}" aria-label="{lbl}">'
                     f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
                     f'stroke-linejoin="round" aria-hidden="true">{NAV_IKON[key]}</svg><span class="lbl">{lbl}</span></a>')
    return ('<header id="header">\n'
            '  <a id="logo-link" href="/" aria-label="metrometre ana sayfa">\n'
            '    <img id="logo-img" src="/metrometre_logotype.png" alt="metrometre" '
            'onerror="this.style.display=\'none\';document.getElementById(\'logo-fallback\').style.display=\'block\'">\n'
            '    <div id="logo-fallback"><span>metro</span>metre</div>\n'
            '    <span id="logo-tagline">raylı sistem projeleri<br>takip paneli</span>\n'
            '  </a>\n'
            '  <nav class="site-nav" aria-label="Site bölümleri">\n    ' + '\n    '.join(items) + '\n  </nav>\n'
            '</header>')

HEADER_CSS = """/* ── Ortak başlık (scripts/hat_sayfalari.py · site_header) ── */
#header { position:fixed; top:0; left:0; right:0; height:var(--header-h); background:var(--surface);
  border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between;
  gap:10px; padding:0 20px; z-index:1000; }
#logo-link { display:flex; align-items:center; gap:12px; text-decoration:none; height:36px; min-width:0; }
#logo-link:hover { text-decoration:none; }
#logo-img { height:26px; width:auto; display:block; flex-shrink:0; }
#logo-fallback { display:none; font-size:17px; font-weight:700; color:var(--text); letter-spacing:-0.03em; }
#logo-fallback span { font-weight:400; color:var(--text-muted); }
#logo-tagline { font-size:10px; font-weight:500; color:var(--text-dim); letter-spacing:.02em; line-height:1.35;
  padding-left:12px; border-left:1px solid var(--border); }
.site-nav { display:flex; align-items:center; gap:8px; flex-shrink:0; }
.nav-btn { position:relative; display:inline-flex; align-items:center; gap:7px; height:32px; padding:0 13px 0 11px;
  border:1px solid var(--border-2); border-radius:99px; background:transparent; color:var(--text);
  font-family:var(--font); font-size:12px; font-weight:600; letter-spacing:-0.01em; line-height:1; white-space:nowrap;
  text-decoration:none; flex-shrink:0; transition:border-color .15s, color .15s, background .15s; }
.nav-btn svg { width:15px; height:15px; flex-shrink:0; }
.nav-btn:hover { border-color:var(--accent); color:var(--accent); background:#FEF2F2; text-decoration:none; }
.nav-btn[aria-current="page"] { background:var(--accent); border-color:var(--accent); color:#fff; }
.nav-btn[aria-current="page"]:hover { background:#9C1F18; border-color:#9C1F18; color:#fff; }
.nav-btn:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.nav-btn.icon { width:32px; padding:0; justify-content:center; }
.nav-btn.icon .lbl { display:none; }
.nav-dot { position:absolute; top:-2px; right:-2px; width:9px; height:9px; border-radius:50%;
  background:var(--accent); border:2px solid var(--surface); }
.nav-dot[hidden] { display:none; }
@media (max-width:768px) {
  #header { padding:0 12px; }
  #logo-tagline { display:none; }
  #logo-link { flex-shrink:1; }
  #logo-img { height:auto; width:min(184px, calc(100vw - 212px)); }
  .site-nav { gap:6px; }
  .nav-btn, .nav-btn.icon { width:40px; height:40px; padding:0; justify-content:center; gap:0; }
  .nav-btn .lbl { display:none; }
  .nav-btn svg { width:18px; height:18px; }
}"""

def bulten_basliklari(repo):
    """Elle hazırlanan bülten sayfalarının başlığını ortak başlıkla değiştirir (yeni sayılar dahil)."""
    for f in sorted((repo / 'bulten').glob('*.html')):
        s = f.read_text(encoding='utf-8')
        a, b = s.find('<header id="header">'), s.find('</header>')
        if a < 0 or b < 0:
            continue
        yeni = s[:a] + site_header('bulten') + s[b + len('</header>'):]
        stil = f'<style id="site-header">\n{HEADER_CSS}\n</style>\n'
        yeni = re.sub(r'<style id="site-header">.*?</style>\n', '', yeni, flags=re.S)
        yeni = yeni.replace('</head>', stil + '</head>', 1)
        if yeni != s:
            f.write_text(yeni, encoding='utf-8')


# ───────────────────────── geometri ─────────────────────────
KX = 111320 * math.cos(math.radians(41))
KY = 110540

def dist_m(a, b):
    return math.hypot((a[0] - b[0]) * KX, (a[1] - b[1]) * KY)

def line_parts(geom):
    if geom['type'] == 'LineString':
        return [geom['coordinates']]
    if geom['type'] == 'MultiLineString':
        return geom['coordinates']
    return []

def project_on(parts, p):
    """Noktanın hat boyunca konumu (m) ve hatta uzaklığı (m)."""
    best, base = (1e18, 0.0), 0.0
    for line in parts:
        for i in range(len(line) - 1):
            a, b = line[i], line[i + 1]
            ax, ay, bx, by = a[0] * KX, a[1] * KY, b[0] * KX, b[1] * KY
            px, py = p[0] * KX, p[1] * KY
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2)) if L2 else 0.0
            d = math.hypot(ax + t * dx - px, ay + t * dy - py)
            seg = math.sqrt(L2)
            if d < best[0]:
                best = (d, base + t * seg)
            base += seg
    return best

def simplify(pts, tol):
    """Douglas–Peucker (piksel uzayında)."""
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1e-9
    idx, dmax = 0, 0.0
    for i in range(1, len(pts) - 1):
        d = abs(dy * pts[i][0] - dx * pts[i][1] + b[0] * a[1] - b[1] * a[0]) / L
        if d > dmax:
            idx, dmax = i, d
    if dmax <= tol:
        return [a, b]
    return simplify(pts[:idx + 1], tol)[:-1] + simplify(pts[idx:], tol)


# ───────────────────────── veri ─────────────────────────
def load(repo):
    hat = json.loads((repo / 'RAYLI_SISTEM_HAT_LN.geojson').read_text(encoding='utf-8'))['features']
    durak = json.loads((repo / 'RAYLI_SISTEM_DURAK_PT.geojson').read_text(encoding='utf-8'))['features']
    with open(repo / 'overlay.csv', encoding='utf-8') as f:
        overlay = list(csv.DictReader(f))
    try:
        cards = json.loads((repo / 'og' / 'lines.json').read_text(encoding='utf-8'))['lines']
    except Exception:
        cards = {}
    return hat, durak, overlay, cards

BELIRSIZ = {'', '-', '—', 'belirsiz', 'yok', 'açıklanmış tarih yok'}

def known(v):
    v = (v or '').strip()
    return bool(v) and not v.startswith('(') and tr_lower(v) not in BELIRSIZ

def series_for(overlay, oid):
    rows = sorted((r for r in overlay if r['OBJECTID'].strip() == oid), key=lambda r: r['YilAy'])
    months = []
    for r in rows:
        months.append({
            'ym': r['YilAy'].strip(),
            'pct': parse_pct(r.get('Ilerleme')),
            'pending': (r.get('Ilerleme') or '').strip().startswith('('),
            'src': (r.get('Kaynak') or '').strip(),
            'durum': (r.get('Durum') or '').strip(),
            'acilis': norm_tarih(r.get('TahminiAcilis')),
            'not': (r.get('Aciklama') or '').strip(),
        })
    # Alan bazlı birikim: her alan için en son dolu değer
    latest = {}
    for m in months:
        for k in ('src', 'durum', 'not'):
            if m[k] and not m[k].startswith('('):
                latest[k] = m[k]
        if m['acilis'] and not m['acilis'].startswith('('):
            latest['acilis'] = m['acilis'] if known(m['acilis']) else ''
        if m['pct'] is not None:
            latest['pct'], latest['pct_ym'], latest['pct_src'] = m['pct'], m['ym'], m['src']
    return months, latest

def acilis_runs(months):
    runs = []
    for m in months:
        v = m['acilis']
        if not known(v):
            continue
        key = v[:7] if re.match(r'^\d{4}-\d{2}', v) else v
        if runs and runs[-1]['key'] == key:
            runs[-1]['end'], runs[-1]['val'] = m['ym'], v   # aynı ay, daha kesin değer
        else:
            runs.append({'val': v, 'key': key, 'start': m['ym'], 'end': m['ym']})
    return runs

def norm_tarih(v):
    """TahminiAcilis değerini ISO'ya çevirir: 2026-12, 2026-12-19, 19.12.2026, 12.2026."""
    s = (v or '').strip()
    m = re.match(r'^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$', s)
    if m:
        return f"{m[1]}-{int(m[2]):02d}" + (f"-{int(m[3]):02d}" if m[3] else '')
    m = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$', s)
    if m:
        return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    m = re.match(r'^(\d{1,2})\.(\d{4})$', s)
    if m:
        return f"{m[2]}-{int(m[1]):02d}"
    return s

def acilis_label(v):
    v = norm_tarih(v)
    if re.match(r'^\d{4}-\d{2}-\d{2}$', v or ''):
        return date_long(v)
    return ym_long(v) if re.match(r'^\d{4}-\d{2}$', v or '') else (v or '')


# ───────────────────────── bölümler ─────────────────────────
def build_chart(months, color, note=''):
    vals = [m for m in months if m['pct'] is not None]
    if not vals:
        return '<p class="chart-note">Bu hat için aylık ilerleme verisi bulunmamaktadır.</p>'
    if len(vals) == 1:
        v = vals[0]
        return (f'<div class="one-pt"><div class="op-bar"><i style="width:{v["pct"]:.1f}%;background:{darken_for_contrast(color)}"></i></div>'
                f'<div class="op-txt"><b>%{fmt_pct(v["pct"])}</b> · {esc(ym_long(v["ym"]))}</div></div>'
                f'<p class="chart-note">Hat {esc(ym_long(v["ym"]))} itibarıyla izlenmektedir; aylık seri sonraki '
                f'güncellemelerle oluşacaktır. {note}.</p>')
    return (chart_svg(months, color, 640, 168, 'c-desk') + chart_svg(months, color, 360, 150, 'c-mob')
            + f'<div class="chart-foot"><span class="chart-note">{note}</span>{chart_table(months)}</div>')


def chart_svg(months, color, W, H, cls):
    pts = [m for m in months if m['pct'] is not None]
    stroke = darken_for_contrast(color)
    ml, mr, mt, mb = (40, 56, 14, 26) if W > 500 else (34, 40, 16, 26)
    lo = min(p['pct'] for p in pts)
    y0 = max(0, math.floor((lo - 3) / 5) * 5)
    if 100 - y0 > 40:            # geniş aralıkta 10'luk adım, en fazla 5 çizgi
        y0 = max(0, math.floor((lo - 5) / 10) * 10)
    y1 = 100
    n = len(months)
    xs = lambda i: ml + (W - ml - mr) * (i / max(1, n - 1))
    ys = lambda v: mt + (H - mt - mb) * (1 - (v - y0) / (y1 - y0))
    g = []
    step = 5 if y1 - y0 <= 20 else 10 if y1 - y0 <= 50 else 20
    for v in range(y0, y1 + 1, step):
        y = ys(v)
        g.append(f'<line x1="{ml}" x2="{W - mr}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                 f'<text x="{ml - 8}" y="{y + 3.5:.1f}" class="tick" text-anchor="end">%{v}</text>')
    for i, m in enumerate(months):
        lbl = ym_short(m['ym']) + (f" {m['ym'][2:4] if W < 500 else m['ym'][:4]}" if i == 0 or m['ym'].endswith('-01') else '')
        if W < 500 and i == 0: lbl = ym_short(m['ym'])
        g.append(f'<text x="{xs(i):.1f}" y="{H - 9}" class="tick" text-anchor="middle">{esc(lbl)}</text>')
    idx = [i for i, m in enumerate(months) if m['pct'] is not None]
    d = ' '.join(f"{'M' if k == 0 else 'L'}{xs(i):.1f},{ys(months[i]['pct']):.1f}" for k, i in enumerate(idx))
    g.append(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    for i in idx:
        g.append(f'<circle cx="{xs(i):.1f}" cy="{ys(months[i]["pct"]):.1f}" r="4.5" fill="{stroke}" stroke="#fff" stroke-width="2"/>')
    last = idx[-1]
    pend_after = any(m['pending'] for m in months[last + 1:])
    if pend_after:   # sağda "bekleniyor" işareti var → değer etiketi noktanın altına
        g.append(f'<text x="{xs(last):.1f}" y="{ys(months[last]["pct"]) + 20:.1f}" class="endlbl" text-anchor="middle">%{fmt_pct(months[last]["pct"])}</text>')
    else:
        g.append(f'<text x="{xs(last) + 10:.1f}" y="{ys(months[last]["pct"]) + 4:.1f}" class="endlbl">%{fmt_pct(months[last]["pct"])}</text>')
    # Sonda verisi beklenen ay(lar)
    for i, m in enumerate(months):
        if i > last and m['pending']:
            y = ys(months[last]['pct'])
            g.append(f'<circle cx="{xs(i):.1f}" cy="{y:.1f}" r="4.5" fill="#fff" stroke="#B5B5B5" stroke-width="1.5"/>'
                     f'<text x="{xs(i):.1f}" y="{y - 12:.1f}" class="tick" text-anchor="middle">bekleniyor</text>')
    # Hover alanları (işaretçiden geniş)
    colw = (W - ml - mr) / max(1, n - 1)
    for i, m in enumerate(months):
        val = f"%{fmt_pct(m['pct'])}" if m['pct'] is not None else 'Veri bekleniyor'
        g.append(f'<rect class="hit" x="{xs(i) - colw / 2:.1f}" y="{mt}" width="{colw:.1f}" height="{H - mt - mb}" '
                 f'data-ay="{esc(ym_long(m["ym"]))}" data-val="{esc(val)}" data-src="{esc(m["src"])}" '
                 f'data-x="{xs(i):.1f}" data-y="{ys(m["pct"]) if m["pct"] is not None else ys(months[last]["pct"]):.1f}"/>')
    g.append(f'<line class="xhair" x1="0" x2="0" y1="{mt}" y2="{H - mb}" visibility="hidden"/>')
    svg = (f'<svg class="chart {cls}" viewBox="0 0 {W} {H}" role="img" '
           f'aria-label="Aylık fiziki ilerleme grafiği; ayrıntılar aşağıdaki tabloda">{"".join(g)}</svg>')
    return f'<div class="chart-wrap {cls}">{svg}<div class="tip" role="status" hidden></div></div>'


def chart_table(months):
    rows = ''.join(
        f"<tr><td>{esc(ym_long(m['ym']))}</td><td>{'%' + fmt_pct(m['pct']) if m['pct'] is not None else 'bekleniyor'}</td>"
        f"<td>{esc(m['src'])}</td></tr>" for m in reversed(months))
    table = (f'<details class="tbl"><summary>Tablo</summary><table><caption>Değerler her ayın sonundaki fiziki ilerlemeyi göstermektedir.</caption><thead><tr><th>Ay</th>'
             f'<th>Fiziki ilerleme</th><th>Kaynak</th></tr></thead><tbody>{rows}</tbody></table></details>')
    return table


def build_map(feat, stations, hat_feats, durak_feats):
    parts = line_parts(feat['geometry'])
    allc = [c for p in parts for c in p]
    minx, maxx = min(c[0] for c in allc), max(c[0] for c in allc)
    miny, maxy = min(c[1] for c in allc), max(c[1] for c in allc)
    padx, pady = (maxx - minx) * 0.18 + 0.004, (maxy - miny) * 0.08 + 0.003
    minx, maxx, miny, maxy = minx - padx, maxx + padx, miny - pady, maxy + pady
    wm, hm = (maxx - minx) * KX, (maxy - miny) * KY
    lo, hi = 0.6, 1.45                      # yükseklik / genişlik
    if hm / wm < lo:                        # yatay hat: dikeyde boşluk aç
        ek = (wm * lo - hm) / 2 / KY
        miny, maxy = miny - ek, maxy + ek
    elif hm / wm > hi:                      # dikey hat: yatayda boşluk aç
        ek = (hm / hi - wm) / 2 / KX
        minx, maxx = minx - ek, maxx + ek
    wm, hm = (maxx - minx) * KX, (maxy - miny) * KY
    W = 320
    H = round(W * hm / wm)
    P = lambda c: ((c[0] - minx) / (maxx - minx) * W, (1 - (c[1] - miny) / (maxy - miny)) * H)
    inside = lambda c: minx <= c[0] <= maxx and miny <= c[1] <= maxy

    def path(coords):
        px = simplify([P(c) for c in coords], 0.6)
        return 'M' + ' L'.join(f"{x:.1f},{y:.1f}" for x, y in px)

    g = [f'<rect width="{W}" height="{H}" fill="#F7F7F7"/>']
    # Bağlam: harita alanına giren işletmedeki diğer hatlar
    ctx_lbl, placed = {}, []
    for f in hat_feats:
        p = f['properties']
        if f is feat or str(p.get('PR_DURUM')) != '1':
            continue
        for part in line_parts(f['geometry']):
            if any(inside(c) for c in part):
                g.append(f'<path d="{path(part)}" class="ctx"/>')
    me = feat['properties']
    g.append(f'<path d="{path(parts[0]) if len(parts) == 1 else " ".join(path(x) for x in parts)}" '
             f'fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>')
    g.append(f'<path d="{" ".join(path(x) for x in parts)}" fill="none" stroke="{esc(me["RENK_HEX"])}" '
             f'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>')
    # Aktarma yapılan işletmedeki hatların istasyonlarına küçük etiket
    for s in stations:
        for t in s['aktarma']:
            if t['planli']:
                continue
            x, y = P(t['coord'])
            sx, sy = P(s['coord'])
            key = t['kod']
            if key in ctx_lbl:
                continue
            ctx_lbl[key] = 1
            # Etiketi hattın dış tarafına koy: M12 güzergâhının yerel yönüne dik, istasyondan uzağa
            j = stations.index(s)
            a = P(stations[max(0, j - 1)]['coord']); b = P(stations[min(len(stations) - 1, j + 1)]['coord'])
            tx, ty = b[0] - a[0], b[1] - a[1]
            L = math.hypot(tx, ty) or 1
            nx, ny = -ty / L, tx / L
            if (x - sx) * nx + (y - sy) * ny < 0 or math.hypot(x - sx, y - sy) < 4:
                nx, ny = (-nx, -ny) if (x - sx) * nx + (y - sy) * ny < 0 else (nx, ny)
            if math.hypot(x - sx, y - sy) > 12:
                g.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#8A8A8A" stroke="#fff" stroke-width="1.5"/>')
            lx, ly = sx + nx * 17, sy + ny * 17
            anchor = 'start' if nx > 0.3 else 'end' if nx < -0.3 else 'middle'
            txt = KOD_AD.get(key, kod_goster(key))
            tw = 6.2 * len(txt)
            x0 = lx if anchor == 'start' else lx - tw if anchor == 'end' else lx - tw / 2
            ty = ly + 3.5 + (ny * 4 if abs(nx) <= 0.3 else 0)
            kutu = (x0 - 2, ty - 10, x0 + tw + 2, ty + 3)
            if any(not (kutu[2] < b[0] or kutu[0] > b[2] or kutu[3] < b[1] or kutu[1] > b[3]) for b in placed):
                continue        # başka bir etiketle çakışıyorsa yazma
            placed.append(kutu)
            g.append(f'<text x="{lx:.1f}" y="{ty:.1f}" class="ctx-lbl" text-anchor="{anchor}">{esc(txt)}</text>')
    pts = [P(s['coord']) for s in stations]
    min_gap = min((math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(pts, pts[1:])), default=99)
    numbered = min_gap >= 18
    ink = esc(darken_for_contrast(me["RENK_HEX"]))
    for s, (x, y) in zip(stations, pts):
        if numbered:
            g.append(f'<g class="st" data-n="{s["n"]}"><circle cx="{x:.1f}" cy="{y:.1f}" r="8.5" fill="#fff" '
                     f'stroke="{ink}" stroke-width="2"/>'
                     f'<text x="{x:.1f}" y="{y + 3.6:.1f}" text-anchor="middle" class="st-n">{s["n"]}</text></g>')
        else:   # sık istasyonlu hat: numaralar listede, haritada yalnız nokta (uçlar numaralı)
            uc = s['n'] in (1, len(stations))
            g.append(f'<g class="st" data-n="{s["n"]}"><circle cx="{x:.1f}" cy="{y:.1f}" r="{7.5 if uc else 4}" '
                     f'fill="#fff" stroke="{ink}" stroke-width="{2 if uc else 1.8}"/>'
                     + (f'<text x="{x:.1f}" y="{y + 3.3:.1f}" text-anchor="middle" class="st-n">{s["n"]}</text>' if uc else '')
                     + '</g>')
    return (f'<svg class="mini-map" viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="{esc(me["KOD"])} güzergâhı ve numaralı istasyonları">{"".join(g)}</svg>')


def find_stations(feat, durak_feats, hat_feats):
    p = feat['properties']
    parts = line_parts(feat['geometry'])
    mine = []
    for d in durak_feats:
        dp = d['properties']
        if dp.get('KOD') != p['KOD']:
            continue
        off, along = project_on(parts, d['geometry']['coordinates'])
        if off < 150:   # aynı kodlu başka etapların istasyonlarını dışarıda bırak
            mine.append((along, d))
    mine.sort(key=lambda x: x[0])
    # aynı adlı ve birbirine yakın istasyonlar (aynı kodlu farklı projelerin ortak durağı) tek sayılır
    tekil = []
    for along, d in mine:
        if any(e['properties']['AD'] == d['properties']['AD'] and
               dist_m(e['geometry']['coordinates'], d['geometry']['coordinates']) < 300 for _, e in tekil):
            continue
        tekil.append((along, d))
    mine = tekil
    hat_by = {}
    for f in hat_feats:
        hat_by.setdefault(f['properties']['KOD'], []).append(f['properties'])
    out = []
    for n, (along, d) in enumerate(mine, 1):
        c = d['geometry']['coordinates']
        akt, seen = [], set()
        for o in durak_feats:
            op = o['properties']
            if op.get('KOD') == p['KOD']:
                continue
            m = dist_m(c, o['geometry']['coordinates'])
            durum = str(op.get('PR_DURUM'))
            if durum == '0':
                continue
            planli = durum != '1'
            if m > (PLANLI_AKTARMA_M if planli else AKTARMA_M):
                continue
            key = (op['KOD'], planli)
            if key in seen:
                continue
            seen.add(key)
            akt.append({'kod': op['KOD'], 'istasyon': op['AD'], 'm': round(m), 'planli': planli,
                        'durum': durum, 'ikon': op.get('IKON_URL'), 'renk': op.get('RENK_HEX'),
                        'coord': o['geometry']['coordinates']})
        for a in akt:
            a['_st'] = d['properties']['AD']
        akt.sort(key=lambda a: (a['planli'], a['m']))
        out.append({'n': n, 'ad': d['properties']['AD'], 'coord': c, 'km': along / 1000, 'aktarma': akt})
    return out


def akt_phrase(a):
    ad = KOD_AD.get(a['kod'], a['kod'])
    if a['istasyon'] and a['m'] > 120 and tr_lower(a['istasyon']) not in tr_lower(a.get('_st', '')):
        return f"{ad} ({a['istasyon']}, ~{round(a['m'], -1):.0f} m yürüme)"
    return ad


# ───────────────────────── künye yan kutusu ─────────────────────────
ASAMALAR = [('Etüt', {'8', '7'}), ('Uygulama projesi', {'6', '5'}), ('İnşa', {'3', '4'}),
            ('Test', {'2'}), ('İşletme', {'1'})]

def months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)

def hero_side(p, durum_kod, durum, hedef, latest, months, runs):
    """Geri sayımın yerine, açılış bilgisinin kesinliğine göre değişen kutu:
       A) kesin hedef tarih    → gün geri sayımı
       B) ay / yıl düzeyinde   → tahmini ay + "yaklaşık N ay", varsa önceki tahmin
       C) tarih yok, yapımda   → son aylardaki ilerleme hızı (puan)
       D) işletmede            → hizmet süresi
       E) proje aşamasında     → aşama göstergesi"""
    today = BUGUN
    box = lambda cls, head, num, unit, foot, extra='': (
        f'<aside class="side {cls}"{extra}><div class="cd-head">{head}</div>'
        f'<div class="cd-num"><b>{num}</b><span>{unit}</span></div><div class="cd-foot">{foot}</div></aside>')

    # A
    if hedef:
        days = (dt.date.fromisoformat(hedef[0]) - today).days
        if days > 0:
            return box('countdown', 'Açılış hedefine', f'<span id="cd-days"><!--v-->{days}<!--/v--></span>', 'gün',
                       f"Açıklanan hedef tarih <strong>{esc(date_long(hedef[0]))}</strong>",
                       f' data-hedef="{hedef[0]}"')
    acilis = latest.get('acilis', '')
    # B
    m = re.match(r'^(\d{4})-(\d{2})$', acilis or '')
    if durum_kod in YAPIM and m:
        y, mo = int(m[1]), int(m[2])
        kalan = months_between(today, dt.date(y, mo, 1))
        prev = runs[-2]['val'] if len(runs) > 1 else None
        foot = (f'Önceki tahmin <s>{esc(acilis_label(prev))}</s>' if prev
                else 'Kesin tarih açıklanmadı')
        if kalan > 0:
            foot = f'<!--v-->Yaklaşık {kalan} ay sonra<!--/v--> · {foot}'
        return box('month' + (' moved' if prev else ''), 'Tahmini açılış', TR_MONTHS[mo - 1], str(y), foot)
    # C
    if durum_kod in YAPIM:
        vals = [x for x in months if x['pct'] is not None]
        if len(vals) >= 2:
            cur = vals[-1]['pct']
            k = len(vals) - 1
            while k > 0 and abs(vals[k - 1]['pct'] - cur) < 0.05:
                k -= 1
            ay0 = lambda x: dt.date.fromisoformat(x['ym'] + '-01')
            sabit = months_between(ay0(vals[k]), ay0(vals[-1]))
            if sabit >= 3:      # en az 3 aydır yerinde sayıyor
                arti = '+' if k == 0 else ''   # kayıtların başından beri aynı → "en az"
                return box('pace flat', 'İlerleme değişmedi', f'{sabit}{arti}', 'aydır',
                           f'Son değer %{fmt_pct(cur)} · açılış tarihi açıklanmadı')
            w = vals[-7:]
            n = months_between(ay0(w[0]), ay0(w[-1]))
            delta = w[-1]['pct'] - w[0]['pct']
            return box('pace', f'Son {n} ayda ilerleme', ('+' if delta > 0 else '') + fmt_pct(delta), 'puan',
                       f'Ayda ortalama {fmt_pct(delta / n)} puan · açılış tarihi açıklanmadı')
        return box('pace flat', 'Açılış tarihi', '—', '', 'Kurumlarca açıklanmış bir açılış tarihi bulunmamaktadır')
    # D
    if durum_kod == '1' and re.match(r'^\d{4}-\d{2}-\d{2}', str(p.get('ACILIS') or '')):
        a = dt.date.fromisoformat(str(p['ACILIS'])[:10])
        ay = months_between(a, today)
        num, unit = (str(ay // 12), 'yıldır') if ay >= 24 else (str(max(ay, 0)), 'aydır')
        return box('service', 'Hizmette', f'<!--v-->{num}<!--/v-->', unit, f'Açılış: <strong>{esc(date_long(a.isoformat()))}</strong>')
    # E
    cur = next((i for i, (_, k) in enumerate(ASAMALAR) if durum_kod in k), None)
    if cur is not None:
        steps = ''.join(f'<li class="{"done" if i < cur else "cur" if i == cur else ""}">{esc(ad)}</li>'
                        for i, (ad, _) in enumerate(ASAMALAR))
        return (f'<aside class="side phase"><div class="cd-head">Proje aşaması</div><ol class="steps">{steps}</ol>'
                f'<div class="cd-foot">{esc(durum)}</div></aside>')
    return ''


# ───────────────────────── sayfa ─────────────────────────
ASAMA_YAPIM, ASAMA_PROJE = YAPIM, {'5', '6', '7', '8'}

def asama_of(durum_kod):
    return 'yapim' if durum_kod in ASAMA_YAPIM else 'proje' if durum_kod in ASAMA_PROJE else 'isletme'

def build_page(oid, hat, durak, overlay, cards, updated):
    feat = next(f for f in hat if str(f['properties']['OBJECTID']) == oid)
    p = feat['properties']
    kod_ham, ad, color = p['KOD'], p['AD'], p.get('RENK_HEX') or '#888888'
    kod = kod_goster(kod_ham)
    kisa_ad = tire(re.sub(r'\s+(Metro|Tramvay)\s+Hattı$', '', ad))
    adli = 'Hattı' in kisa_ad or 'Uzatması' in kisa_ad   # bu adlar "Metrosu" eki almaz
    on = f"{kod} " if len(kod) > 1 else ''   # numarasız proje kodları (M, T, F) başlıkta yazılmaz
    kisa_baslik = f"{on}{kisa_ad}"
    kod_ref = kod if on else kisa_ad
    tur = TUR_LABEL.get(str(p.get('TUR')), 'Raylı sistem')
    tur_l = tr_lower(tur)
    il = (p.get('IL') or '').split(',')[0].strip() or VARSAYILAN_IL
    il_hash = '' if il == VARSAYILAN_IL else f"il={slugify(il)}&"
    takma = TAKMA_AD.get(oid, '')
    slug = slugify(f"{kod_ham} {ad}")   # sitedeki paylaşım bağlantısıyla aynı
    url = f"{SITE}/hat/{oid}-{slug}"
    months, latest = series_for(overlay, oid)
    runs = acilis_runs(months)
    stations = find_stations(feat, durak, hat)
    durum_kod = str(p.get('PR_DURUM'))
    durum = latest.get('durum') or DURUM_LABEL.get(durum_kod, '')
    kurum = p.get('PR_KURUM') or ''
    pct, pct_ym = latest.get('pct'), latest.get('pct_ym')
    asama = asama_of(str(p.get('PR_DURUM')))
    if not latest.get('acilis') and known(p.get('PR_ACILIS')) and asama != 'isletme':
        latest['acilis'] = norm_tarih(p['PR_ACILIS'])      # proje hatları: geojson'daki öngörü
    acilis = acilis_label(latest.get('acilis', ''))
    acilis_tarihi = str(p.get('ACILIS') or '')[:10] if re.match(r'^\d{4}-\d{2}-\d{2}', str(p.get('ACILIS') or '')) else ''
    hedef = ((latest['acilis'], kurum) if re.match(r'^\d{4}-\d{2}-\d{2}$', latest.get('acilis', ''))
             else None)
    data_ym = months[-1]['ym'] if months else ''
    stroke = darken_for_contrast(color)
    card = cards.get(oid, {}).get('card')
    og_img = f"{SITE}/og/{oid}.png" + (f"?v={card}" if card else '') if card else f"{SITE}/og-image.png"
    if adli:
        baslik_kisa = kisa_baslik
    else:
        baslik_kisa = f"{kisa_baslik} metrosu" if tur == 'Metro' else f"{kisa_baslik} {tur_l} hattı"
    if il != VARSAYILAN_IL and il not in baslik_kisa:
        baslik_kisa = f"{il} {baslik_kisa}"
    h1_txt = kisa_baslik if adli else f"{kisa_baslik} {('Metrosu' if tur == 'Metro' else tur + ' Hattı')}"

    # ── Özet metinler (soneki gerektirmeyen kalıplarla; her hat için aynı üretilebilir)
    ozet = []
    if pct is not None:
        ozet.append(f"{ym_long(pct_ym)} sonu itibarıyla fiziki ilerleme yüzde {fmt_pct(pct)} seviyesindedir.")
    if latest.get('not'):
        ozet.append(sentences(latest['not'], 2))
    dek = ' '.join(ozet[1:]) if len(ozet) > 1 else ' '.join(ozet)
    if not latest.get('not') and p.get('PR_ACIKLAMA'):
        dek = p['PR_ACIKLAMA'].strip()          # izleme verisi olmayan hatlar: geojson açıklaması

    # Açılış tarihi seyri
    if len(runs) > 1:
        ilk, son = runs[0], runs[-1]
        seyir = (f"{ym_long(ilk['start'])} itibarıyla {acilis_label(ilk['val'])} olarak öngörülen tahmini açılış, "
                 f"{ym_long(son['start'])} güncellemesiyle {acilis_label(son['val'])} olarak revize edilmiştir.")
    elif runs:
        seyir = (f"Tahmini açılış, izlenen dönem boyunca ({ym_long(runs[0]['start'])}–{ym_long(runs[0]['end'])}) "
                 f"{acilis_label(runs[0]['val'])} olarak korunmaktadır.")
    else:
        seyir = ''

    hedef_cumle = (f" Kurumlarca açıklanan hedef açılış tarihi {date_long(hedef[0])} olarak "
                   f"belirtilmektedir." if hedef else '')

    # ── SSS
    st_names = ', '.join(s['ad'] for s in stations)
    akt_now = [(s, a) for s in stations for a in s['aktarma'] if not a['planli']]
    akt_plan = [(s, a) for s in stations for a in s['aktarma'] if a['planli']]
    akt_cumle = ''
    if akt_now:
        akt_cumle = ('Hat; ' + ', '.join(f"{bulunma(s['ad'])} {akt_phrase(a)}" for s, a in akt_now)
                     + (' ile aktarma sağlamaktadır.' if asama == 'isletme' else ' ile aktarma sağlayacaktır.'))
    if akt_plan:
        grp = {}
        for s, a in akt_plan:
            grp.setdefault(s['ad'], []).append(KOD_AD.get(a['kod'], a['kod']))
        akt_cumle += (' Henüz işletmede olmayan hatlarla planlanan aktarma noktaları: '
                      + ', '.join(f"{k} ({', '.join(v)})" for k, v in grp.items()) + '.')
    ad_soru = takma or (kisa_baslik + ('' if adli else ' ' + tur_l + ' hattı'))
    if asama == 'isletme':
        s1 = (ad_soru + ' ne zaman açıldı?',
              f"Hat {date_long(acilis_tarihi)} tarihinde hizmete açılmıştır." if acilis_tarihi else f"Hat {tr_lower(durum)} durumdadır.")
    elif asama == 'proje':
        s1 = (ad_soru + ' ne zaman yapılacak?',
              f"Hat proje aşamasındadır ({tr_lower(durum)})."
              + (f" Kurumlarca öngörülen açılış: {acilis}." if acilis else
                 ' Kurumlarca açıklanmış bir yapım ya da açılış takvimi bulunmamaktadır.'))
    else:
        s1 = (ad_soru + ' ne zaman açılacak?',
              (f"Tahmini açılış {ym_long(latest['acilis'][:7]) if hedef else acilis} olarak öngörülmektedir." if acilis
               else 'Hat için kurumlarca açıklanmış bir açılış tarihi bulunmamaktadır.') + hedef_cumle
              + (f" {seyir}" if len(runs) > 1 else ''))
    faq = [s1,
        (f"{kod_ref} hattının güzergâhı ve istasyonları nelerdir?",
         f"Hat yaklaşık {fmt_km(p['MESAFE'])} km uzunluğundadır."
         + (f" Hatta {len(stations)} istasyon bulunmaktadır: {st_names}." if stations else '')),
        (f"{kod_ref} hangi hatlarla aktarma sağlayacak?" if asama != 'isletme' else f"{kod_ref} hangi hatlarla aktarma sağlıyor?",
         akt_cumle if stations else ''),
        (f"{kod_ref} hattında inşaat ne aşamada?",
         ' '.join(ozet) if ozet and asama == 'yapim' else
         f"Hat henüz inşaat aşamasına geçmemiştir. Proje aşaması: {durum}." if asama == 'proje' else
         ' '.join(ozet) if ozet else ''),
        ("Bu sayfadaki veriler nereden geliyor?",
         (f"Fiziki ilerleme değerleri {latest.get('pct_src')} yanıtlarına ve kurumların kamuoyuyla paylaştığı en güncel "
          "açıklamalara dayanmaktadır. Aylık gelişme notları resmî duyurular, basın haberleri ve sahadan yapılan "
          "bağımsız paylaşımlar derlenerek hazırlanmaktadır.") if latest.get('pct_src') else
         "Proje bilgileri kurumların kamuoyuyla paylaştığı açıklamalara ve plan belgelerine dayanmaktadır."),
    ]
    faq = [(q, a.strip()) for q, a in faq if a.strip()]

    # ── Diğer yapımı süren hatlar (iç bağlantı)
    others = []
    for f in hat:
        q = f['properties']
        qid = str(q['OBJECTID'])
        if qid == oid or str(q.get('PR_DURUM')) not in YAPIM:
            continue
        c = cards.get(qid, {})
        q_il = (q.get('IL') or '').split(',')[0].strip()
        others.append(((q_il != il, -(c.get('pct') if c.get('pct') is not None else -1)), q, c, q_il))
    others.sort(key=lambda x: x[0])

    soru = {'yapim': ' ne zaman açılacak?', 'proje': ' ne zaman yapılacak?', 'isletme': ': açılış ve yapım süreci'}[asama]
    title = (f"{baslik_kisa[0].upper() + baslik_kisa[1:]}" + (f" ({takma})" if takma else '') + soru + " | metrometre")
    if asama == 'isletme':
        meta_desc = (f"{kisa_baslik} {date_long(acilis_tarihi)} tarihinde hizmete açılmıştır. " if acilis_tarihi
                     else f"{kisa_baslik}: {durum}. ") + "Yapım süreci, istasyonlar ve aktarmalar."
    elif asama == 'proje':
        meta_desc = (f"{kisa_baslik}: {durum}. " + (f"Öngörülen açılış {acilis}. " if acilis else '')
                     + "Güzergâh, istasyonlar ve aktarmalar.")
    else:
        meta_desc = (f"{kisa_baslik} hattında fiziki ilerleme %{fmt_pct(pct)} ({ym_long(pct_ym)}). "
                     if pct is not None else f"{kisa_baslik}: {durum}. ")
        meta_desc += (f"Hedef açılış {date_long(hedef[0])}. " if hedef else (f"Tahmini açılış {acilis}. " if acilis else ''))
        meta_desc += "Aylık gelişmeler, istasyonlar ve aktarmalar."
    if takma:
        meta_desc = f"{takma}: " + meta_desc
    if il != VARSAYILAN_IL:
        meta_desc = meta_desc.replace(' hattında fiziki', f" hattında ({il}) fiziki", 1)

    # ── JSON-LD
    ld = [
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "metrometre", "item": SITE + "/"},
            {"@type": "ListItem", "position": 2, "name": kisa_baslik, "item": url}]},
        {"@context": "https://schema.org", "@type": "Article",
         "headline": title.replace(' | metrometre', ''), "description": meta_desc,
         "inLanguage": "tr-TR", "dateModified": updated, "image": og_img, "mainEntityOfPage": url,
         "author": {"@type": "Person", "name": "Barış Öztürk", "url": "https://x.com/sefabarisozturk"},
         "publisher": {"@type": "Organization", "name": "metrometre", "url": SITE + "/",
                       "logo": {"@type": "ImageObject", "url": SITE + "/icon-512.png"}},
         "about": {"@type": "Thing", "name": f"{on}{ad}"}},
        {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]},
    ]

    # ── HTML parçaları
    ic = lambda s: icon(p.get('IKON_URL'), kod, color, s)
    metrics = []
    if pct is not None:
        metrics.append((f"%{fmt_pct(pct)}", 'Fiziki ilerleme', f"{ym_long(pct_ym)} · {latest.get('pct_src', '')}"))
    vals = [x for x in months if x['pct'] is not None][-7:]
    side_is_month = acilis and not hedef and durum_kod in YAPIM and re.match(r'^\d{4}-\d{2}$', latest.get('acilis', ''))
    if side_is_month and len(vals) >= 2:
        n = months_between(dt.date.fromisoformat(vals[0]['ym'] + '-01'), dt.date.fromisoformat(vals[-1]['ym'] + '-01'))
        dlt = vals[-1]['pct'] - vals[0]['pct']
        metrics.append((('+' if dlt > 0 else '') + fmt_pct(dlt) + ' puan', f'Son {n} ayda', f'ayda ~{fmt_pct(dlt / n)} puan'))
    elif acilis:
        metrics.append((ym_long(latest['acilis'][:7]) if hedef else acilis, 'Tahmini açılış',
                        f"hedef {date_long(hedef[0]).rsplit(' ', 1)[0]}" if hedef else ''))
    elif durum_kod in YAPIM:
        metrics.append(('Açıklanmadı', 'Tahmini açılış', ''))
    if asama == 'isletme' and acilis_tarihi:
        metrics = [m for m in metrics if m[1] != 'Tahmini açılış']
        metrics.insert(1 if pct is not None else 0, (date_long(acilis_tarihi), 'Açılış', ''))
    metrics.append((f"{fmt_km(p['MESAFE'])} km", 'Uzunluk', tur))
    if stations: metrics.append((str(len(stations)), 'İstasyon', f"{len({id(x[0]) for x in akt_now})} aktarma istasyonu" if akt_now else ''))
    metrics_html = ''.join(
        f'<div class="metric"><div class="metric-val">{esc(v)}</div><div class="metric-lbl">{esc(l)}</div>'
        f'{f"<div class=metric-sub>{esc(s)}</div>" if s else ""}</div>' for v, l, s in metrics)

    countdown = hero_side(p, durum_kod, durum, hedef, latest, months, runs)

    seyir_html = ''
    if runs and not (len(runs) == 1 and runs[0]['start'] == runs[0]['end']):
        items = []
        for i, r in enumerate(runs):
            donem = ym_long(r['start']) if r['start'] == r['end'] else f"{ym_long(r['start'])} – {ym_long(r['end'])}"
            cls = ' moved' if i > 0 else ''
            items.append(f'<li class="sy-row{cls}"><span class="sy-when">{esc(donem)}</span>'
                         f'<span class="sy-val">{esc(acilis_label(r["val"]))}</span></li>')
        seyir_html = (f'<section id="s-sy" aria-labelledby="h-sy"><h2 class="sec-lbl" id="h-sy">Tahmini açılış tarihinin seyri</h2>'
                      f'<div class="panel pad"><ol class="seyir">{"".join(items)}</ol>'
                      f'<p class="prose small">{esc(seyir)}</p></div></section>')

    st_items = []
    for s in stations:
        chips = ''.join(
            f'<span class="akt{" plan" if a["planli"] else ""}" title="{esc(a["istasyon"])} · ~{a["m"]} m'
            f'{" · " + esc(DURUM_LABEL.get(a["durum"], "")) if a["planli"] else ""}">'
            f'{icon(a["ikon"], kod_goster(a["kod"]), a["renk"], 16)}{esc(KOD_AD.get(a["kod"], kod_goster(a["kod"])))}'
            f'{("<em>yapımda</em>" if a["durum"] in YAPIM else "<em>planlanan</em>") if a["planli"] else ""}</span>'
            for a in s['aktarma'])
        st_items.append(f'<li data-n="{s["n"]}"><span class="st-badge" style="--c:{stroke}">{s["n"]}</span>'
                        f'<span class="st-ad">{esc(s["ad"])}</span><span class="st-akt">{chips}</span></li>')

    gel = []
    acilis_by_ym = {}
    last_val = None
    for m in months:
        if known(m['acilis']):
            moved = last_val and last_val[:7] != m['acilis'][:7]
            acilis_by_ym[m['ym']] = (m['acilis'], last_val if moved else None)
            last_val = m['acilis']
    for m in reversed(months):
        if not m['not'] or m['not'].startswith('('):
            continue
        chips = []
        chips.append(f'<span class="chip">%{fmt_pct(m["pct"])}</span>' if m['pct'] is not None
                     else '<span class="chip muted">ilerleme verisi bekleniyor</span>')
        a = acilis_by_ym.get(m['ym'])
        if a and a[1]:
            chips.append(f'<span class="chip warn">Tahmini açılış {esc(acilis_label(a[0]))} '
                         f'<s>{esc(acilis_label(a[1]))}</s></span>')
        gel.append(f'<li class="gl-item"><div class="gl-head"><h3><time datetime="{m["ym"]}">{esc(ym_long(m["ym"]))}</time></h3>'
                   f'{"".join(chips)}</div><p class="prose">{esc(m["not"])}</p></li>')

    faq_html = ''.join(f'<details class="faq"><summary><h3>{esc(q)}</h3></summary><p class="prose">{esc(a)}</p></details>'
                       for q, a in faq)

    bar = lambda c: (f'<span class="oth-bar"><i style="width:{c["pct"]:.0f}%"></i></span>'
                     if c.get('pct') is not None else '')
    oth_html = ''.join(
        f'<a class="oth" href="/hat/{q["OBJECTID"]}-{slugify(q["KOD"] + " " + q["AD"])}">'
        f'{icon(q.get("IKON_URL"), q["KOD"], q.get("RENK_HEX"), 26)}'
        f'<span class="oth-txt"><b>{esc(kod_goster(q["KOD"]))}</b> {esc(tire(re.sub(r" (Metro|Tramvay) Hattı$", "", q["AD"])))}'
        f'{f" <span class=oth-il>{esc(q_il)}</span>" if q_il != il else ""}'
        f'<span class="oth-sub">{"%" + fmt_pct(c["pct"]) + " · " if c.get("pct") is not None else ""}{esc(c.get("acilis", ""))}</span></span>'
        f'{bar(c)}</a>'
        for _, q, c, q_il in others[:8])

    og_ek = {'yapim': 'son durum ve tahmini açılış', 'proje': 'proje durumu ve güzergâh',
             'isletme': 'açılış ve yapım süreci'}[asama]
    share_text = f"{kisa_baslik}: {og_ek}"
    has_chart = any(m['pct'] is not None for m in months)
    chart_section = (f'<section id="s-il" aria-labelledby="h-il"><h2 class="sec-lbl" id="h-il">Aylık fiziki ilerleme</h2>'
                     f'<div class="panel chart-panel">{build_chart(months, color, esc("Kaynak: " + latest.get("pct_src", "")))}</div></section>'
                     if has_chart else '')
    gel_section = (f'<section id="s-gl" aria-labelledby="h-gl"><h2 class="sec-lbl" id="h-gl">Aylık gelişmeler</h2>'
                   f'<ol class="panel gelismeler">{"".join(gel)}</ol></section>' if gel else '')
    st_note = f"{fmt_km(p['MESAFE'])} km" + (f", {len(stations)} istasyon" if stations else '')
    crumb_tab = {'yapim': 'yapımı süren hatlar', 'proje': 'proje aşamasındaki hatlar', 'isletme': 'işletmedeki hatlar'}[asama]
    sekme = asama
    harita_url = f"/#{il_hash}tab={sekme}&hat={oid}"
    tag = {'yapim': 'Yapımı süren', 'proje': 'Proje aşamasında', 'isletme': DURUM_LABEL.get(durum_kod, 'İşletmede')}[asama]
    veri_ay = f"{ym_long(data_ym)} verisi · " if data_ym else ''

    return TEMPLATE.format(
        title=esc(title), desc=esc(meta_desc), url=esc(url), og_img=esc(og_img),
        og_title=esc(f"{kisa_baslik}: {og_ek}"),
        og_alt=esc(f"{kisa_baslik} fiziki ilerleme kartı" if card else 'metrometre — raylı sistem projeleri takip paneli'),
        updated=updated, ld=json.dumps(ld, ensure_ascii=False).replace('</', '<\\/'),
        accent_line=esc(stroke), line_color=esc(color),
        crumb_kod=esc(kod_ref), tag=esc(tag), veri_ay=esc(veri_ay), updated_long=esc(date_long(updated)),
        crumb_tab=esc(crumb_tab), sekme=sekme, chart_section=chart_section, gel_section=gel_section, st_note=esc(st_note),
        icon_hero=ic(44), h1=esc(h1_txt),
        sub=esc(' · '.join(x for x in [takma, il if il != VARSAYILAN_IL else '', durum, f"Yürütücü: {kurum}" if kurum else ''] if x)),
        durum=esc(durum), kurum=esc(kurum), dek=esc(dek), countdown=countdown,
        hero_cls=' has-cd' if countdown else '', metrics=metrics_html,
        harita_url=esc(harita_url),
        wa=esc('https://wa.me/?text=' + quote(f"{share_text} {url}")),
        xshare=esc(f"https://x.com/intent/post?text={quote(share_text)}&url={quote(url, safe='')}"),
        share_url=esc(url),
        seyir=seyir_html,
        map_svg=build_map(feat, stations, hat, durak), stations=''.join(st_items),
        faq=faq_html, others=oth_html, il=esc(il), il_hash=esc(il_hash), il_slug=slugify(il),
        header_css=HEADER_CSS, site_header=site_header('hatlar'),
    )


TEMPLATE = r'''<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<meta property="og:type"         content="article">
<meta property="og:site_name"    content="metrometre">
<meta property="og:locale"       content="tr_TR">
<meta property="og:url"          content="{url}">
<meta property="og:title"        content="{og_title}">
<meta property="og:description"  content="{desc}">
<meta property="og:image"        content="{og_img}">
<meta property="og:image:width"  content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt"    content="{og_alt}">
<meta property="article:modified_time" content="{updated}">
<meta name="twitter:card"        content="summary_large_image">
<meta name="twitter:site"        content="@sefabarisozturk">
<meta name="twitter:title"       content="{og_title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image"       content="{og_img}">
<link rel="alternate" type="application/rss+xml" title="metrometre — aylık ilerleme güncellemeleri" href="/feed.xml">
<meta name="theme-color" content="#B4271F">
<link rel="icon" href="/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script type="application/ld+json">{ld}</script>
<style>
*, *::before, *::after {{ box-sizing:border-box; margin:0; padding:0; }}
/* metrometre (index.html, bülten) ile aynı jetonlar */
:root {{
  --bg:#F0F0F0; --surface:#FFFFFF; --surface-2:#F7F7F7;
  --border:#E4E4E4; --border-2:#D1D1D1;
  --text:#111111; --text-muted:#6B6B6B; --text-dim:#757575;
  --accent:#B4271F; --header-h:52px;
  --line:{line_color}; --line-ink:{accent_line};
  --font:'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
}}
html {{ -webkit-text-size-adjust:100%; }}
body {{ background:var(--bg); color:var(--text); font-family:var(--font); font-size:13px; line-height:1.5; }}
a {{ color:var(--accent); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
:focus:not(:focus-visible) {{ outline:none; }}

{header_css}

/* ── Sayfa içi düğmeler ── */
.btn {{ display:inline-flex; align-items:center; gap:7px; height:32px; padding:0 13px 0 11px;
  border:1px solid var(--border-2); border-radius:99px; background:transparent; color:var(--text);
  font-family:var(--font); font-size:12px; font-weight:600; letter-spacing:-0.01em; white-space:nowrap; cursor:pointer;
  transition:border-color .15s, color .15s, background .15s, box-shadow .15s; flex-shrink:0; }}
.btn:hover {{ border-color:var(--accent); color:var(--accent); background:#FEF2F2; text-decoration:none; }}
.btn.primary {{ border-color:var(--accent); background:var(--accent); color:#fff; }}
.btn.primary:hover {{ background:#9C1F18; color:#fff; box-shadow:0 2px 8px rgba(180,39,31,.28); }}
.btn svg {{ width:15px; height:15px; flex-shrink:0; }}

/* ── Sayfa ── */
.crumbs {{ font-size:11px; color:var(--text-dim); display:flex; gap:6px; flex-wrap:wrap; margin:0 2px -10px; }}
.crumbs a {{ color:var(--text-muted); }}
.crumbs span[aria-hidden] {{ color:var(--border-2); }}
.panel {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; overflow:hidden; }}
.pad {{ padding:16px 18px; }}
.sec-lbl {{ font-size:10px; font-weight:700; color:var(--text-dim); letter-spacing:.05em; text-transform:uppercase; margin:0 2px 8px; }}
.sec-note {{ font-weight:500; text-transform:none; letter-spacing:0; }}
.prose {{ font-size:13.5px; line-height:1.65; color:var(--text-muted); }}
.prose.small {{ font-size:12.5px; margin-top:12px; }}
.tag {{ font-size:10px; font-weight:600; padding:1px 6px; border-radius:4px; white-space:nowrap; }}
.tag-accent {{ background:#FEF2F2; color:var(--accent); }}
.card-chip {{ font-size:11px; color:var(--text-dim); }}

/* Yuvarlak hat ikonu (sitedeki .card-icon) */
.line-icon {{ width:var(--s); height:var(--s); border-radius:50%; overflow:hidden; flex-shrink:0;
  display:inline-flex; align-items:center; justify-content:center; background:#F0F0F0; border:1.5px solid var(--border); }}
.line-icon img {{ width:100%; height:100%; object-fit:cover; display:block; }}
.icon-fb {{ width:100%; height:100%; display:flex; align-items:center; justify-content:center; border-radius:50%;
  font-size:9px; font-weight:700; color:#fff; letter-spacing:-0.04em; }}
.icon-fb[hidden] {{ display:none; }}

/* ── Yerleşim: masaüstünde künye + grafik solda, güzergâh sağda (ilk ekranda üçü birlikte) ── */
main {{ max-width:1100px; margin:0 auto; padding:calc(var(--header-h) + 12px) 16px 40px;
  display:grid; grid-template-columns:minmax(0,1fr) 340px; column-gap:18px; row-gap:18px; align-items:start; }}
.crumbs  {{ grid-column:1 / -1; }}
.hero    {{ grid-column:1; grid-row:2; }}
#s-il    {{ grid-column:1; grid-row:3; }}
#s-gz    {{ grid-column:2; grid-row:2 / span 5; }}
#s-sy    {{ grid-column:1; grid-row:4; }}
#s-gl    {{ grid-column:1; grid-row:5; }}
#s-sss   {{ grid-column:1; grid-row:6; }}
#s-ot, .method {{ grid-column:1 / -1; }}

/* ── Künye ── */
.hero {{ display:grid; grid-template-columns:minmax(0,1fr); }}
.hero.has-cd {{ grid-template-columns:minmax(0,1fr) 196px; }}
.hero-main {{ padding:18px 20px 16px; display:flex; flex-direction:column; gap:9px; min-width:0; }}
.eyebrow {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
.title-row {{ display:flex; align-items:center; gap:12px; }}
h1 {{ font-size:24px; font-weight:700; line-height:1.18; letter-spacing:-0.03em; text-wrap:balance; }}
.sub {{ font-size:12px; color:var(--text-dim); margin-top:3px; }}
.dek {{ font-size:13.5px; line-height:1.6; color:var(--text-muted); max-width:62ch; }}
.actions {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:2px; }}
.actions .btn:not(.primary) {{ width:32px; padding:0; justify-content:center; }}
.actions .btn:not(.primary) .lbl {{ display:none; }}
.side {{ border-left:1px solid var(--border); background:var(--surface-2); padding:16px 16px 14px;
  display:flex; flex-direction:column; justify-content:space-between; gap:10px; min-width:0; }}
.cd-head {{ font-size:11px; font-weight:700; }}
.cd-num {{ display:flex; align-items:baseline; gap:6px; flex-wrap:wrap; }}
.cd-num b {{ font-size:56px; line-height:.9; font-weight:700; letter-spacing:-0.05em; color:var(--accent); }}
.cd-num > span {{ font-size:15px; font-weight:600; color:var(--text-muted); }}
.side.month .cd-num b, .side.pace .cd-num b {{ font-size:34px; letter-spacing:-0.03em; color:var(--text); }}
.side.month.moved .cd-num b {{ color:var(--accent); }}
.side.pace.flat .cd-num b {{ color:var(--text-dim); }}
.side.service .cd-num b {{ color:var(--text); }}
.cd-foot {{ font-size:11.5px; color:var(--text-muted); line-height:1.4; }}
.cd-foot strong {{ color:var(--text); }}
.cd-foot s {{ color:var(--text-dim); }}
.steps {{ list-style:none; display:flex; flex-direction:column; gap:5px; }}
.steps li {{ font-size:12px; color:var(--text-dim); display:flex; align-items:center; gap:8px; }}
.steps li::before {{ content:""; width:9px; height:9px; border-radius:50%; border:1.5px solid var(--border-2); background:#fff; flex-shrink:0; }}
.steps li.done::before {{ background:var(--text-muted); border-color:var(--text-muted); }}
.steps li.cur {{ color:var(--text); font-weight:700; }}
.steps li.cur::before {{ background:var(--accent); border-color:var(--accent); box-shadow:0 0 0 3px #FEF2F2; }}
.metrics {{ grid-column:1 / -1; display:grid; grid-template-columns:repeat(4,1fr); border-top:1px solid var(--border); }}
.metric {{ padding:10px 8px 9px; display:flex; flex-direction:column; align-items:center; gap:3px; text-align:center; min-width:0; }}
.metric:not(:last-child) {{ border-right:1px solid var(--border); }}
.metric-val {{ font-size:16px; font-weight:700; line-height:1.15; }}
.metric-lbl {{ font-size:9px; font-weight:600; color:var(--text-dim); letter-spacing:.04em; text-transform:uppercase; }}
.metric-sub {{ font-size:10.5px; color:var(--text-dim); line-height:1.3; }}

/* ── Grafik ── */
.chart-panel {{ padding:12px 16px 10px; }}
.one-pt {{ display:flex; align-items:center; gap:12px; padding:10px 0 8px; }}
.op-bar {{ flex:1; height:10px; border-radius:5px; background:#EDEDED; overflow:hidden; }}
.op-bar i {{ display:block; height:100%; border-radius:5px; }}
.op-txt {{ font-size:12px; color:var(--text-muted); white-space:nowrap; }}
.op-txt b {{ font-size:15px; color:var(--text); }}
.chart-wrap {{ position:relative; }}
.chart-wrap.c-mob {{ display:none; }}
.chart {{ width:100%; height:auto; display:block; overflow:visible; }}
.chart .grid {{ stroke:#EDEDED; stroke-width:1; }}
.chart .tick {{ font-size:10.5px; fill:var(--text-dim); font-variant-numeric:tabular-nums; }}
.chart .endlbl {{ font-size:12px; font-weight:700; fill:var(--text); }}
.chart .hit {{ fill:transparent; cursor:crosshair; }}
.chart .xhair {{ stroke:var(--border-2); stroke-width:1; }}
.tip {{ position:absolute; pointer-events:none; background:var(--text); color:#fff; font-size:11.5px; line-height:1.4; z-index:5;
  padding:6px 9px; border-radius:6px; transform:translate(-50%, calc(-100% - 12px)); white-space:nowrap; box-shadow:0 2px 8px rgba(0,0,0,.18); }}
.tip b {{ font-size:13px; }}
.tip span {{ color:#CFCFCF; }}
.chart-foot {{ display:flex; gap:12px; align-items:baseline; justify-content:space-between; margin-top:4px; flex-wrap:wrap; }}
.chart-note {{ font-size:11px; color:var(--text-dim); }}
.tbl {{ font-size:12px; }}
.tbl summary {{ cursor:pointer; color:var(--text-muted); font-weight:600; font-size:11.5px; }}
.tbl[open] {{ flex-basis:100%; order:2; }}
.tbl table {{ width:100%; border-collapse:collapse; margin-top:6px; }}
.tbl th, .tbl td {{ text-align:left; padding:5px 6px; border-bottom:1px solid var(--border); font-variant-numeric:tabular-nums; }}
.tbl caption {{ text-align:left; font-size:11px; color:var(--text-dim); padding:2px 0 4px; }}
.tbl th {{ font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:var(--text-dim); }}

/* ── Açılış seyri ── */
.seyir {{ list-style:none; display:flex; flex-wrap:wrap; gap:8px 22px; align-items:center; }}
.sy-row {{ position:relative; display:flex; flex-direction:column; gap:2px; padding:8px 12px; border:1px solid var(--border); border-radius:8px; background:var(--surface-2); }}
.sy-row + .sy-row::before {{ content:"→"; position:absolute; left:-17px; top:50%; transform:translateY(-50%); color:var(--text-dim); }}
.sy-when {{ font-size:10.5px; color:var(--text-dim); }}
.sy-val {{ font-size:14px; font-weight:700; }}
.sy-row.moved {{ border-color:#F3C9C6; background:#FEF2F2; }}
.sy-row.moved .sy-val {{ color:var(--accent); }}

/* ── Güzergâh (sağ sütun: harita üstte, istasyonlar altta) ── */
.route {{ display:flex; flex-direction:column; }}
.route-map {{ background:var(--surface-2); border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:center; }}
.mini-map {{ width:100%; height:auto; display:block; max-height:340px; }}
.mini-map .ctx {{ fill:none; stroke:#C9C9C9; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }}
.mini-map .ctx-lbl {{ font-size:10px; font-weight:600; fill:var(--text-dim); paint-order:stroke; stroke:#F7F7F7; stroke-width:3px; }}
.mini-map .st-n {{ font-size:9.5px; font-weight:700; fill:var(--text); }}
.mini-map .st.on circle {{ fill:var(--line); }}
.stations {{ list-style:none; padding:4px 0; }}
.stations:empty {{ display:none; }}
.stations li {{ display:grid; grid-template-columns:22px minmax(0,1fr); column-gap:10px; row-gap:2px; align-items:center;
  padding:4px 14px; position:relative; }}
.stations li::before {{ content:""; position:absolute; left:24px; top:0; bottom:0; width:3px; background:var(--line); }}
.stations li:first-child::before {{ top:50%; }}
.stations li:last-child::before {{ bottom:50%; }}
.stations li.on {{ background:var(--surface-2); }}
.st-badge {{ position:relative; z-index:1; width:22px; height:22px; border-radius:50%; background:#fff; border:2px solid var(--c);
  display:flex; align-items:center; justify-content:center; font-size:10px; font-weight:700; }}
.st-ad {{ font-size:12.5px; font-weight:600; }}
.st-akt {{ grid-column:2; display:flex; gap:4px; flex-wrap:wrap; }}
.st-akt:empty {{ display:none; }}
.akt {{ display:inline-flex; align-items:center; gap:4px; font-size:10.5px; font-weight:600; padding:1px 7px 1px 2px;
  border:1px solid var(--border); border-radius:99px; color:var(--text); background:#fff; }}
.akt .line-icon {{ border-width:1px; }}
.akt em {{ font-style:normal; font-weight:500; color:var(--text-dim); }}
.akt.plan {{ color:var(--text-muted); background:var(--surface-2); }}
.route-foot {{ border-top:1px solid var(--border); padding:9px 14px; display:flex; flex-direction:column; gap:3px;
  font-size:11px; color:var(--text-dim); }}

/* ── Aylık gelişmeler ── */
.gelismeler {{ list-style:none; }}
.gl-item {{ padding:15px 18px; }}
.gl-item + .gl-item {{ border-top:1px solid var(--border); }}
.gl-head {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px; }}
.gl-head h3 {{ font-size:14px; font-weight:700; margin-right:4px; }}
.chip {{ font-size:10.5px; font-weight:600; padding:1px 7px; border-radius:99px; background:var(--surface-2); border:1px solid var(--border); }}
.chip.muted {{ color:var(--text-dim); font-weight:500; }}
.chip.warn {{ background:#FEF2F2; border-color:#F3C9C6; color:var(--accent); }}
.chip s {{ color:var(--text-dim); font-weight:500; margin-left:3px; }}

/* ── SSS ── */
.faq + .faq {{ border-top:1px solid var(--border); }}
.faq summary {{ list-style:none; cursor:pointer; padding:13px 40px 13px 18px; position:relative; }}
.faq summary::-webkit-details-marker {{ display:none; }}
.faq summary::after {{ content:"+"; position:absolute; right:18px; top:50%; transform:translateY(-50%); font-size:18px; color:var(--text-dim); }}
.faq[open] summary::after {{ content:"−"; }}
.faq summary h3 {{ font-size:13.5px; font-weight:600; }}
.faq p {{ padding:0 18px 15px; }}

/* ── Diğer hatlar ── */
.others {{ display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:8px; }}
.oth {{ display:grid; grid-template-columns:auto minmax(0,1fr); grid-template-rows:auto auto; column-gap:10px; align-items:center;
  background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:10px 12px; color:var(--text); }}
.oth:hover {{ border-color:var(--border-2); text-decoration:none; }}
.oth .line-icon {{ grid-row:1 / 3; }}
.oth-txt {{ font-size:12.5px; line-height:1.35; min-width:0; }}
.oth-il {{ font-size:10px; font-weight:600; color:var(--text-dim); border:1px solid var(--border); border-radius:4px; padding:0 4px; margin-left:2px; }}
.oth-sub {{ display:block; font-size:11px; color:var(--text-dim); }}
.oth-bar {{ grid-column:2; height:4px; border-radius:2px; background:#EDEDED; margin-top:6px; overflow:hidden; }}
.oth-bar i {{ display:block; height:100%; background:var(--text-muted); border-radius:2px; }}

.method {{ font-size:12px; color:var(--text-dim); line-height:1.6; max-width:90ch; }}
footer {{ max-width:1100px; margin:0 auto; padding:4px 16px 32px; font-size:11px; color:var(--text-dim); }}

/* ── Tablet: tek sütun; sıra künye → grafik → güzergâh → diğerleri ── */
@media (max-width:1000px) {{
  main {{ grid-template-columns:minmax(0,1fr); max-width:760px; }}
  .hero, #s-il, #s-gz, #s-sy, #s-gl, #s-sss {{ grid-column:1; grid-row:auto; }}
  .route {{ display:grid; grid-template-columns:minmax(0,300px) minmax(0,1fr); }}
  .route-map {{ border-bottom:0; border-right:1px solid var(--border); }}
  .route-foot {{ grid-column:1 / -1; }}
  .others {{ grid-template-columns:repeat(2, minmax(0,1fr)); }}
}}
/* ── Telefon ── */
@media (max-width:640px) {{
  main {{ row-gap:14px; padding-top:calc(var(--header-h) + 10px); }}
  .hero-main {{ padding:14px 16px 12px; gap:8px; }}
  h1 {{ font-size:20px; }}
  .title-row .line-icon {{ --s:36px !important; }}
  .dek {{ display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; font-size:13px; }}
  .actions .btn {{ height:36px; }}
  .actions .btn:not(.primary) {{ width:36px; }}
  .hero.has-cd {{ grid-template-columns:minmax(0,1fr); }}
  .side {{ border-left:0; border-top:1px solid var(--border); flex-direction:row; align-items:center; padding:10px 16px; gap:12px; }}
  .side .cd-head {{ max-width:84px; }}
  .side .cd-num {{ flex-wrap:nowrap; }}
  .side .cd-num b {{ font-size:34px; }}
  .side.month .cd-num b, .side.pace .cd-num b {{ font-size:22px; }}
  .side .cd-foot {{ max-width:150px; text-align:right; font-size:11px; }}
  .side.phase {{ flex-direction:column; align-items:stretch; }}
  .side.phase .steps {{ flex-direction:row; flex-wrap:wrap; gap:6px 10px; }}
  .side.phase .cd-foot {{ max-width:none; text-align:left; }}
  .metric {{ padding:8px 4px 7px; }}
  .metric-val {{ font-size:13.5px; }}
  .metric-lbl {{ font-size:8.5px; }}
  .metric-sub {{ display:none; }}
  .chart-panel {{ padding:10px 12px 8px; }}
  .chart-wrap.c-desk {{ display:none; }}
  .chart-wrap.c-mob {{ display:block; }}
  .route {{ display:flex; }}
  .route-map {{ border-right:0; border-bottom:1px solid var(--border); }}
  .mini-map {{ max-height:300px; }}
  .others {{ grid-template-columns:minmax(0,1fr); }}
}}
@media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; }} }}
</style>
</head>
<body>

{site_header}

<main>
<nav class="crumbs" aria-label="Konum"><a href="/">metrometre</a><span aria-hidden="true">›</span><a href="/hatlar/#{il_slug}-{sekme}">{il} · {crumb_tab}</a><span aria-hidden="true">›</span><span aria-current="page">{crumb_kod}</span></nav>

<article class="panel hero{hero_cls}" aria-labelledby="h1">
  <div class="hero-main">
    <div class="eyebrow"><span class="tag tag-accent">{tag}</span><span class="card-chip">{veri_ay}{updated_long} tarihinde güncellendi</span></div>
    <div class="title-row">{icon_hero}<div><h1 id="h1">{h1}</h1><div class="sub">{sub}</div></div></div>
    <p class="dek">{dek}</p>
    <div class="actions">
      <a class="btn primary" href="{harita_url}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/></svg><span class="lbl">Haritada aç</span></a>
      <a class="btn" href="{wa}" target="_blank" rel="noopener" aria-label="WhatsApp'ta paylaş" title="WhatsApp'ta paylaş"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 20.5 5 16a8.5 8.5 0 1 1 3.2 3.1z"/><path d="M9 9.5c.3 2 2.5 4.2 4.6 4.6l1.3-1.2 1.8.9-.5 1.6c-3.7.2-7.6-3.7-7.4-7.4l1.6-.5.9 1.8z"/></svg><span class="lbl">WhatsApp</span></a>
      <a class="btn" href="{xshare}" target="_blank" rel="noopener" aria-label="X'te paylaş" title="X'te paylaş"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 4l16 16M20 4 4 20"/></svg><span class="lbl">X'te paylaş</span></a>
      <button class="btn" type="button" id="copy" data-url="{share_url}" aria-label="Bağlantıyı kopyala" title="Bağlantıyı kopyala"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg><span class="lbl">Bağlantıyı kopyala</span></button>
    </div>
  </div>
  {countdown}
  <div class="metrics">{metrics}</div>
</article>

{chart_section}

<section id="s-gz" aria-labelledby="h-gz">
  <h2 class="sec-lbl" id="h-gz">Güzergâh ve istasyonlar <span class="sec-note">· {st_note}</span></h2>
  <div class="panel route">
    <div class="route-map">{map_svg}</div>
    <ol class="stations">{stations}</ol>
    <div class="route-foot"><span>Aktarma: işletmedeki hatla ~350 m içinde · yapımda / planlanan: henüz işletmede olmayan hatlar</span><a href="{harita_url}">Haritada ayrıntılı incele →</a></div>
  </div>
</section>

{seyir}

{gel_section}

<section id="s-sss" aria-labelledby="h-sss">
  <h2 class="sec-lbl" id="h-sss">Sık sorulan sorular</h2>
  <div class="panel">{faq}</div>
</section>

<section id="s-ot" aria-labelledby="h-ot">
  <h2 class="sec-lbl" id="h-ot">Yapımı süren diğer hatlar <span class="sec-note">· <a href="/hatlar/">bütün hatlar</a></span></h2>
  <div class="others">{others}</div>
</section>

<p class="method">metrometre, raylı sistem projelerinin fiziki ilerlemesini bağımsız olarak izleyen bir sivil girişimdir; herhangi bir kurumla bağlantısı bulunmamaktadır. İlerleme değerleri kurumların resmî açıklamalarına ve bilgi edinme başvurularına verilen yanıtlara dayanmakta, yerinde bağımsız denetim yapılmamaktadır. Aylık veriler <a href="https://github.com/sefabarisozturk/metrometre-data">GitHub'da açık veri</a> olarak yayımlanmaktadır; güncellemeleri <a href="/feed.xml">RSS</a> ya da <a href="https://x.com/sefabarisozturk">X</a> üzerinden takip edebilirsiniz. Veri kaynakları ve yöntem için: <a href="/hakkinda/">metrometre hakkında</a>.</p>
</main>

<footer>© Barış Öztürk · sbarisozturk@gmail.com · <a href="https://x.com/sefabarisozturk">X</a> · <a href="https://www.linkedin.com/in/sefabarisozturk/">LinkedIn</a> · <a href="/bulten/">Bültenler</a></footer>

<script>
(function(){{
  // Geri sayım: sayfa önbellekten gelse de güncel gün sayısı
  var cd = document.querySelector('.side[data-hedef]');
  if(cd){{
    var t = new Date(cd.dataset.hedef + 'T00:00:00+03:00'), now = new Date();
    var d = Math.ceil((t - now) / 864e5);
    if(d > 0) document.getElementById('cd-days').textContent = d; else cd.remove();
  }}
  // Bağlantıyı kopyala
  var cp = document.getElementById('copy');
  if(cp) cp.addEventListener('click', function(){{
    navigator.clipboard && navigator.clipboard.writeText(cp.dataset.url).then(function(){{
      var o = cp.textContent; cp.textContent = 'Kopyalandı'; setTimeout(function(){{ cp.textContent = o; }}, 1600);
    }});
  }});
  // Grafik ipucu
  document.querySelectorAll('.chart-wrap').forEach(function(wrap){{
  var svg = wrap.querySelector('.chart'), tip = wrap.querySelector('.tip'), xh = svg.querySelector('.xhair');
  svg.querySelectorAll('.hit').forEach(function(r){{
    function show(){{
      var vb = svg.viewBox.baseVal, k = svg.getBoundingClientRect().width / vb.width;
      tip.innerHTML = '<b>' + r.dataset.val + '</b><br><span>' + r.dataset.ay + (r.dataset.src ? ' · ' + r.dataset.src : '') + '</span>';
      tip.style.left = (r.dataset.x * k) + 'px'; tip.style.top = (r.dataset.y * k) + 'px'; tip.hidden = false;
      xh.setAttribute('x1', r.dataset.x); xh.setAttribute('x2', r.dataset.x); xh.setAttribute('visibility', 'visible');
    }}
    function hide(){{ tip.hidden = true; xh.setAttribute('visibility', 'hidden'); }}
    r.addEventListener('mouseenter', show); r.addEventListener('mouseleave', hide);
    r.addEventListener('touchstart', show, {{passive:true}});
  }});
  }});
  // İstasyon listesi ↔ harita vurgusu
  document.querySelectorAll('.stations li').forEach(function(li){{
    var dot = document.querySelector('.mini-map .st[data-n="' + li.dataset.n + '"]');
    li.addEventListener('mouseenter', function(){{ li.classList.add('on'); dot && dot.classList.add('on'); }});
    li.addEventListener('mouseleave', function(){{ li.classList.remove('on'); dot && dot.classList.remove('on'); }});
  }});
}})();
</script>
</body>
</html>
'''


# ───────────────────────── toplu üretim ─────────────────────────
def secili_hatlar(hat, overlay):
    """Sayfa alacak hatlar: yapım + proje aşamasındakiler + izleme verisi olan (açılmış) hatlar.
    Aynı kod ve adı taşıyan parçalı geometriler tek sayfada toplanır (diğer id'ler yönlendirilir)."""
    izlenen = {r['OBJECTID'].strip() for r in overlay}
    sayfa, takma, gorulen = [], {}, {}
    for f in sorted(hat, key=lambda f: int(f['properties']['OBJECTID'])):
        q = f['properties']
        oid, d = str(q['OBJECTID']), str(q.get('PR_DURUM'))
        if not (d in ASAMA_YAPIM or d in ASAMA_PROJE or oid in izlenen):
            continue
        key = (q['KOD'], q['AD'])
        if key in gorulen:
            takma[oid] = gorulen[key]
            continue
        gorulen[key] = oid
        sayfa.append(oid)
    return sayfa, takma

def icerik_ozeti(html_):
    return hashlib.sha1(re.sub(r'<!--v-->.*?<!--/v-->', '', html_, flags=re.S).encode()).hexdigest()[:12]

SITEMAP_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

def build_sitemap(repo, manifest):
    urls = []
    son = max((v['lastmod'] for v in manifest['sayfalar'].values()), default=BUGUN.isoformat())
    urls.append((SITE + '/', son))
    urls.append((SITE + '/hatlar/', son))
    urls.append((SITE + '/hakkinda/', None))
    urls.append((SITE + '/bulten/', None))
    try:
        bl = json.loads((repo / 'bulten' / 'bultenler.json').read_text(encoding='utf-8'))['bultenler']
        for b in sorted(bl, key=lambda b: b['ay'], reverse=True):
            if (repo / 'bulten' / f"{b['ay']}.html").exists():
                urls.append((f"{SITE}/bulten/{b['ay']}", None))
    except Exception:
        pass
    for oid, v in sorted(manifest['sayfalar'].items(), key=lambda kv: int(kv[0])):
        urls.append((f"{SITE}/hat/{oid}-{v['slug']}", v['lastmod']))
    body = ''.join(f"  <url><loc>{esc(u)}</loc>{f'<lastmod>{lm}</lastmod>' if lm else ''}</url>\n" for u, lm in urls)
    return SITEMAP_HEAD + body + '</urlset>\n'

def build_hakkinda(repo, overlay):
    """/hakkinda/ — metin scripts/hakkinda_icerik.html dosyasından okunur (tek kaynak; sitede
    yayımlanmaz). İki parça burada üretilir: kaynak rozetleri (index.html'deki SOURCE_TYPES)
    ve veri dönemi cümlesi (overlay.csv'deki en son ilerleme ayı)."""
    src = (repo / 'index.html').read_text(encoding='utf-8')
    body = (repo / 'scripts' / 'hakkinda_icerik.html').read_text(encoding='utf-8')
    body = re.sub(r'<!--.*?-->', '', body, flags=re.S).strip()
    # kaynak rozetleri
    m = re.search(r'const SOURCE_TYPES = \[(.*?)\];', src, re.S)
    turler = re.findall(r"key:'(\w+)',\s*label:'([^']*)',\s*desc:'([^']*)'", m[1] if m else '')
    legend = ''.join(f'<div class="src-legend-row"><span class="src-badge src-{k}"><span class="src-dot"></span>{esc(l)}</span>'
                     f'<span>{esc(d)}</span></div>' for k, l, d in turler)
    body = body.replace('<div id="src-legend" class="src-legend"></div>', f'<div class="src-legend">{legend}</div>')
    # veri dönemi
    yms = sorted({r['YilAy'].strip() for r in overlay if parse_pct(r.get('Ilerleme')) is not None})
    asof = f" Paneldeki en güncel fiziki ilerleme verisi {ym_long(yms[-1])} dönemine aittir." if yms else ''
    body = body.replace('<span id="modal-asof"></span>', esc(asof))
    # bölüm başlıkları sayfada gerçek başlık olsun; yeni sekme açtırmayalım
    body = re.sub(r'<div class="modal-section-title">(.*?)</div>', r'<h2>\1</h2>', body)
    body = body.replace(' target="_blank" rel="noopener"', '')
    body = re.sub(r'<p style="margin-top:8px">', '<p>', body)
    ozet = re.sub(r'<[^>]+>', '', re.search(r'<p>(.*?)</p>', body, re.S)[1]).strip()
    return (PAGE_HAKKINDA.replace('{{BODY}}', body).replace('{{DESC}}', esc(ozet[:300]))
            .replace('{{HEADER_CSS}}', HEADER_CSS).replace('{{HEADER}}', site_header('hakkinda')))

ROBOTS = f"User-agent: *\nAllow: /\n\nSitemap: {SITE}/sitemap.xml\n"

ASAMA_BASLIK = {'yapim': 'Yapımı süren hatlar', 'isletme': 'Yakın zamanda açılan hatlar',
                'proje': 'Proje aşamasındaki hatlar'}

def build_hatlar(hat, cards, manifest, overlay):
    """/hatlar/ — bütün hat sayfalarının il ve aşamaya göre dizini."""
    by_id = {str(f['properties']['OBJECTID']): f['properties'] for f in hat}
    gruplar = {}
    for oid in manifest['sayfalar']:
        q = by_id[oid]
        il = (q.get('IL') or '').split(',')[0].strip() or VARSAYILAN_IL
        gruplar.setdefault((il, asama_of(str(q.get('PR_DURUM')))), []).append(q)
    iller = sorted({k[0] for k in gruplar}, key=lambda x: (x != VARSAYILAN_IL, x))
    bolumler, toplam = [], 0
    for il in iller:
        parcalar = []
        for asama in ('yapim', 'isletme', 'proje'):
            satirlar = gruplar.get((il, asama), [])
            if not satirlar:
                continue
            if asama == 'yapim':
                satirlar.sort(key=lambda q: -(cards.get(str(q['OBJECTID']), {}).get('pct') or 0))
            else:
                satirlar.sort(key=lambda q: (len(kod_goster(q['KOD'])) < 2, kod_goster(q['KOD']), q['AD']))
            rows = []
            for q in satirlar:
                oid = str(q['OBJECTID'])
                c = cards.get(oid, {})
                kod = kod_goster(q['KOD'])
                ad = tire(re.sub(r" (Metro|Tramvay) Hattı$", "", q['AD']))
                if asama == 'yapim':
                    alt = ' · '.join(x for x in [f"%{fmt_pct(c['pct'])}" if c.get('pct') is not None else '',
                                                 f"Tahmini açılış: {c['acilis']}" if c.get('acilis') else ''] if x)
                elif asama == 'isletme':
                    alt = f"Açılış: {c['acilis']}" if c.get('acilis') else 'İşletmede'
                else:
                    alt = DURUM_LABEL.get(str(q.get('PR_DURUM')), '')
                bar = (f'<span class="bar"><i style="width:{c["pct"]:.0f}%"></i></span>'
                       if asama == 'yapim' and c.get('pct') is not None else '')
                rows.append(f'<li><a href="/hat/{oid}-{manifest["sayfalar"][oid]["slug"]}">'
                            f'{icon(q.get("IKON_URL"), kod if len(kod) > 1 else (kod or "•"), q.get("RENK_HEX"), 26)}'
                            f'<span class="t"><b>{esc(kod + " " if len(kod) > 1 else "")}</b>{esc(ad)}'
                            f'<span class="s">{esc(alt)}</span></span>{bar}</a></li>')
                toplam += 1
            parcalar.append(f'<section id="{slugify(il)}-{asama}"><h3 class="grp">{ASAMA_BASLIK[asama]} '
                            f'<span>{len(satirlar)}</span></h3><ul class="list">{"".join(rows)}</ul></section>')
        bolumler.append(f'<section class="il"><h2>{esc(il)}</h2>{"".join(parcalar)}</section>')
    return (PAGE_HATLAR.replace('{{BOLUMLER}}', ''.join(bolumler))
            .replace('{{HEADER_CSS}}', HEADER_CSS).replace('{{HEADER}}', site_header('hatlar'))
            .replace('{{ILLER}}', esc(' ve '.join(iller)))
            .replace('{{ILLER_SIFAT}}', esc(' ve '.join(iller[:-1]) + (' ve ' if len(iller) > 1 else '') + iller[-1] + "'deki")
                     if iller else ''))

def build_404(hat, cards, manifest):
    yapim = []
    for f in hat:
        q = f['properties']
        oid = str(q['OBJECTID'])
        if str(q.get('PR_DURUM')) in ASAMA_YAPIM and oid in manifest['sayfalar']:
            yapim.append(((q.get('IL') or '').split(',')[0].strip() != VARSAYILAN_IL,
                          -(cards.get(oid, {}).get('pct') or 0), q, oid))
    yapim.sort(key=lambda x: x[:2])
    links = ''.join(
        f'<a class="oth" href="/hat/{oid}-{manifest["sayfalar"][oid]["slug"]}">'
        f'{icon(q.get("IKON_URL"), kod_goster(q["KOD"]), q.get("RENK_HEX"), 26)}'
        f'<span class="oth-txt"><b>{esc(kod_goster(q["KOD"]))}</b> '
        f'{esc(tire(re.sub(r" (Metro|Tramvay) Hattı$", "", q["AD"])))}</span></a>'
        for _, _, q, oid in yapim)
    return (PAGE_404.replace('{{LINKS}}', links)
            .replace('{{HEADER_CSS}}', HEADER_CSS).replace('{{HEADER}}', site_header(None)))

PAGE_HATLAR = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ILLER}} raylı sistem projeleri: yapımı süren ve planlanan hatlar | metrometre</title>
<meta name="description" content="{{ILLER}}'de yapımı süren, proje aşamasındaki ve yakın zamanda açılan metro, tramvay ve banliyö hatları; fiziki ilerleme, tahmini açılış tarihleri ve hat sayfaları.">
<link rel="canonical" href="https://metrometre.com/hatlar/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="metrometre">
<meta property="og:locale" content="tr_TR">
<meta property="og:url" content="https://metrometre.com/hatlar/">
<meta property="og:title" content="{{ILLER}} raylı sistem projeleri">
<meta property="og:image" content="https://metrometre.com/og-image.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#B4271F">
<link rel="icon" href="/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="alternate" type="application/rss+xml" title="metrometre — aylık ilerleme güncellemeleri" href="/feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }
:root { --bg:#F0F0F0; --surface:#FFFFFF; --surface-2:#F7F7F7; --border:#E4E4E4; --border-2:#D1D1D1; --text:#111111;
  --text-muted:#6B6B6B; --text-dim:#757575; --accent:#B4271F; --header-h:52px;
  --font:'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; }
body { background:var(--bg); color:var(--text); font-family:var(--font); font-size:13px; line-height:1.5; }
a { color:var(--accent); text-decoration:none; }
{{HEADER_CSS}}
main { max-width:900px; margin:0 auto; padding:calc(var(--header-h) + 22px) 16px 40px; display:flex; flex-direction:column; gap:26px; }
h1 { font-size:24px; letter-spacing:-0.03em; line-height:1.2; }
.lead { font-size:14px; color:var(--text-muted); line-height:1.6; max-width:68ch; margin-top:8px; }
.il > h2 { font-size:18px; letter-spacing:-0.02em; margin:0 2px 10px; }
.il section + section { margin-top:16px; }
.grp { font-size:10px; font-weight:700; color:var(--text-dim); letter-spacing:.05em; text-transform:uppercase; margin:0 2px 8px; }
.grp span { font-weight:500; margin-left:4px; }
.list { list-style:none; display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:8px; }
.list a { display:grid; grid-template-columns:auto minmax(0,1fr); grid-template-rows:auto auto; column-gap:10px; align-items:center;
  background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:10px 12px; color:var(--text); height:100%; }
.list a:hover { border-color:var(--border-2); }
.list .line-icon { grid-row:1 / 3; }
.t { font-size:12.5px; line-height:1.35; min-width:0; }
.s { display:block; font-size:11px; color:var(--text-dim); }
.bar { grid-column:2; height:4px; border-radius:2px; background:#EDEDED; margin-top:6px; overflow:hidden; }
.bar i { display:block; height:100%; background:var(--text-muted); border-radius:2px; }
.line-icon { width:var(--s); height:var(--s); border-radius:50%; overflow:hidden; flex-shrink:0; display:inline-flex;
  align-items:center; justify-content:center; background:#F0F0F0; border:1.5px solid var(--border); }
.line-icon img { width:100%; height:100%; object-fit:cover; display:block; }
.icon-fb { width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:9px; font-weight:700; color:#fff; }
.icon-fb[hidden] { display:none; }
.method { font-size:12px; color:var(--text-dim); line-height:1.6; max-width:90ch; }
@media (max-width:640px) { .list { grid-template-columns:minmax(0,1fr); } h1 { font-size:20px; } }
</style>
</head>
<body>
{{HEADER}}
<main>
  <div>
    <h1>{{ILLER}} raylı sistem projeleri</h1>
    <p class="lead">{{ILLER_SIFAT}} yapımı süren, proje aşamasındaki ve yakın zamanda açılan raylı sistem hatları. Her hattın sayfasında aylık fiziki ilerleme, tahmini açılış tarihinin seyri, güzergâh ve istasyonlar yer almaktadır.</p>
  </div>
  {{BOLUMLER}}
  <p class="method">metrometre, raylı sistem projelerinin fiziki ilerlemesini bağımsız olarak izleyen bir sivil girişimdir; herhangi bir kurumla bağlantısı bulunmamaktadır. Veriler aylık olarak güncellenmekte ve <a href="https://github.com/sefabarisozturk/metrometre-data">GitHub'da açık veri</a> olarak yayımlanmaktadır.</p>
</main>
</body>
</html>
"""

PAGE_HAKKINDA = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>metrometre hakkında: veri kaynakları ve yöntem | metrometre</title>
<meta name="description" content="{{DESC}}">
<link rel="canonical" href="https://metrometre.com/hakkinda/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="metrometre">
<meta property="og:locale" content="tr_TR">
<meta property="og:url" content="https://metrometre.com/hakkinda/">
<meta property="og:title" content="metrometre hakkında">
<meta property="og:description" content="{{DESC}}">
<meta property="og:image" content="https://metrometre.com/og-image.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#B4271F">
<link rel="icon" href="/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="alternate" type="application/rss+xml" title="metrometre — aylık ilerleme güncellemeleri" href="/feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"AboutPage","url":"https://metrometre.com/hakkinda/","inLanguage":"tr-TR","name":"metrometre hakkında","isPartOf":{"@type":"WebSite","name":"metrometre","url":"https://metrometre.com/"},"author":{"@type":"Person","name":"Barış Öztürk","url":"https://x.com/sefabarisozturk","sameAs":["https://www.linkedin.com/in/sefabarisozturk/","https://github.com/sefabarisozturk"]}}</script>
<style>
*, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }
:root { --bg:#F0F0F0; --surface:#FFFFFF; --border:#E4E4E4; --border-2:#D1D1D1; --text:#111111;
  --text-muted:#6B6B6B; --text-dim:#757575; --accent:#B4271F; --header-h:52px;
  --font:'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; }
body { background:var(--bg); color:var(--text); font-family:var(--font); font-size:13px; line-height:1.5; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
{{HEADER_CSS}}
main { max-width:680px; margin:0 auto; padding:calc(var(--header-h) + 22px) 16px 40px; }
.panel { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:24px 26px; }
h1 { font-size:22px; letter-spacing:-0.03em; margin-bottom:14px; }
h2 { font-size:12px; font-weight:700; color:var(--text); margin:20px 0 6px; text-transform:uppercase; letter-spacing:.04em; }
p { font-size:14px; color:var(--text-muted); line-height:1.7; }
p + p { margin-top:8px; }
strong { color:var(--text); }
.src-legend { display:flex; flex-direction:column; gap:7px; margin-top:10px; }
.src-legend-row { display:flex; align-items:flex-start; gap:8px; font-size:12.5px; line-height:1.5; color:var(--text-muted); }
.src-badge { display:inline-flex; align-items:center; gap:5px; font-size:10.5px; font-weight:600; line-height:1; padding:4px 8px;
  border-radius:99px; white-space:nowrap; flex-shrink:0; margin-top:1px; }
.src-dot { width:6px; height:6px; border-radius:50%; background:currentColor; flex-shrink:0; }
.src-bilgi { background:#ECFDF5; color:#065F46; } .src-resmi { background:#EFF6FF; color:#1E40AF; }
.src-saha { background:#FFFBEB; color:#92400E; } .src-tahmin { background:#F5F5F5; color:#525252; }
.src-diger { background:#F5F3FF; color:#5B21B6; }
footer { max-width:680px; margin:0 auto; padding:0 16px 32px; font-size:11px; color:var(--text-dim); }
@media (max-width:640px) { .panel { padding:20px 18px; } .src-legend-row { flex-direction:column; gap:4px; } }
</style>
</head>
<body>
{{HEADER}}
<main>
  <article class="panel">
    <h1>metrometre hakkında</h1>
    {{BODY}}
  </article>
</main>
<footer>© Barış Öztürk · <a href="mailto:sbarisozturk@gmail.com">sbarisozturk@gmail.com</a> · <a href="https://x.com/sefabarisozturk">X</a> · <a href="https://www.linkedin.com/in/sefabarisozturk/">LinkedIn</a> · <a href="/hatlar/">Hatlar</a> · <a href="/bulten/">Bültenler</a></footer>
</body>
</html>
"""

PAGE_404 = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sayfa bulunamadı | metrometre</title>
<meta name="robots" content="noindex">
<meta name="theme-color" content="#B4271F">
<link rel="icon" href="/icon-192.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }
:root { --bg:#F0F0F0; --surface:#FFFFFF; --border:#E4E4E4; --border-2:#D1D1D1; --text:#111111;
  --text-muted:#6B6B6B; --text-dim:#757575; --accent:#B4271F; --header-h:52px;
  --font:'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; }
body { background:var(--bg); color:var(--text); font-family:var(--font); font-size:13px; line-height:1.5; }
a { color:var(--accent); text-decoration:none; }
{{HEADER_CSS}}
main { max-width:760px; margin:0 auto; padding:calc(var(--header-h) + 28px) 16px 40px; display:flex; flex-direction:column; gap:22px; }
.panel { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:26px 24px; }
.code { font-size:12px; font-weight:700; color:var(--accent); letter-spacing:.06em; }
h1 { font-size:24px; letter-spacing:-0.03em; margin:6px 0 8px; }
p { font-size:14px; color:var(--text-muted); line-height:1.6; max-width:60ch; }
.actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:16px; }
.btn { display:inline-flex; align-items:center; height:34px; padding:0 14px; border:1px solid var(--border-2); border-radius:99px;
  color:var(--text); font-weight:600; font-size:12.5px; }
.btn.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
.sec-lbl { font-size:10px; font-weight:700; color:var(--text-dim); letter-spacing:.05em; text-transform:uppercase; margin:0 2px 8px; }
.others { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:8px; }
.oth { display:flex; align-items:center; gap:10px; background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:10px 12px; color:var(--text); }
.oth:hover { border-color:var(--border-2); }
.oth-txt { font-size:12.5px; line-height:1.35; }
.line-icon { width:var(--s); height:var(--s); border-radius:50%; overflow:hidden; flex-shrink:0; display:inline-flex;
  align-items:center; justify-content:center; background:#F0F0F0; border:1.5px solid var(--border); }
.line-icon img { width:100%; height:100%; object-fit:cover; display:block; }
.icon-fb { width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:9px; font-weight:700; color:#fff; }
.icon-fb[hidden] { display:none; }
@media (max-width:640px) { .others { grid-template-columns:minmax(0,1fr); } }
</style>
</head>
<body>
{{HEADER}}
<main>
  <section class="panel">
    <div class="code">404</div>
    <h1>Aradığınız sayfa bulunamadı</h1>
    <p>Bağlantı eskimiş ya da adres hatalı yazılmış olabilir. Bütün hatları haritada inceleyebilir ya da aşağıdaki yapımı süren hatlardan birine geçebilirsiniz.</p>
    <div class="actions"><a class="btn primary" href="/">Haritaya git</a><a class="btn" href="/hatlar/">Bütün hatlar</a><a class="btn" href="/bulten/">Aylık bültenler</a><a class="btn" href="/hakkinda/">Hakkında</a></div>
  </section>
  <section>
    <h2 class="sec-lbl">Yapımı süren hatlar</h2>
    <div class="others">{{LINKS}}</div>
  </section>
</main>
</body>
</html>
"""


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='metrometre hat sayfaları, sitemap, robots ve 404 üretici')
    ap.add_argument('--repo', default=Path(__file__).resolve().parent.parent, type=Path)
    ap.add_argument('--id', action='append', help='yalnız bu hat(lar) — önizleme; manifest/sitemap güncellenmez')
    ap.add_argument('--out', type=Path, help='--id ile: çıktı klasörü')
    a = ap.parse_args()
    repo = a.repo
    hat, durak, overlay, cards = load(repo)

    if a.id:
        out = a.out or repo / 'hat-sayfalari'
        out.mkdir(parents=True, exist_ok=True)
        for oid in a.id:
            (out / f"{oid}.html").write_text(build_page(oid, hat, durak, overlay, cards, BUGUN.isoformat()), encoding='utf-8')
            print('yazıldı:', out / f"{oid}.html")
        raise SystemExit

    out = repo / 'hat-sayfalari'
    out.mkdir(exist_ok=True)
    man_path = out / 'manifest.json'
    try:
        eski = json.loads(man_path.read_text(encoding='utf-8')).get('sayfalar', {})
    except Exception:
        eski = {}
    ids, takma = secili_hatlar(hat, overlay)
    manifest = {'aciklama': 'hat_sayfalari.py üretir; elle düzenlemeyin. sayfalar: id → slug, lastmod, ozet. '
                            'takma: aynı hattın parçalı geometrilerinden yönlendirilen id → sayfa id.',
                'sayfalar': {}, 'takma': takma}
    YER_TUTUCU = '2000-01-01'
    for oid in ids:
        q = next(f for f in hat if str(f['properties']['OBJECTID']) == oid)['properties']
        slug = slugify(f"{q['KOD']} {q['AD']}")
        ozet = icerik_ozeti(build_page(oid, hat, durak, overlay, cards, YER_TUTUCU))
        prev = eski.get(oid, {})
        lastmod = prev['lastmod'] if prev.get('ozet') == ozet and prev.get('lastmod') else BUGUN.isoformat()
        hedef_dosya = out / f"{oid}.html"
        # içerik aynıysa dosyaya dokunma: yalnız gün sayısı gibi değişken parçalar için commit oluşmasın
        # (sayfadaki geri sayımı tarayıcı zaten güncel hesaplar)
        if not (prev.get('ozet') == ozet and hedef_dosya.exists()):
            hedef_dosya.write_text(build_page(oid, hat, durak, overlay, cards, lastmod), encoding='utf-8')
        manifest['sayfalar'][oid] = {'slug': slug, 'lastmod': lastmod, 'ozet': ozet}
    # artık sayfası olmayan hatların eski dosyalarını kaldır
    for f in out.glob('*.html'):
        if f.stem not in manifest['sayfalar']:
            f.unlink()
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=False) + '\n', encoding='utf-8')
    (repo / 'sitemap.xml').write_text(build_sitemap(repo, manifest), encoding='utf-8')
    (repo / 'robots.txt').write_text(ROBOTS, encoding='utf-8')
    (repo / '404.html').write_text(build_404(hat, cards, manifest), encoding='utf-8')
    bulten_basliklari(repo)
    (repo / 'hakkinda').mkdir(exist_ok=True)
    (repo / 'hakkinda' / 'index.html').write_text(build_hakkinda(repo, overlay), encoding='utf-8')
    (repo / 'hatlar').mkdir(exist_ok=True)
    (repo / 'hatlar' / 'index.html').write_text(build_hatlar(hat, cards, manifest, overlay), encoding='utf-8')
    # haritadaki "Hat sayfası" düğmesi için hafif liste (id → slug; parçalı geometri id'leri → sayfa id'si)
    (repo / 'hatlar' / 'sayfalar.json').write_text(json.dumps(
        {'sayfalar': {k: v['slug'] for k, v in manifest['sayfalar'].items()}, 'takma': manifest['takma']},
        ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    degisen = sum(1 for k, v in manifest['sayfalar'].items() if eski.get(k, {}).get('ozet') != v['ozet'])
    print(f"{len(ids)} hat sayfası ({degisen} değişen), {len(takma)} yönlendirme; "
          "hatlar/, hakkinda/, sitemap.xml, robots.txt, 404.html yazıldı.")

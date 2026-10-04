// ═════════════════════════════════════════════════════
//  metrometre — Cloudflare Worker
//
//  Site Cloudflare'de "Workers + statik dosyalar" olarak yayınlanıyor
//  (Pages değil). Bu projelerde functions/ klasörü çalışmaz; aynı işi bu
//  dosya yapar. wrangler.jsonc'deki "run_worker_first" ayarı sayesinde
//  Worker YALNIZCA şu iki yolda devreye girer, diğer her şey (index.html,
//  görseller, GeoJSON…) doğrudan statik dosya olarak sunulur:
//
//    /hat/<OBJECTID>[-slug]  → index.html, <head> etiketleri o hatta göre
//                              yeniden yazılmış olarak (X/WhatsApp önizlemesi)
//    /feed.xml               → overlay.csv'den üretilen RSS beslemesi
//
//  Veri: metrometre-data reposu (raw.githubusercontent.com). İstenirse
//  DATA_BASE değişkeniyle başka bir adres verilebilir.
// ═════════════════════════════════════════════════════

const DEFAULT_DATA_BASE = 'https://raw.githubusercontent.com/sefabarisozturk/metrometre-data/main';
const TR_MONTHS = ['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran',
                   'Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];

export default {
  async fetch(request, env, ctx){
    const url = new URL(request.url);
    const dataBase = (env.DATA_BASE || DEFAULT_DATA_BASE).replace(/\/+$/, '');
    try {
      if(url.pathname === '/feed.xml') return await handleFeed(url, dataBase);
      if(url.pathname === '/hat' || url.pathname.startsWith('/hat/')) return await handleHat(request, env, url, dataBase);
    } catch(err){
      console.warn('worker hatası:', err);
      // Her durumda site açılsın: hat yolunda genel index.html'e düş
      if(url.pathname.startsWith('/hat')) return env.ASSETS.fetch(new URL('/', request.url).toString());
      return new Response('Geçici bir hata oluştu.', { status: 503 });
    }
    return env.ASSETS.fetch(request);
  },
};

// ═════════════════════════════════════════════════════
//  /hat/<id> — bağlantı önizlemesi
// ═════════════════════════════════════════════════════
const DURUM_LABEL = {
  '0':'Devre dışı','1':'İşletmede',
  '2':'Test aşamasında','3':'İnşa aşamasında','4':'Beklemede',
  '5':'Uygulama projesi tamamlandı','6':'Uygulama projesi sürüyor',
  '7':'Etüt projesi tamamlandı','8':'Etüt projesi sürüyor',
};
const SITE_DESC = "İstanbul'daki raylı sistem projelerini bağımsız olarak izleyen panel.";
const ymLong = ym => {
  const m = /^(\d{4})-(\d{2})$/.exec(ym || '');
  return m ? `${TR_MONTHS[parseInt(m[2], 10) - 1]} ${m[1]}` : '';
};

function buildMeta(id, rec, origin, dataBase){
  const name  = [rec.kod, rec.ad].filter(Boolean).join(' ');
  const durum = String(rec.durum ?? '');
  const label = DURUM_LABEL[durum] || '';
  const pct   = rec.pct != null ? String(Math.round(rec.pct)) : null;
  let title, desc;

  if(['2','3','4'].includes(durum) && pct !== null){
    title = `${name} · %${pct} fiziki ilerleme`;
    desc  = [`${label}.`, `Fiziki ilerleme %${pct}${rec.ym ? ` (${ymLong(rec.ym)} itibarıyla)` : ''}.`,
             rec.acilis ? `Tahmini açılış: ${rec.acilis}.` : ''].filter(Boolean).join(' ');
  } else if(durum === '1'){
    title = name; desc = rec.acilis ? `${rec.acilis} tarihinde hizmete açıldı.` : 'İşletmede.';
  } else if(durum === '0'){
    title = name; desc = 'Devre dışı.';
  } else {
    title = name;
    desc  = `Proje aşamasında${label ? ` (${label.toLocaleLowerCase('tr-TR')})` : ''}.`
          + (rec.acilis ? ` Tahmini açılış: ${rec.acilis}.` : '');
  }
  return {
    title: `${title} | metrometre`,
    socialTitle: title,
    desc: `${desc} ${SITE_DESC}`,
    url: `${origin}/hat/${encodeURIComponent(id)}`,
    image: rec.card
      ? `${dataBase}/og/${encodeURIComponent(id)}.png?v=${encodeURIComponent(rec.card)}`
      : `${origin}/og-image.png`,
    imageAlt: rec.card ? `${name} fiziki ilerleme kartı` : 'metrometre — İstanbul raylı sistem projeleri takip paneli',
  };
}

async function handleHat(request, env, url, dataBase){
  // Sitenin kök index.html'i
  const page = await env.ASSETS.fetch(new URL('/', request.url).toString());
  const id = (/^\/hat\/(\d+)/.exec(url.pathname) || [])[1];
  if(!id || !page.ok) return page;

  const r = await fetch(`${dataBase}/og/lines.json`, { cf: { cacheTtl: 300, cacheEverything: true } });
  if(!r.ok) return page;
  const rec = (await r.json())?.lines?.[id];
  if(!rec) return page;

  const m = buildMeta(id, rec, url.origin, dataBase);
  const setContent = v => ({ element(el){ el.setAttribute('content', v); } });
  const out = new HTMLRewriter()
    .on('title',                            { element(el){ el.setInnerContent(m.title); } })
    .on('meta[name="description"]',         setContent(m.desc))
    .on('link[rel="canonical"]',            { element(el){ el.setAttribute('href', m.url); } })
    .on('meta[property="og:url"]',          setContent(m.url))
    .on('meta[property="og:title"]',        setContent(m.socialTitle))
    .on('meta[property="og:description"]',  setContent(m.desc))
    .on('meta[property="og:image"]',        setContent(m.image))
    .on('meta[property="og:image:alt"]',    setContent(m.imageAlt))
    .on('meta[name="twitter:title"]',       setContent(m.socialTitle))
    .on('meta[name="twitter:description"]', setContent(m.desc))
    .on('meta[name="twitter:image"]',       setContent(m.image))
    .transform(page);

  const headers = new Headers(out.headers);
  headers.set('Cache-Control', 'public, max-age=300');
  return new Response(out.body, { status: 200, headers });
}

// ═════════════════════════════════════════════════════
//  /feed.xml — aylık RSS beslemesi (her veri ayı tek öğe)
// ═════════════════════════════════════════════════════
const MAX_ITEMS = 12;

function parseCSV(text){
  const rows = []; let row = [], field = '', q = false;
  const s = text.replace(/\r\n?/g, '\n');
  for(let i = 0; i < s.length; i++){
    const c = s[i];
    if(q){
      if(c === '"'){ if(s[i+1] === '"'){ field += '"'; i++; } else q = false; }
      else field += c;
    } else if(c === '"') q = true;
    else if(c === ','){ row.push(field); field = ''; }
    else if(c === '\n'){ row.push(field); rows.push(row); row = []; field = ''; }
    else field += c;
  }
  if(field.length || row.length){ row.push(field); rows.push(row); }
  return rows.filter(r => r.some(c => c.trim() !== ''));
}
const norm = h => String(h || '').trim().toLowerCase()
  .replace(/ı/g,'i').replace(/ş/g,'s').replace(/ğ/g,'g').replace(/ü/g,'u').replace(/ö/g,'o').replace(/ç/g,'c')
  .replace(/[\s_]+/g, '');
const parsePct = raw => {
  const s = String(raw ?? '').trim();
  if(!s || s.startsWith('(')) return null;
  const v = parseFloat(s.replace('%','').replace(/\s/g,'').replace(',', '.'));
  return isNaN(v) ? null : Math.max(0, Math.min(100, v));
};
const toYM = raw => {
  const m = /^(\d{4})[-/.](\d{1,2})$/.exec(String(raw || '').trim());
  return (m && +m[2] >= 1 && +m[2] <= 12) ? `${m[1]}-${String(+m[2]).padStart(2,'0')}` : null;
};
const num   = v => (Math.round(v * 100) / 100).toString().replace('.', ',');
const xml   = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const cdata = s => `<![CDATA[${String(s).replace(/]]>/g, ']]]]><![CDATA[>')}]]>`;
// Veri dönemini izleyen ayın 1'i (verinin derlenip yayımlandığı yaklaşık tarih)
const pubDate = ym => {
  const y = +ym.slice(0, 4), m = +ym.slice(5);
  return new Date(Date.UTC(m === 12 ? y + 1 : y, m === 12 ? 0 : m, 1, 6, 0, 0)).toUTCString();
};

async function handleFeed(url, dataBase){
  const origin = url.origin;
  const r = await fetch(`${dataBase}/overlay.csv`, { cf: { cacheTtl: 300, cacheEverything: true } });
  if(!r.ok) return new Response('Besleme şu anda oluşturulamadı.', { status: 503, headers: { 'Retry-After': '600' } });
  const rows = parseCSV(await r.text());

  const head = rows[0].map(norm);
  const col  = (...names) => names.map(n => head.indexOf(n)).find(i => i >= 0) ?? -1;
  const iOid = col('objectid','oid'), iKod = col('kod','hatkodu'), iHat = col('hat','ad','hatadi');
  const iYm  = col('yilay','donem','ay'), iPct = col('ilerleme','yuzde','oran','pct');
  const iNot = col('aciklama','not','metin'), iSrc = col('kaynak','kaynakturu');

  const lines = new Map();
  for(const c of rows.slice(1)){
    const ym = toYM(c[iYm]); const pct = parsePct(c[iPct]);
    const key = (c[iOid] || c[iKod] || '').trim();
    if(!ym || pct === null || !key) continue;
    if(!lines.has(key)){
      lines.set(key, { id: (c[iOid] || '').trim(),
        ad: [c[iKod], c[iHat]].map(x => (x || '').trim()).filter(Boolean).join(' '), series: new Map() });
    }
    lines.get(key).series.set(ym, {
      pct,
      note: iNot >= 0 ? (c[iNot] || '').trim() : '',
      src:  iSrc >= 0 ? (c[iSrc] || '').trim() : '',
    });
  }

  const months = [...new Set([...lines.values()].flatMap(l => [...l.series.keys()]))].sort().reverse().slice(0, MAX_ITEMS);
  const items = months.map(ym => {
    const entries = [];
    for(const l of lines.values()){
      const cur = l.series.get(ym);
      if(!cur) continue;
      const prevYm = [...l.series.keys()].filter(k => k < ym).sort().pop();
      const prev = prevYm ? l.series.get(prevYm) : null;
      entries.push({ l, cur, delta: prev ? cur.pct - prev.pct : null,
                     newNote: cur.note && (!prev || prev.note !== cur.note) ? cur.note : '' });
    }
    entries.sort((a, b) => (b.delta ?? -1e9) - (a.delta ?? -1e9) || b.cur.pct - a.cur.pct);

    const li = entries.map(({ l, cur, delta, newNote }) => {
      const d = delta === null ? '' : delta > 0.004 ? ` (+${num(delta)} puan)` : delta < -0.004 ? ` (${num(delta)} puan)` : ' (değişmedi)';
      const link = l.id ? `${origin}/hat/${encodeURIComponent(l.id)}` : origin;
      return `<li><a href="${xml(link)}"><b>${xml(l.ad)}</b></a>: %${num(cur.pct)}${d}${cur.src ? ` <i>· ${xml(cur.src)}</i>` : ''}`
        + (newNote ? `<br>${xml(newNote)}` : '') + '</li>';
    }).join('');
    const top = entries.find(e => e.delta !== null && e.delta > 0.004);
    const title = `${ymLong(ym)} fiziki ilerleme verileri` + (top ? ` — en çok ilerleyen: ${top.l.ad}` : '');
    const html = `<p>İstanbul raylı sistem projelerinin ${ymLong(ym)} sonu itibarıyla fiziki ilerleme değerleri:</p><ul>${li}</ul>`
      + `<p><a href="${xml(origin)}">metrometre.com</a> üzerinde haritada inceleyin.</p>`;
    return `<item>
  <title>${xml(title)}</title>
  <link>${xml(origin)}/</link>
  <guid isPermaLink="false">metrometre-${ym}</guid>
  <pubDate>${pubDate(ym)}</pubDate>
  <description>${cdata(html)}</description>
</item>`;
  }).join('\n');

  const body = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>metrometre — aylık ilerleme güncellemeleri</title>
  <link>${xml(origin)}/</link>
  <atom:link href="${xml(origin)}/feed.xml" rel="self" type="application/rss+xml"/>
  <description>İstanbul'da yapımı süren raylı sistem projelerinin aylık fiziki ilerleme verileri.</description>
  <language>tr</language>
  ${months.length ? `<lastBuildDate>${pubDate(months[0])}</lastBuildDate>` : ''}
${items}
</channel>
</rss>`;
  return new Response(body, {
    headers: { 'Content-Type': 'application/rss+xml; charset=utf-8', 'Cache-Control': 'public, max-age=900' },
  });
}

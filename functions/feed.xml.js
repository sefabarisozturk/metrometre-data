// ═════════════════════════════════════════════════════
//  metrometre — RSS beslemesi (Cloudflare Pages Function) → /feed.xml
//
//  overlay.csv'den her veri dönemi (ay) için TEK bir öğe üretir: o ayki
//  fiziki ilerleme değerleri, bir önceki aya göre değişim ve o ay yeni
//  girilen açıklamalar. Okuyucular (Feedly, Inoreader, Thunderbird…) ve
//  otomasyon araçları (IFTTT/Zapier → X, Telegram, e-posta) bunu takip edebilir.
//  Ayrı bir derleme adımı yoktur; tablo güncellendiğinde besleme de güncellenir.
// ═════════════════════════════════════════════════════

const DEFAULT_DATA_BASE = 'https://raw.githubusercontent.com/sefabarisozturk/metrometre-data/main';
const TR_MONTHS = ['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran',
                   'Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
const MAX_ITEMS = 12;

// RFC-4180 uyumlu küçük CSV ayrıştırıcı (sitedeki parseCSV ile aynı mantık)
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
const ymLong = ym => `${TR_MONTHS[parseInt(ym.slice(5), 10) - 1]} ${ym.slice(0, 4)}`;
const num = v => (Math.round(v * 100) / 100).toString().replace('.', ',');
const xml = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const cdata = s => `<![CDATA[${String(s).replace(/]]>/g, ']]]]><![CDATA[>')}]]>`;
// Veri dönemini izleyen ayın 1'i (verinin derlenip yayımlandığı yaklaşık tarih)
const pubDate = ym => {
  const y = +ym.slice(0, 4), m = +ym.slice(5);
  return new Date(Date.UTC(m === 12 ? y + 1 : y, m === 12 ? 0 : m, 1, 6, 0, 0)).toUTCString();
};

export async function onRequest(context){
  const { request, env } = context;
  const origin   = new URL(request.url).origin;
  const dataBase = (env.DATA_BASE || DEFAULT_DATA_BASE).replace(/\/+$/, '');

  let rows;
  try {
    const r = await fetch(`${dataBase}/overlay.csv`, { cf: { cacheTtl: 300, cacheEverything: true } });
    if(!r.ok) throw new Error('HTTP ' + r.status);
    rows = parseCSV(await r.text());
  } catch(err){
    return new Response('Besleme şu anda oluşturulamadı.', { status: 503, headers: { 'Retry-After': '600' } });
  }

  const head = rows[0].map(norm);
  const col  = (...names) => names.map(n => head.indexOf(n)).find(i => i >= 0) ?? -1;
  const iOid = col('objectid','oid'), iKod = col('kod','hatkodu'), iHat = col('hat','ad','hatadi');
  const iYm  = col('yilay','donem','ay'), iPct = col('ilerleme','yuzde','oran','pct');
  const iNot = col('aciklama','not','metin'), iSrc = col('kaynak','kaynakturu');

  // hat → { ad, series: Map(ym → {pct, note, src}) }
  const lines = new Map();
  for(const c of rows.slice(1)){
    const ym = toYM(c[iYm]); const pct = parsePct(c[iPct]);
    const key = (c[iOid] || c[iKod] || '').trim();
    if(!ym || pct === null || !key) continue;
    if(!lines.has(key)){
      lines.set(key, { id: (c[iOid] || '').trim(), ad: [c[iKod], c[iHat]].map(x => (x || '').trim()).filter(Boolean).join(' '), series: new Map() });
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
      const delta = prev ? cur.pct - prev.pct : null;
      const newNote = cur.note && (!prev || prev.note !== cur.note) ? cur.note : '';
      entries.push({ l, cur, delta, newNote });
    }
    // Önce en çok ilerleyenler
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
    headers: {
      'Content-Type': 'application/rss+xml; charset=utf-8',
      'Cache-Control': 'public, max-age=900',
    },
  });
}

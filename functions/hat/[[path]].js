// ═════════════════════════════════════════════════════
//  metrometre — hat bağlantısı önizlemesi (Cloudflare Pages Function)
//
//  metrometre.com/hat/<OBJECTID>[-slug] adresine gelen istekte sitenin
//  index.html'ini döner, ama <head> içindeki başlık / açıklama / Open Graph /
//  X kartı etiketlerini o hatta göre yeniden yazar. X, WhatsApp, LinkedIn,
//  Telegram gibi botlar JavaScript çalıştırmadığı için hat başına önizleme
//  ancak sunucu tarafında üretilebilir.
//
//  Veri: metrometre-data reposundaki og/lines.json (GitHub Actions üretir).
//  Görsel: og/<OBJECTID>.png varsa o, yoksa sitenin genel og-image.png'si.
//  Herhangi bir hata olursa sayfa genel etiketlerle, olduğu gibi döner —
//  ziyaretçi için sayfa her durumda açılır; tarayıcıdaki uygulama yolu
//  okuyup ilgili hattı seçer.
// ═════════════════════════════════════════════════════

const DEFAULT_DATA_BASE = 'https://raw.githubusercontent.com/sefabarisozturk/metrometre-data/main';
const TR_MONTHS = ['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran',
                   'Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
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
const pctTxt = v => String(Math.round(v));

// Hat bilgisinden başlık / açıklama / görsel üretir
function buildMeta(id, rec, origin, dataBase){
  const name   = [rec.kod, rec.ad].filter(Boolean).join(' ');
  const durum  = String(rec.durum ?? '');
  const label  = DURUM_LABEL[durum] || '';
  let title, desc;

  if(['2','3','4'].includes(durum) && rec.pct != null){
    title = `${name} · %${pctTxt(rec.pct)} fiziki ilerleme`;
    const parts = [`${label}.`, `Fiziki ilerleme %${pctTxt(rec.pct)}${rec.ym ? ` (${ymLong(rec.ym)} itibarıyla)` : ''}.`];
    if(rec.acilis) parts.push(`Tahmini açılış: ${rec.acilis}.`);
    desc = parts.join(' ');
  } else if(durum === '1'){
    title = name;
    desc  = rec.acilis ? `${rec.acilis} tarihinde hizmete açıldı.` : 'İşletmede.';
  } else if(durum === '0'){
    title = name;
    desc  = 'Devre dışı.';
  } else {
    title = name;
    desc  = `Proje aşamasında${label ? ` (${label.toLocaleLowerCase('tr-TR')})` : ''}.`
          + (rec.acilis ? ` Tahmini açılış: ${rec.acilis}.` : '');
  }

  const image = rec.card
    ? `${dataBase}/og/${encodeURIComponent(id)}.png?v=${encodeURIComponent(rec.card)}`
    : `${origin}/og-image.png`;

  return {
    title:     `${title} | metrometre`,
    socialTitle: title,
    desc:      `${desc} ${SITE_DESC}`,
    url:       `${origin}/hat/${encodeURIComponent(id)}`,
    image,
    imageAlt:  rec.card ? `${name} fiziki ilerleme kartı` : 'metrometre — İstanbul raylı sistem projeleri takip paneli',
  };
}

// <head> etiketlerini yeniden yazan HTMLRewriter
function rewriteHead(res, m){
  const setContent = v => ({ element(el){ el.setAttribute('content', v); } });
  return new HTMLRewriter()
    .on('title',                             { element(el){ el.setInnerContent(m.title); } })
    .on('meta[name="description"]',          setContent(m.desc))
    .on('link[rel="canonical"]',             { element(el){ el.setAttribute('href', m.url); } })
    .on('meta[property="og:url"]',           setContent(m.url))
    .on('meta[property="og:title"]',         setContent(m.socialTitle))
    .on('meta[property="og:description"]',   setContent(m.desc))
    .on('meta[property="og:image"]',         setContent(m.image))
    .on('meta[property="og:image:alt"]',     setContent(m.imageAlt))
    .on('meta[name="twitter:title"]',        setContent(m.socialTitle))
    .on('meta[name="twitter:description"]',  setContent(m.desc))
    .on('meta[name="twitter:image"]',        setContent(m.image))
    .transform(res);
}

export async function onRequest(context){
  const { request, env, params } = context;
  const url     = new URL(request.url);
  const origin  = url.origin;
  const dataBase = (env.DATA_BASE || DEFAULT_DATA_BASE).replace(/\/+$/, '');

  // Sitenin kök index.html'i (SPA)
  const page = await env.ASSETS.fetch(new URL('/', request.url));
  if(!page.ok || !(page.headers.get('content-type') || '').includes('text/html')) return page;

  // /hat/60, /hat/60-m11-arnavutkoy-halkali, /hat/60/… → "60"
  const seg = Array.isArray(params.path) ? params.path[0] : params.path;
  const id  = (/^(\d+)/.exec(seg || '') || [])[1];
  if(!id) return page;

  try {
    const r = await fetch(`${dataBase}/og/lines.json`, { cf: { cacheTtl: 300, cacheEverything: true } });
    if(!r.ok) return page;
    const data = await r.json();
    const rec  = data?.lines?.[id];
    if(!rec) return page;

    const out = rewriteHead(page, buildMeta(id, rec, origin, dataBase));
    const headers = new Headers(out.headers);
    headers.set('Cache-Control', 'public, max-age=300');
    return new Response(out.body, { status: 200, headers });
  } catch(err){
    console.warn('hat önizleme hatası:', err);
    return page;
  }
}

// Komponen bersama semua halaman: navigasi, asisten melayang, panel detail bisnis, toast, notifikasi.
(function () {
  const EMBED = new URLSearchParams(location.search).has('embed');
  const clean = s => String(s ?? '').replace(/[\uE000-\uF8FF]/g, '').replace(/\s*·\s*$/, '').trim();
  const esc = s => clean(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const num = n => n == null ? '-' : n >= 1e6 ? (n / 1e6).toFixed(1).replace('.', ',') + ' jt' : n >= 1e4 ? Math.round(n / 1e3) + ' rb' : Number(n).toLocaleString('id-ID');
  const today = () => new Date().toISOString().slice(0, 10);
  const LEAD_ST = ['baru', 'dihubungi', 'tertarik', 'deal', 'tolak'];
  const ST_LABEL = {baru: 'Baru', dihubungi: 'Dihubungi', tertarik: 'Tertarik', deal: 'Deal', tolak: 'Tidak jadi'};

  // ---------- navigasi ----------
  const NAV = [
    ['/market', 'Riset'],
    ['/prospects', 'Prospek'],
    ['/watch', 'Pantau'],
    ['/', 'Asisten'],
  ];
  const MORE = [
    ['/competitors', 'Kompetitor IG', 'Akun Instagram pesaing di sekitar lokasi', true],
    ['/report', 'Analisis IG', 'Followers, engagement, dan konten akun', true],
    ['/tools', 'Pengaturan lanjutan', 'Scrape manual dan pilihan koneksi', false],
  ];
  function renderNav() {
    const top = document.querySelector('.top');
    if (!top) return;
    if (EMBED) { top.remove(); document.body.classList.add('embed'); return; }
    const here = location.pathname;
    top.innerHTML = `<a class="brand" href="/market" style="text-decoration:none" aria-label="Raqio"><svg class="mark" viewBox="0 0 32 32" aria-hidden="true"><path d="M5 29V3H18A8 8 0 0 1 18 19H11" fill="none" stroke="currentColor" stroke-width="4"/><path d="M17 19L27 29" stroke="var(--good, #FFB020)" stroke-width="4"/></svg><span class="word">raqio</span></a>
      <nav>${NAV.map(([h, l]) => `<a href="${h}" class="${here === h ? 'on' : ''} ${h === '/' ? 'hide-m' : ''}">${l}${h === '/prospects' ? '<span class="badge-n" id="nav-due" hidden></span>' : ''}</a>`).join('')}
        <details class="more"><summary>Lanjutan</summary><div class="menu">
          ${MORE.map(([h, l, d, nw]) => `<a href="${h}" ${nw ? 'target="_blank"' : ''}>${l}<small>${d}</small></a>`).join('')}
        </div></details></nav>`;
    document.addEventListener('click', e => { const m = top.querySelector('.more'); if (m && !m.contains(e.target)) m.open = false; });
    fetch('/prospects/data').then(r => r.json()).then(j => {
      const n = j.items.filter(p => p.lead && p.lead.remind && p.lead.remind <= today() && !['deal', 'tolak'].includes(p.lead.status)).length;
      const b = document.getElementById('nav-due');
      if (b && n) { b.hidden = false; b.textContent = n; b.title = n + ' follow up jatuh tempo'; }
    }).catch(() => {});
  }

  // ---------- drawer ----------
  let bg, drawer;
  function ensureDrawer() {
    if (drawer) return;
    bg = document.createElement('div'); bg.className = 'drawer-bg';
    drawer = document.createElement('aside'); drawer.className = 'drawer';
    document.body.append(bg, drawer);
    bg.onclick = closeDrawer;
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });
  }
  function openDrawer(title, bodyHtml, isFrame) {
    ensureDrawer();
    drawer.innerHTML = `<div class="dh"><b>${title}</b><button class="x" aria-label="Tutup">✕</button></div>` +
      (isFrame ? bodyHtml : `<div class="db">${bodyHtml}</div>`);
    drawer.querySelector('.x').onclick = closeDrawer;
    requestAnimationFrame(() => { bg.classList.add('on'); drawer.classList.add('on'); });
    return drawer;
  }
  function closeDrawer() { if (drawer) { bg.classList.remove('on'); drawer.classList.remove('on'); } }

  function renderFab() {
    if (EMBED || location.pathname === '/') return;
    const b = document.createElement('button');
    b.className = 'fab'; b.innerHTML = 'Tanya asisten';
    b.onclick = () => openDrawer('Asisten', '<iframe src="/?embed=1" title="Asisten"></iframe>', true);
    document.body.appendChild(b);
  }

  // ---------- toast + notifikasi ----------
  let tEl, tTimer;
  function toast(msg) {
    if (!tEl) { tEl = document.createElement('div'); tEl.className = 'toast'; document.body.appendChild(tEl); }
    tEl.textContent = msg; tEl.classList.add('on');
    clearTimeout(tTimer); tTimer = setTimeout(() => tEl.classList.remove('on'), 2600);
  }
  function askNotify() { try { if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission(); } catch (e) {} }
  function notify(title, body) {
    try { if (document.hidden && 'Notification' in window && Notification.permission === 'granted') new Notification(title, {body}); } catch (e) {}
    const t = document.title; if (document.hidden) { document.title = '● ' + title; addEventListener('focus', () => document.title = t, {once: true}); }
  }

  // ---------- kontak + draf pesan ----------
  const waOf = p => {
    let d = String(p.phone || '').replace(/\D/g, '');
    if (d.startsWith('0')) d = '62' + d.slice(1);
    return d.startsWith('628') && d.length >= 10 && d.length <= 15 ? 'https://wa.me/' + d : '';
  };
  const offer = () => { try { return localStorage.getItem('offer') || 'meningkatkan penjualan lewat kehadiran online'; } catch (e) { return 'meningkatkan penjualan lewat kehadiran online'; } };
  const short = n => clean(n).split(/\s[-|@·,(]\s?/)[0].trim();
  function draft(p, industry, area) {
    const r = p.lead_reasons || [];
    const praise = p.rating >= 4.5 ? `rating ${String(p.rating).replace('.', ',')} dari ${p.reviews} ulasan di Google Maps, keren banget!` : `sudah ${p.reviews} ulasan di Google Maps.`;
    const why = r.includes('tanpa website') || (p._detailed && !p.website) ? 'Saya lihat belum ada website atau katalog online, padahal banyak calon pelanggan cek dulu di Google sebelum datang.'
      : r.includes('IG tidak ditemukan') ? 'Saya belum menemukan Instagram resminya, padahal banyak pelanggan cari referensi di sana.'
      : (p.reviews || 0) < 100 ? 'Ulasannya masih bisa ditambah supaya lebih sering muncul di pencarian.'
      : 'Masih ada ruang untuk tampil lebih menonjol dibanding pesaing sekitar.';
    return `Halo kak, salam kenal. Saya lihat ${short(p.name)} punya ${praise} ${why} Kebetulan saya bantu bisnis ${industry || ''} di sekitar ${area || ''} untuk ${offer()}. Boleh saya kirim contoh singkatnya? Terima kasih 🙏`;
  }
  async function saveLead(p, patch) {
    const lead = Object.assign({status: 'baru'}, p.lead || {}, patch);
    const r = await fetch('/market/lead', {method: 'POST', body: JSON.stringify({key: p.key || p.place_id || p.maps_url, name: clean(p.name),
      status: lead.status, note: lead.note ?? null, remind: lead.remind ?? null})});
    if (!r.ok) { toast((await r.json()).error || 'Gagal menyimpan'); return false; }
    p.lead = Object.assign(lead, {updated: new Date().toISOString()});
    return true;
  }

  // ---------- panel detail bisnis ----------
  function openBusiness(p, ctx) {
    ctx = ctx || {};
    const industry = ctx.industry || p.industry, area = ctx.area || p.area, rid = ctx.report_id || p.report_id;
    const wa = waOf(p), lead = p.lead || {};
    const host = u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (e) { return u; } };
    const no = '<span style="color:var(--faint)">tidak ada</span>', unk = '<span style="color:var(--faint)">belum dicek</span>';
    const html = `
      <div class="bz-sec">
        <div class="muted-note">${esc(p.category || '')}${area ? ' · ' + esc(area) : ''}</div>
        <div class="big-n" style="margin-top:12px">
          <div><b>${p.rating ? String(p.rating).replace('.', ',') : '-'}</b><small>rating</small></div>
          <div><b>${num(p.reviews)}</b><small>ulasan</small></div>
          <div><b>${p.lead_score ?? '-'}</b><small>skor prospek</small></div>
        </div>
        ${(p.lead_reasons || []).length ? `<div class="reasons">${p.lead_reasons.map(x => `<span>${esc(x)}</span>`).join('')}</div>` : ''}
      </div>
      <div class="bz-sec"><h4>Kontak</h4>
        <div class="kv">
          <span>Telepon</span><span>${p.phone ? esc(p.phone) : (p._detailed ? no : unk)}</span>
          <span>Website</span><span>${p.website ? `<a href="${esc(p.website)}" target="_blank">${esc(host(p.website))}</a>` : (p._detailed ? no : unk)}</span>
          <span>Instagram</span><span>${p.instagram ? `<a href="https://instagram.com/${esc(p.instagram)}" target="_blank">@${esc(p.instagram)}</a>` : (p._detailed ? no : unk)}</span>
          <span>Email</span><span>${(p.emails || []).length ? p.emails.map(e => `<a href="mailto:${esc(e)}">${esc(e)}</a>`).join('<br>') : no}</span>
          <span>Alamat</span><span>${esc(p.address || '-')}</span>
        </div>
        <div class="acts-row">
          ${wa ? `<a class="btn wa-btn" target="_blank" href="${wa}?text=${encodeURIComponent(draft(p, industry, area))}">Kirim WhatsApp</a>` : ''}
          ${p.maps_url ? `<a class="btn ghost" target="_blank" href="${esc(p.maps_url)}">Buka di Maps</a>` : ''}
          ${!p._detailed && rid ? '<button class="btn ghost" id="bz-detail">Cek kontak sekarang</button>' : ''}
        </div>
      </div>
      <div class="bz-sec"><h4>Follow up</h4>
        <div class="st-row">${LEAD_ST.map(s => `<button data-st="${s}" class="${lead.status === s ? 'on' : ''}">${ST_LABEL[s]}</button>`).join('')}</div>
        <label class="field">Catatan<textarea id="bz-note" placeholder="mis. pemiliknya Pak Budi, minta dihubungi sore">${esc(lead.note || '')}</textarea></label>
        <label class="field">Ingatkan saya tanggal<input type="date" id="bz-remind" value="${esc(lead.remind || '')}"></label>
        <div class="acts-row"><button class="btn" id="bz-save">Simpan</button></div>
      </div>
      <div class="bz-sec"><h4>Draf pesan</h4><p class="muted-note" id="bz-draft">${esc(draft(p, industry, area))}</p>
        <button class="btn ghost" id="bz-copy">Salin draf</button></div>
      <div class="bz-sec" id="bz-ig">${p.instagram ? '<h4>Instagram</h4><div class="muted-note">Memuat…</div>' : ''}</div>`;
    const d = openDrawer(esc(p.name), html);
    let status = lead.status;
    d.querySelectorAll('[data-st]').forEach(b => b.onclick = () => {
      status = b.dataset.st; d.querySelectorAll('[data-st]').forEach(x => x.classList.toggle('on', x === b)); });
    d.querySelector('#bz-save').onclick = async () => {
      const ok = await saveLead(p, {status: status || 'baru', note: d.querySelector('#bz-note').value, remind: d.querySelector('#bz-remind').value});
      if (ok) { toast('Tersimpan'); ctx.onChange && ctx.onChange(p); }
    };
    d.querySelector('#bz-copy').onclick = async e => {
      try { await navigator.clipboard.writeText(draft(p, industry, area)); e.target.textContent = 'Tersalin ✓'; } catch (x) { e.target.textContent = 'Gagal menyalin'; } };
    const det = d.querySelector('#bz-detail');
    if (det) det.onclick = async () => {
      det.disabled = true; det.textContent = 'Membuka Google Maps… (±15 detik)';
      const r = await fetch('/market/detail', {method: 'POST', body: JSON.stringify({report_id: rid, key: p.key || p.place_id || p.maps_url})});
      const j = await r.json();
      if (!r.ok) { det.disabled = false; det.textContent = 'Cek kontak sekarang'; toast(j.error || 'Gagal'); return; }
      Object.assign(p, j); toast('Kontak diperbarui'); ctx.onChange && ctx.onChange(p); openBusiness(p, ctx);
    };
    if (p.instagram) loadIg(d.querySelector('#bz-ig'), p.instagram);
  }
  async function loadIg(el, handle) {
    const j = await (await fetch('/business?ig=' + encodeURIComponent(handle))).json();
    if (!j.found) {
      el.innerHTML = `<h4>Instagram</h4><p class="muted-note">@${esc(handle)} belum pernah dianalisis.</p>
        <button class="btn ghost" id="bz-scrape">Analisis akun ini</button>`;
      el.querySelector('#bz-scrape').onclick = async e => {
        const r = await fetch('/run', {method: 'POST', body: JSON.stringify({targets: handle, mode: 'direct'})});
        const k = await r.json();
        if (!r.ok) { toast(k.error || 'Gagal'); return; }
        e.target.disabled = true; e.target.textContent = 'Sedang dianalisis, buka lagi 1 sampai 2 menit lagi';
      };
      return;
    }
    const er = j.engagement_rate;
    el.innerHTML = `<h4>Instagram @${esc(j.username)}</h4>
      <div class="big-n"><div><b>${num(j.followers)}</b><small>followers${j.followers_delta ? ` (${j.followers_delta > 0 ? '+' : ''}${num(j.followers_delta)})` : ''}</small></div>
        <div><b>${er != null ? String(er).replace('.', ',') + '%' : '-'}</b><small>engagement</small></div>
        <div><b>${j.posts_per_week != null ? String(j.posts_per_week).replace('.', ',') : '-'}</b><small>post/minggu</small></div></div>
      ${j.biography ? `<p class="muted-note" style="margin-top:12px">${esc(j.biography)}</p>` : ''}
      <p class="muted-note" style="margin-top:8px">${er != null ? (er >= 3 ? 'Interaksi tinggi, audiensnya aktif.' : er >= 1 ? 'Interaksi wajar.' : 'Interaksi rendah, kontennya kurang ditanggapi.') : ''}
        ${j.best_day ? ' Hari terbaik posting: ' + esc(j.best_day) + '.' : ''}</p>`;
  }

  window.Shell = {esc, clean, num, toast, notify, askNotify, waOf, draft, saveLead, openBusiness, openDrawer, closeDrawer, LEAD_ST, ST_LABEL, today};
  const boot = () => { renderNav(); renderFab(); };
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', boot) : boot();
})();

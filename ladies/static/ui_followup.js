'use strict';

(() => {
  const FILTER_CATALOG = {
    categories: [
      ['', 'Alle'],
      ['huren-hostessen', 'Ladies'],
      ['bordelle-laufhaeuser', 'Locations'],
      ['escort', 'Escort'],
      ['transen-shemale', 'TS/TV'],
      ['erotische-massage', 'Massage'],
      ['domina-sm', 'Bizarr-SM'],
      ['fkk-saunaclubs', 'FKK-Clubs'],
      ['swinger', 'Swinger'],
      ['telefonsex', 'Telefonsex'],
      ['onlineservice', 'Onlineservice'],
    ],
    traits: {
      Figur: ['sehr schlank', 'schlank', 'mittel', 'mollig', 'sehr mollig'],
      Typ: ['afrikanisch', 'asiatisch', 'deutsch', 'exotisch', 'latina', 'orientalisch', 'osteuropäisch', 'skandinavisch', 'westeuropäisch'],
      Geschlecht: ['weiblich', 'männlich', 'trans', 'Paar/Duo'],
      Busen: ['klein', 'mittel', 'groß', 'riesig', 'Naturbusen'],
      Intimbereich: ['total rasiert', 'teilrasiert', 'behaart', 'stark behaart'],
      Hautton: ['helle Haut', 'mittlere Haut', 'bräunliche Haut', 'schwarze Haut'],
      Haarfarbe: ['blond', 'dunkelblond', 'brünett', 'rotbraun', 'dunkelbraun', 'schwarz', 'rot', 'grau'],
      Haarlänge: ['Glatze', 'kurz', 'kinnlang', 'schulterlang', 'rückenlang'],
      Augenfarbe: ['braun', 'blau', 'grün', 'blau-grün, grau'],
      Körperschmuck: ['Intim-Piercing', 'Brust-Piercing', 'Gesichts-/Nabelp.', 'Zungen-Piercing', 'Tattoos', 'kein Körperschmuck'],
      Sonstiges: ['Nichtraucher', 'Gesicht erkennbar', 'mit Video', 'Empfehlung', 'Mit 18+ Inhalten'],
    },
    services: {
      Beliebt: ['ZK', 'Franz.', 'nur Massage'],
      'Franz.': ['D**p Thr**t', 'Franz. bei ihr', '69'],
      Anal: ['AV bei ihm', 'FA aktiv', 'FA passiv', 'ZA aktiv', 'ZA passiv'],
      'Bizarr Basic': ['NS aktiv', 'NS passiv', 'KV aktiv', 'KV passiv', 'dominant', 'devot'],
      'Service für': ['Damen', 'Paare', 'Menschen mit Behinderung'],
      'Mehrere Personen': ['Flotter Dreier (MFF)', 'Zu dritt (MMF)', 'Herrenrunde', 'GS (Gruppen-S*x)', 'SW (S*ndw*ch)'],
      Massage: ['Body-to-Body M.', 'erotische Massage', 'Ganzkörperm.', 'HE (Handentsp.)', 'Intim-Massage', 'Öl-Massage', 'Pärchen-Massage', 'Po-Massage', 'Pr*stata-Massage', 'Synchron-/Duo-M.', 'Tantra-Massage', 'Thai-Massage'],
      'Bizarr weitere': ['Bondage/Fess**ung', 'Fetisch-Spielchen', 'Fesselspiele aktiv', 'Fesselspiele passiv', 'FS (Facesitting)', 'Lack, Leder u. Latex', 'Verbalerotik', 'WS'],
      Weitere: ['Dusch-/Badeservice', 'Fotos/Videos möglich', 'Fuß-/Schuherotik', 'Intimrasur', 'Strapserotik', 'gekonnter Striptease', 'LS/Duo (Lesboshow)', 'Nylonerotik', 'Outdoor-Erotik', 'Rollenspiele', 'Squ*rting'],
      Escort: ['Begleitservice', 'Frivoles Ausgehen', 'Urlaubsbegleitung', 'Pärchenbesuche', 'Swingerclub-Begl.', 'Übernacht. b. Gast', 'Wochenende', 'Gastrobegleitung', 'Messebegleitung'],
    },
    meeting: {
      Treffpunkte: ['besuchbar', 'Hausbesuche', 'Hotelbesuche', 'Bürobesuche'],
      Location: ['für Rollstuhlfahrer geeignet'],
      Zeiten: ['jetzt geöffnet', 'vor 10 Uhr', 'auch Samstag', 'heute geöffnet', 'nach 22 Uhr', 'auch Sonntag'],
    },
  };

  const TRAIT_KEYS = {
    Figur: 'figur',
    Typ: 'typ',
    Geschlecht: 'geschlecht',
    Busen: 'busen',
    Intimbereich: 'intimbereich',
    Hautton: 'hautton',
    Haarfarbe: 'haarfarbe',
    Haarlänge: 'haarlaenge',
    Augenfarbe: 'augenfarbe',
    Körperschmuck: 'koerperschmuck',
    Sonstiges: 'sonstiges',
  };

  const escHtml = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[ch]);
  const icon = name => `<svg aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;

  let currentChatThreadId = null;
  let profileOpenedFromChat = false;

  const style = document.createElement('style');
  style.textContent = `
    /* Swipe: keep status chips on one compact line and remove the misleading freshness badge. */
    .swipe-title-line .online-pill,.profile-name-row>.online-pill{display:none!important}
    .swipe-meta-row{
      flex-wrap:nowrap!important;max-height:none!important;overflow-x:auto!important;overflow-y:hidden!important;
      padding:1px 0 3px;scrollbar-width:none;overscroll-behavior-x:contain;-webkit-overflow-scrolling:touch
    }
    .swipe-meta-row::-webkit-scrollbar{display:none}
    .swipe-meta-row .badge{flex:0 0 auto;white-space:nowrap;padding:4px 7px!important;font-size:8.7px!important}

    /* Chat -> profile is a nested layer; the chat itself stays alive underneath. */
    body.chat-profile-open #profileLayer{z-index:180!important}
    #chatAvatar,#chatIdentity{cursor:pointer}
    #chatAvatar[role="button"]:focus-visible,#chatIdentity:focus-visible{outline:2px solid rgba(255,255,255,.9);outline-offset:2px}

    /* Expanded V8 filter catalogue in the current light V9 visual language. */
    .filter-grid.followup-filter-grid{grid-template-columns:1fr;gap:8px}
    .followup-filter-grid .filter-card{min-height:0}
    .followup-filter-grid .filter-card.full{grid-column:auto}
    .followup-filter-section-title{display:flex;align-items:center;gap:7px;margin:0 0 8px;font-size:11px;font-weight:800;color:#173b5c}
    .followup-filter-subtitle{margin:11px 0 6px;color:#6a7d92;font-size:9px;font-weight:800;letter-spacing:.055em;text-transform:uppercase}
    .followup-filter-subtitle:first-child{margin-top:0}
    .followup-filter-grid .mini-chips{gap:5px}
    .followup-filter-grid .mini-chip{padding:7px 9px;font-size:9.5px}
    .followup-top-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
    .followup-top-grid .filter-card{margin:0}
    .followup-range-stack{display:grid;gap:13px}
    .followup-range-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:3px;font-size:10px}
    .followup-range-head b{font-size:11px}
    .followup-range-head strong{font-size:11px;color:#173b5c}
    .followup-dual-range{position:relative;height:34px;margin:0 2px}
    .followup-dual-track,.followup-dual-fill{position:absolute;left:0;right:0;top:15px;height:4px;border-radius:99px}
    .followup-dual-track{background:#dbe5ef}
    .followup-dual-fill{right:auto;background:linear-gradient(90deg,#ff4d7d,#e92a61);pointer-events:none}
    .followup-dual-range input{
      position:absolute;inset:0;width:100%;height:34px;margin:0;background:transparent;appearance:none;-webkit-appearance:none;pointer-events:none
    }
    .followup-dual-range input::-webkit-slider-runnable-track{height:4px;background:transparent}
    .followup-dual-range input::-webkit-slider-thumb{
      appearance:none;-webkit-appearance:none;width:20px;height:20px;margin-top:-8px;border-radius:50%;
      border:2px solid #fff;background:#f72f64;box-shadow:0 2px 8px rgba(37,65,94,.28);pointer-events:auto
    }
    .followup-dual-range input::-moz-range-track{height:4px;background:transparent}
    .followup-dual-range input::-moz-range-thumb{
      width:18px;height:18px;border-radius:50%;border:2px solid #fff;background:#f72f64;
      box-shadow:0 2px 8px rgba(37,65,94,.28);pointer-events:auto
    }
    .followup-filter-note{margin:7px 0 0;color:#71849a;font-size:9px;line-height:1.35}
    .followup-image-modes{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;padding-bottom:2px}
    .followup-image-modes::-webkit-scrollbar{display:none}
    .followup-image-mode{flex:0 0 auto;padding:7px 10px;border:1px solid #dce8f4;border-radius:10px;background:#edf4fb;color:#314a63;font-size:9.5px;font-weight:750}
    .followup-image-mode.active{background:#ffe7ef;color:#e52b60;border-color:#ffd3e0}
    .followup-local-toggles{display:flex;gap:8px;overflow-x:auto;padding:2px 0;scrollbar-width:none}
    .followup-local-toggles::-webkit-scrollbar{display:none}
    .followup-filter-grid .filter-default-actions{margin-top:2px}
    @media(max-width:360px){.followup-top-grid{grid-template-columns:1fr}.followup-filter-grid .mini-chip{font-size:9px;padding:6px 8px}}
  `;
  document.head.appendChild(style);

  function selected(group, value) {
    return (window.state?.filters?.selected?.[group] || []).includes(value);
  }

  function chip(group, value, label=value) {
    return `<button type="button" class="mini-chip ${selected(group, value) ? 'active' : ''}" data-followup-filter-group="${escHtml(group)}" data-followup-filter-value="${escHtml(value)}">${escHtml(label)}</button>`;
  }

  function chips(group, values) {
    return `<div class="mini-chips">${values.map(value => chip(group, value)).join('')}</div>`;
  }

  function categoryChip(value, label) {
    const active = window.state?.filters?.category === value;
    return `<button type="button" class="mini-chip ${active ? 'active' : ''}" data-followup-category="${escHtml(value)}">${escHtml(label)}</button>`;
  }

  function localToggle(key, label, iconName) {
    const on = Boolean(window.state?.filters?.local?.[key]);
    return `<button type="button" class="small-toggle-label" data-followup-local-toggle="${escHtml(key)}">${iconName ? icon(iconName) : ''}<span class="small-switch ${on ? 'on' : ''}"></span>${escHtml(label)}</button>`;
  }

  function dualRange(prefix, label, min, max, low, high, suffix='') {
    const lo = Number.isFinite(Number(low)) ? Number(low) : min;
    const hi = Number.isFinite(Number(high)) ? Number(high) : max;
    const left = ((lo - min) / (max - min)) * 100;
    const right = ((hi - min) / (max - min)) * 100;
    return `<div class="followup-range-block" data-range-block="${prefix}">
      <div class="followup-range-head"><b>${escHtml(label)}</b><strong id="${prefix}RangeLabel">${lo}${suffix} – ${hi}${suffix}</strong></div>
      <div class="followup-dual-range">
        <span class="followup-dual-track"></span><span id="${prefix}RangeFill" class="followup-dual-fill" style="left:${left}%;width:${Math.max(0, right-left)}%"></span>
        <input id="${prefix}MinRange" aria-label="${escHtml(label)} von" type="range" min="${min}" max="${max}" value="${lo}">
        <input id="${prefix}MaxRange" aria-label="${escHtml(label)} bis" type="range" min="${min}" max="${max}" value="${hi}">
      </div>
    </div>`;
  }

  function groupedMarkup(groups, keyForGroup) {
    return Object.entries(groups).map(([group, values]) => {
      const key = keyForGroup(group);
      return `<div class="followup-filter-subtitle">${escHtml(group)}</div>${chips(key, values)}`;
    }).join('');
  }

  function renderExpandedFilter() {
    const state = window.state;
    const content = document.querySelector('#filterContent');
    if (!state?.filters || !content) return;
    const f = state.filters;
    f.selected ||= {};
    f.local ||= {photosOnly:true,videoOnly:false,likesOnly:false,imageMode:'ALL'};
    const ageMin = Number.isFinite(Number(f.ageMin)) ? Number(f.ageMin) : 18;
    const ageMax = Number.isFinite(Number(f.ageMax)) ? Number(f.ageMax) : 60;
    const heightMin = Number.isFinite(Number(f.heightMin)) ? Number(f.heightMin) : 140;
    const heightMax = Number.isFinite(Number(f.heightMax)) ? Number(f.heightMax) : 200;

    content.innerHTML = `
      <div class="filter-search-box">${icon('search')}<input id="filterSearchText" placeholder="Filter durchsuchen (z. B. Haarfarbe, Massage, Escort …)"></div>
      <div class="filter-grid followup-filter-grid">
        <div class="followup-top-grid">
          <div class="filter-card"><div class="filter-card-head"><b>${icon('map-pin')}Standort</b></div><input id="filterCity" class="compact-input" value="${escHtml(f.city || '')}"></div>
          <div class="filter-card"><div class="filter-card-head"><b>${icon('location')}Umkreis</b><strong id="filterRadiusLabel">${Number(f.radius || 20)} km</strong></div><input id="filterRadius" class="filter-range" type="range" min="1" max="100" value="${Number(f.radius || 20)}"><div class="range-labels" style="margin-left:0"><span>1 km</span><span>100 km</span></div></div>
        </div>

        <div class="filter-card full" data-followup-search="Alter Größe">
          <div class="filter-card-head"><b>${icon('calendar')}Alter &amp; Größe</b></div>
          <div class="followup-range-stack">
            ${dualRange('age', 'Alter', 18, 60, ageMin, ageMax)}
            ${dualRange('height', 'Größe', 140, 200, heightMin, heightMax, ' cm')}
          </div>
          <p class="followup-filter-note">Je Merkmal eine gemeinsame Von–Bis-Leiste mit zwei Griffen.</p>
        </div>

        <div class="filter-card full" data-followup-search="Geschlecht Busen Brustgröße wichtigste Merkmale">
          <div class="filter-card-head"><b>${icon('user')}Wichtigste Merkmale</b></div>
          <div class="followup-filter-subtitle">Geschlecht</div>${chips('geschlecht', FILTER_CATALOG.traits.Geschlecht)}
          <div class="followup-filter-subtitle">Busengröße</div>${chips('busen', FILTER_CATALOG.traits.Busen)}
        </div>

        <div class="filter-card full" data-followup-search="Besuche Treffpunkte Haus Hotel Büro Incall">
          <div class="filter-card-head"><b>${icon('home')}Besuche</b></div>
          ${chips('treff', FILTER_CATALOG.meeting.Treffpunkte)}
        </div>

        <div class="filter-card full" data-followup-search="Beliebte Services AV GV Girlfriend Sex">
          <div class="filter-card-head"><b>${icon('heart')}Beliebte Services</b></div>
          ${chips('service', ['AV', 'GV', 'Girlfriend-Sex'])}
        </div>

        <div class="filter-card full" data-followup-search="Bild KI Fotos Studio FaceApp">
          <div class="filter-card-head"><b>${icon('sparkles')}Bild-KI &amp; Medien</b></div>
          <div class="followup-image-modes">${[
            ['ALL','Alle Bilder'],['NATURAL','Ohne KI-Vermerk'],['AI','Mit KI-Vermerk'],['STUDIO','Studio'],['FACEAPP','FaceApp'],
          ].map(([value,label]) => `<button type="button" class="followup-image-mode ${f.local.imageMode===value?'active':''}" data-followup-image-mode="${value}">${escHtml(label)}</button>`).join('')}</div>
        </div>

        <div class="filter-card full" data-followup-search="Rubriken Kategorien Ladies Locations Escort TS TV Massage Bizarr SM FKK Swinger Telefonsex Onlineservice">
          <div class="filter-card-head"><b>${icon('tag')}Rubriken &amp; exklusive Inhalte</b></div>
          <div class="mini-chips">${FILTER_CATALOG.categories.map(([value,label]) => categoryChip(value,label)).join('')}</div>
        </div>

        <div class="filter-card full" data-followup-search="Figur Typ Intimbereich Hautton Haarfarbe Haarlänge Augenfarbe Körperschmuck Tattoos Nichtraucher Video Empfehlung">
          <div class="filter-card-head"><b>${icon('user')}Weitere Merkmale</b></div>
          ${groupedMarkup(Object.fromEntries(Object.entries(FILTER_CATALOG.traits).filter(([name]) => !['Geschlecht','Busen'].includes(name))), group => TRAIT_KEYS[group])}
        </div>

        <div class="filter-card full" data-followup-search="Services Französisch Anal Bizarr Massage Escort Begleitung Gruppen">
          <div class="filter-card-head"><b>${icon('heart')}Weitere Services</b></div>
          ${groupedMarkup(FILTER_CATALOG.services, () => 'service')}
        </div>

        <div class="filter-card full" data-followup-search="Treffen Erreichbarkeit Rollstuhl Öffnungszeiten Samstag Sonntag 22 Uhr">
          <div class="filter-card-head"><b>${icon('clock')}Treffen, Erreichbarkeit &amp; Zeiten</b></div>
          <div class="followup-filter-subtitle">Treffpunkte</div>${chips('treff', FILTER_CATALOG.meeting.Treffpunkte)}
          <div class="followup-filter-subtitle">Location</div>${chips('location', FILTER_CATALOG.meeting.Location)}
          <div class="followup-filter-subtitle">Zeiten</div>${chips('zeiten', FILTER_CATALOG.meeting.Zeiten)}
        </div>

        <div class="filter-card full" data-followup-search="Lokale Filter Fotos Video Likes">
          <div class="filter-card-head"><b>${icon('sliders')}Lokale Ergebnisfilter</b></div>
          <div class="followup-local-toggles">${localToggle('photosOnly','Nur mit Fotos','camera')}${localToggle('videoOnly','Nur mit Video','video')}${localToggle('likesOnly','Nur Likes','heart')}</div>
        </div>

        <div class="filter-default-actions"><button id="saveDefaultFilters" type="button">${icon('save')}Als Standard speichern</button><button id="deleteDefaultFilters" type="button" ${state.settings?.default_filters?'':'disabled'}>${icon('trash')}Standard löschen</button></div>
      </div>`;

    bindExpandedFilterControls();
    window.updateFilterCount?.();
  }

  function syncDualRange(prefix, minKey, maxKey, minLimit, maxLimit, source) {
    const state = window.state;
    const minEl = document.querySelector(`#${prefix}MinRange`);
    const maxEl = document.querySelector(`#${prefix}MaxRange`);
    const fill = document.querySelector(`#${prefix}RangeFill`);
    const label = document.querySelector(`#${prefix}RangeLabel`);
    if (!state?.filters || !minEl || !maxEl || !fill || !label) return;
    let low = Number(minEl.value);
    let high = Number(maxEl.value);
    if (low > high) {
      if (source === 'min') low = high;
      else high = low;
    }
    minEl.value = String(low);
    maxEl.value = String(high);
    state.filters[minKey] = low;
    state.filters[maxKey] = high;
    const left = ((low-minLimit)/(maxLimit-minLimit))*100;
    const right = ((high-minLimit)/(maxLimit-minLimit))*100;
    fill.style.left = `${left}%`;
    fill.style.width = `${Math.max(0,right-left)}%`;
    const suffix = prefix === 'height' ? ' cm' : '';
    label.textContent = `${low}${suffix} – ${high}${suffix}`;
  }

  function bindExpandedFilterControls() {
    const state = window.state;
    const root = document.querySelector('#filterContent');
    if (!state?.filters || !root) return;
    document.querySelector('#filterCity')?.addEventListener('input', event => { state.filters.city = event.target.value; });
    document.querySelector('#filterRadius')?.addEventListener('input', event => {
      state.filters.radius = Number(event.target.value);
      const label = document.querySelector('#filterRadiusLabel');
      if (label) label.textContent = `${state.filters.radius} km`;
    });

    document.querySelector('#ageMinRange')?.addEventListener('input', () => syncDualRange('age','ageMin','ageMax',18,60,'min'));
    document.querySelector('#ageMaxRange')?.addEventListener('input', () => syncDualRange('age','ageMin','ageMax',18,60,'max'));
    document.querySelector('#heightMinRange')?.addEventListener('input', () => syncDualRange('height','heightMin','heightMax',140,200,'min'));
    document.querySelector('#heightMaxRange')?.addEventListener('input', () => syncDualRange('height','heightMin','heightMax',140,200,'max'));

    root.querySelectorAll('[data-followup-filter-group]').forEach(button => button.addEventListener('click', () => {
      const group = button.dataset.followupFilterGroup;
      const value = button.dataset.followupFilterValue;
      state.filters.selected[group] ||= [];
      const values = state.filters.selected[group];
      const index = values.indexOf(value);
      if (index >= 0) values.splice(index, 1); else values.push(value);
      renderExpandedFilter();
    }));

    root.querySelectorAll('[data-followup-category]').forEach(button => button.addEventListener('click', () => {
      state.filters.category = button.dataset.followupCategory;
      renderExpandedFilter();
    }));

    root.querySelectorAll('[data-followup-image-mode]').forEach(button => button.addEventListener('click', () => {
      state.filters.local.imageMode = button.dataset.followupImageMode;
      renderExpandedFilter();
    }));

    root.querySelectorAll('[data-followup-local-toggle]').forEach(button => button.addEventListener('click', () => {
      const key = button.dataset.followupLocalToggle;
      state.filters.local[key] = !state.filters.local[key];
      renderExpandedFilter();
    }));

    document.querySelector('#filterSearchText')?.addEventListener('input', event => {
      const query = event.target.value.trim().toLocaleLowerCase('de-DE');
      root.querySelectorAll('.filter-card').forEach(card => {
        const haystack = `${card.textContent || ''} ${card.dataset.followupSearch || ''}`.toLocaleLowerCase('de-DE');
        card.classList.toggle('hidden', Boolean(query) && !haystack.includes(query));
      });
    });

    document.querySelector('#saveDefaultFilters')?.addEventListener('click', () => window.saveDefaultFilters?.());
    document.querySelector('#deleteDefaultFilters')?.addEventListener('click', () => window.deleteDefaultFilters?.());
  }

  function installFilterOverride() {
    window.renderFilter = renderExpandedFilter;
  }

  function rememberThreadFromClick(event) {
    const row = event.target.closest?.('#threadList [data-thread-id]');
    if (row?.dataset.threadId) currentChatThreadId = Number(row.dataset.threadId) || null;
    const profileContact = event.target.closest?.('#profileContent [data-contact]');
    if (profileContact?.dataset.messagingThread) currentChatThreadId = Number(profileContact.dataset.messagingThread) || currentChatThreadId;
  }

  async function resolveCurrentThread() {
    if (currentChatThreadId) {
      try { return await fetch(`/api/messaging/threads/${encodeURIComponent(currentChatThreadId)}?limit=1`).then(async response => response.ok ? response.json() : null); }
      catch { return null; }
    }
    const displayName = document.querySelector('#chatName')?.textContent?.trim();
    if (!displayName) return null;
    try {
      const response = await fetch('/api/messaging/threads?filter=ALL');
      if (!response.ok) return null;
      const data = await response.json();
      const matches = (data.items || []).filter(item => String(item.display_name || '').trim() === displayName);
      if (matches.length !== 1) return null;
      currentChatThreadId = Number(matches[0].thread_id) || null;
      if (!currentChatThreadId) return null;
      const detail = await fetch(`/api/messaging/threads/${encodeURIComponent(currentChatThreadId)}?limit=1`);
      return detail.ok ? detail.json() : null;
    } catch {
      return null;
    }
  }

  async function openProfileFromChat() {
    const layer = document.querySelector('#messagingChatLayer');
    if (!layer || layer.classList.contains('hidden')) return;
    const thread = await resolveCurrentThread();
    if (!thread?.person_id) {
      window.toast?.('Für diesen Chat ist noch kein Personenprofil zugeordnet.');
      return;
    }
    profileOpenedFromChat = true;
    document.body.classList.add('chat-profile-open');
    try {
      await window.openPerson?.(Number(thread.person_id));
      const profileLayer = document.querySelector('#profileLayer');
      const showTop = () => { if (profileOpenedFromChat && profileLayer && !profileLayer.classList.contains('hidden')) profileLayer.scrollTop = 0; };
      requestAnimationFrame(showTop);
      setTimeout(showTop, 80);
      setTimeout(showTop, 220);
      if (!profileLayer || profileLayer.classList.contains('hidden')) throw new Error('Profil konnte nicht geöffnet werden');
    } catch (error) {
      profileOpenedFromChat = false;
      document.body.classList.remove('chat-profile-open');
      window.toast?.(error?.message || 'Profil konnte nicht geöffnet werden');
    }
  }

  function decorateChatIdentity() {
    const avatar = document.querySelector('#chatAvatar');
    const identity = document.querySelector('#chatIdentity');
    if (avatar) {
      avatar.setAttribute('role', 'button');
      avatar.setAttribute('tabindex', '0');
      avatar.setAttribute('aria-label', 'Profil öffnen');
      avatar.setAttribute('title', 'Profil öffnen');
    }
    if (identity) {
      identity.setAttribute('aria-label', 'Profil öffnen');
      identity.setAttribute('title', 'Profil öffnen');
    }
  }

  document.addEventListener('click', event => {
    rememberThreadFromClick(event);
    const target = event.target.closest?.('#chatAvatar,#chatIdentity');
    if (!target) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    openProfileFromChat();
  }, true);

  document.addEventListener('keydown', event => {
    if (!event.target?.matches?.('#chatAvatar')) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    openProfileFromChat();
  }, true);

  document.addEventListener('click', event => {
    if (!profileOpenedFromChat || !event.target.closest?.('[data-profile-close]')) return;
    setTimeout(() => {
      profileOpenedFromChat = false;
      document.body.classList.remove('chat-profile-open');
      document.querySelector('#chatIdentity')?.focus({preventScroll:true});
    }, 0);
  }, true);

  const observer = new MutationObserver(() => decorateChatIdentity());
  observer.observe(document.body, {childList:true, subtree:true});
  decorateChatIdentity();
  installFilterOverride();
})();

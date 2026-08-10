// app.js — AMD Design System UI

let currentJobId = null;
let pollingTimer = null;

// Читаем max_pages из hero-статистики (рендерится сервером)
// Элемент hero-stat-num с лейблом «Страниц на домен»
function _getMaxPages() {
  const items = document.querySelectorAll('.hero-stats > div');
  for (const item of items) {
    const lbl = item.querySelector('.hero-stat-label');
    if (lbl && lbl.textContent.trim().toLowerCase().includes('страниц')) {
      const num = item.querySelector('.hero-stat-num');
      if (num) return parseInt(num.textContent) || 4;
    }
  }
  return 4;
}

// ── Тема ─────────────────────────────────────────────────────────────────────
function toggleTheme() {
  const html = document.documentElement;
  const isDark = html.getAttribute('data-theme') === 'dark';
  html.setAttribute('data-theme', isDark ? 'light' : 'dark');
  document.getElementById('themeIcon').textContent  = isDark ? '☀️' : '🌙';
  document.getElementById('themeLabel').textContent = isDark ? 'Светлая' : 'Тёмная';
}

// ── Табы ввода ────────────────────────────────────────────────────────────────
function setTab(tab) {
  document.getElementById('tab-text').style.display = tab === 'text' ? '' : 'none';
  document.getElementById('tab-file').style.display = tab === 'file' ? '' : 'none';
  document.getElementById('tab-btn-text').classList.toggle('active', tab === 'text');
  document.getElementById('tab-btn-file').classList.toggle('active', tab === 'file');
}

// ── Критерии ─────────────────────────────────────────────────────────────────
function toggleCrit(el) {
  if (el.dataset.disabled) return;
  const ch = el.querySelector('.crit-check');
  const isActive = el.classList.contains('active');
  ch.textContent = isActive ? '' : '✓';
  el.classList.toggle('active', !isActive);
}

// ── Запуск проверки ───────────────────────────────────────────────────────────
async function startCheck() {
  const formData = new FormData();

  const activeIsText = document.getElementById('tab-text').style.display !== 'none';
  if (activeIsText) {
    const text = document.getElementById('urls-textarea').value.trim();
    if (!text) { alert('Введите хотя бы один URL'); return; }
    formData.append('urls', text);
  } else {
    const file = document.getElementById('file-input').files[0];
    if (!file) { alert('Выберите файл'); return; }
    formData.append('file', file);
  }

  // Критерии из criterion-карточек
  document.querySelectorAll('.criterion[data-name]').forEach(el => {
    if (el.classList.contains('active')) formData.append(el.dataset.name, '1');
  });

  const usePw = document.querySelector('.criterion[data-name="use_playwright"]')?.classList.contains('active');

  document.getElementById('start-btn').disabled = true;
  document.getElementById('progress-block').style.display = 'block';
  document.getElementById('results-block').style.display = 'none';
  const pwNote = document.getElementById('pw-note');
  if (pwNote) pwNote.style.display = usePw ? 'inline-block' : 'none';
  setProgress(0, 0, 'Отправка запроса...');

  try {
    const resp = await fetch('/check', { method: 'POST', body: formData });
    const data = await resp.json();
    if (!resp.ok || data.error) { alert('Ошибка: ' + (data.error || resp.statusText)); resetUI(); return; }
    currentJobId = data.job_id;
    pollingTimer = setInterval(() => pollStatus(data.total), 2000);
  } catch(e) { alert('Ошибка соединения: ' + e.message); resetUI(); }
}

async function pollStatus(total) {
  if (!currentJobId) return;
  try {
    const resp = await fetch(`/status/${currentJobId}`);
    const data = await resp.json();
    if (!resp.ok) { clearInterval(pollingTimer); return; }
    setProgress(data.progress, data.total || total, data.current);
    if (data.results && data.results.length) renderCards(data.results);
    if (data.status === 'done' || data.status === 'error') {
      clearInterval(pollingTimer);
      document.getElementById('progress-block').style.display = 'none';
      renderCards(data.results || []);
      showResults(data.results || []);
    }
  } catch(e) { console.error('Polling:', e); }
}

function setProgress(done, total, cur) {
  const pct = total > 0 ? Math.round(done / total * 100) : 0;
  const bar = document.getElementById('progress-bar');
  bar.style.width = pct + '%';
  document.getElementById('progress-text').textContent = `Проверено ${done} из ${total} сайтов`;
  document.getElementById('current-url').textContent = cur ? `⏳ ${cur} (проверяю до ${_getMaxPages()} страниц)` : '';
}

// ── AMD Badges ────────────────────────────────────────────────────────────────
function badge(text, type) {
  // type: red | green | orange | cyan
  return `<span class="amd-badge ab-${type}">${esc(text)}</span>`;
}

// ── Карточка домена (AMD) ─────────────────────────────────────────────────────
function buildDomainCard(r, rowIndex) {
  // ── Колонки сводной строки
  // Политика
  let policyCell;
  if (r.policy_found) {
    policyCell = `<div class="d-ok">✓ Найдена</div>`;
    if (r.sections_missing && r.sections_missing.length)
      policyCell += `<div class="d-sub">нет разделов: ${r.sections_missing.length}</div>`;
    else
      policyCell += `<div class="d-sub">все разделы</div>`;
    if (!r.policy_in_footer)
      policyCell += `<div class="d-sub" style="color:var(--c-orange)">⚠ не в футере</div>`;
    if (r.policy_is_pdf)
      policyCell += `<div class="d-sub" style="color:var(--c-orange)">⚠ PDF</div>`;
  } else {
    policyCell = `<div class="d-err">✕ Не найдена</div><div class="d-sub">нет разделов: 11</div>`;
  }

  // Cookie
  let cookieCell;
  if (r.has_cookie_banner) {
    cookieCell = badge('✓ баннер', 'green');
    if (r.has_decline_button !== undefined)
      cookieCell += ' ' + (r.has_decline_button ? badge('✓ кнопка отказа', 'green') : badge('✕ кнопка отказа', 'red'));
  } else {
    cookieCell = badge('✕ баннер', 'red');
  }
  if (r.checked_by_default)
    cookieCell += ' ' + badge('⚠ галочка', 'orange');

  // Иностранные ресурсы
  let foreignCell;
  if (Array.isArray(r.foreign_resources) && r.foreign_resources.length)
    foreignCell = r.foreign_resources.map(s => badge(s, 'orange')).join(' ');
  else
    foreignCell = `<span style="color:var(--c-muted);font-size:.75rem">—</span>`;

  // Аналитика
  let analyticsCell;
  if (Array.isArray(r.analytics_systems) && r.analytics_systems.length)
    analyticsCell = `<div class="multi-col">${r.analytics_systems.map(s => badge(s, 'cyan')).join('')}</div>`;
  else
    analyticsCell = `<span style="color:var(--c-muted);font-size:.75rem">—</span>`;

  // Формы
  const formsCount = r.pd_forms_count || 0;
  const formsColor = formsCount > 0 ? 'var(--c-orange)' : 'var(--c-muted)';

  // Согласие
  let consentCell;
  const consent = r.consent_level || '—';
  if (consent === 'полное')        consentCell = badge(consent, 'green');
  else if (consent === 'частичное') consentCell = badge(consent, 'orange');
  else if (consent === 'отсутствует' || consent === 'нарушения') consentCell = badge(consent, 'red');
  else                              consentCell = badge(consent, 'cyan');

  // Риск — цвет домена
  const riskStr = r.risk || '';
  const domainColor = riskStr.includes('ВЫСОКИЙ') ? 'var(--c-red)'
    : riskStr.includes('СРЕДНИЙ') ? 'var(--c-orange)'
    : riskStr.includes('НИЗКИЙ')  ? 'var(--c-green)'
    : 'var(--c-cyan)';

  // ── Detail rows
  const detailRows = buildDetailRows(r, rowIndex);

  return `
<div class="domain-card">
  <div class="row-header">
    <div class="th-amd">Домен</div>
    <div class="th-amd">Политика ПД</div>
    <div class="th-amd">Cookie-баннер</div>
    <div class="th-amd">Иностр. ресурсы</div>
    <div class="th-amd">Аналитика</div>
    <div class="th-amd">Форм с ПД</div>
    <div class="th-amd">Согласие</div>
  </div>
  <div class="domain-data" onclick="toggleExpand(this.nextElementSibling.nextElementSibling, this.nextElementSibling)">
    <div>
      <a href="${esc(r.url)}" target="_blank" class="d-name" onclick="event.stopPropagation()" style="color:${domainColor}">${esc(r.domain || r.url)}</a>
      ${r.error && !r.error.includes('Playwright') ? `<div class="d-sub" style="color:var(--c-red)">${esc(r.error)}</div>` : ''}
      ${riskStr ? `<div class="d-sub" style="margin-top:2px">${esc(riskStr)}</div>` : ''}
      ${r.cms ? `<div style="margin-top:4px"><span class="amd-badge ab-cyan" style="font-size:.6rem;opacity:.85">⚙ ${esc(r.cms)}</span></div>` : ''}
    </div>
    <div class="d-policy">${policyCell}</div>
    <div>${cookieCell}</div>
    <div>${foreignCell}</div>
    <div>${analyticsCell}</div>
    <div style="font-family:var(--f-display);font-size:1.4rem;font-weight:500;color:${formsColor}">${formsCount}</div>
    <div>${consentCell}</div>
  </div>
  <div class="expand-toggle" onclick="toggleExpand(this.nextElementSibling, this)">
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
    Детали проверки — ${detailRows.count} строк
    <svg class="arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m6 9 6 6 6-6"/></svg>
  </div>
  <div class="detail-block">
    ${detailRows.html}
  </div>
</div>`;
}

// ── Детали домена ─────────────────────────────────────────────────────────────
function buildDetailRows(r, rowIndex) {
  const FINES = {
    foreign: 'до 500 000 ₽',
    cookie:  'до 300 000 ₽',
    policy:  'до 150 000 ₽',
    forms:   'до 100 000 ₽',
  };

  const rows = [];

  // Страницы
  if (r.pages_checked && r.pages_checked.length > 1) {
    const links = r.pages_checked.map((u, idx) =>
      `<span style="display:inline-flex;align-items:center;gap:4px;white-space:nowrap">` +
      `<span style="color:var(--c-muted);font-size:.65rem;font-family:monospace">${idx + 1}</span>` +
      `<a href="${esc(u)}" target="_blank" class="d-name" style="font-size:.75rem">${esc(u)}</a>` +
      `</span>`
    ).join(`<span style="color:var(--c-border);margin:0 4px">·</span>`);
    rows.push({ icon: '🔍', text: `Проверено страниц: <strong>${r.pages_checked.length}</strong><br><span style="display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin-top:4px">${links}</span>` });
  }

  // Политика
  if (!r.policy_found) {
    rows.push({ icon: '✕', cat: 'red', text: 'Политика обработки ПД не найдена на сайте', fine: FINES.policy });
  } else {
    rows.push({ icon: '✓', cat: 'green', text: `Политика найдена: <a href="${esc(r.policy_url)}" target="_blank" class="d-name" style="font-size:.78rem">${esc(r.policy_url)}</a> (${(r.policy_text_len||0).toLocaleString()} симв.)` });
    if (!r.policy_in_footer)
      rows.push({ icon: '⚠', cat: 'orange', text: 'Ссылка на политику отсутствует в футере сайта', fine: FINES.policy });
    if (r.policy_is_pdf)
      rows.push({ icon: '⚠', cat: 'orange', text: 'Политика в формате PDF — АС МПДн Роскомнадзора может не распарсить документ' });
    if (r.sections_missing && r.sections_missing.length)
      rows.push({ icon: '✕', cat: 'red', text: `В политике отсутствуют обязательные разделы по 152-ФЗ: <strong>${esc(r.sections_missing.join(', '))}</strong>`, fine: FINES.policy });
    else if (r.sections_found && r.sections_found.length)
      rows.push({ icon: '✓', cat: 'green', text: `Все ${r.sections_found.length} обязательных раздела по ст.14 152-ФЗ присутствуют` });
    if (!r.operator_found)
      rows.push({ icon: '⚠', cat: 'orange', text: 'Реквизиты оператора (ИНН/ОГРН/юр. адрес) не найдены в тексте политики', fine: FINES.policy });
    else
      rows.push({ icon: '✓', cat: 'green', text: 'Реквизиты оператора найдены в политике' });
  }

  // Аналитика и Cookie
  const hasSystems = Array.isArray(r.analytics_systems) && r.analytics_systems.length;
  const hasForeign = Array.isArray(r.foreign_resources) && r.foreign_resources.length;

  if (hasForeign)
    rows.push({ icon: '✕', cat: 'red', text: `Иностранные ресурсы без уведомления РКН (трансграничная передача IP-адресов): <strong>${esc(r.foreign_resources.join(', '))}</strong>`, fine: FINES.foreign });
  else if (hasSystems)
    rows.push({ icon: '✓', cat: 'green', text: 'Иностранных ресурсов не обнаружено' });

  if (hasSystems) {
    rows.push({ icon: 'ℹ', text: `Системы аналитики: ${r.analytics_systems.map(s => badge(s, 'cyan')).join(' ')}` });
    if (!r.has_cookie_banner)
      rows.push({ icon: '✕', cat: 'red', text: 'Cookie-баннер отсутствует при наличии трекеров — требуется получить согласие пользователя', fine: FINES.cookie });
    else {
      rows.push({ icon: '✓', cat: 'green', text: 'Cookie-баннер присутствует' });
      if (!r.has_decline_button)
        rows.push({ icon: '✕', cat: 'red', text: 'Отсутствует кнопка «Отказаться» / «Только необходимые» в cookie-баннере', fine: FINES.cookie });
      else
        rows.push({ icon: '✓', cat: 'green', text: 'Кнопка отказа от cookies присутствует' });
    }
    if (r.checked_by_default)
      rows.push({ icon: '✕', cat: 'red', text: 'Галочка согласия на cookie предустановлена по умолчанию — принудительное согласие запрещено', fine: FINES.cookie });
  } else {
    rows.push({ icon: '✓', cat: 'green', text: 'Системы аналитики не обнаружены' });
  }

  // Playwright
  if (r.playwright_used && Array.isArray(r.trackers_before_consent) && r.trackers_before_consent.length)
    rows.push({ icon: '✕', cat: 'red', text: `<strong>Трекеры загружаются ДО получения согласия</strong> (проверено Playwright): ${esc(r.trackers_before_consent.join(', '))}`, fine: FINES.foreign });
  else if (r.playwright_used)
    rows.push({ icon: '✓', cat: 'green', text: 'Трекеры не загружаются до получения согласия пользователя (проверено Playwright)' });

  if (r.playwright_used && !r.banner_visible)
    rows.push({ icon: '⚠', cat: 'orange', text: '«Спрятанный баннер»: cookie-баннер не отображается при первом посещении (проверено Playwright)', fine: FINES.cookie });

  // Формы
  const formsCount = r.pd_forms_count || 0;
  if (formsCount === 0) {
    rows.push({ icon: 'ℹ', text: 'Формы сбора персональных данных не обнаружены' });
  } else {
    rows.push({ icon: 'ℹ', text: `Обнаружено форм с полями ПД: <strong>${formsCount}</strong>` });

    // Детальная разбивка по каждой форме
    const forms = Array.isArray(r.pd_forms) && r.pd_forms.length ? r.pd_forms : null;
    if (forms) {
      forms.forEach((form, fi) => {
        // Идентификатор формы
        const idParts = [];
        if (form.form_id)     idParts.push(`id=<code style="color:var(--c-cyan);font-family:monospace;font-size:.8em">${esc(form.form_id)}</code>`);
        if (form.form_name)   idParts.push(`name=<code style="color:var(--c-cyan);font-family:monospace;font-size:.8em">${esc(form.form_name)}</code>`);
        if (form.form_action) idParts.push(`action=<code style="color:var(--c-muted);font-family:monospace;font-size:.75em">${esc(form.form_action)}</code>`);
        const formLabel = idParts.length
          ? `Форма ${fi + 1}: ${idParts.join(' · ')}`
          : `Форма ${fi + 1}: <span style="color:var(--c-muted);font-size:.78em">без id/name</span>`;

        // Поля формы
        const rawFields = form.pd_fields || [];
        const fieldNames = [...new Set(rawFields.map(f => {
          const mp = f.match(/placeholder=([^\]]+)/);
          if (mp && mp[1] && mp[1] !== '') return mp[1];
          const mn = f.match(/name=([^,\]]+)/);
          return mn ? mn[1] : f;
        }).filter(Boolean))];

        const fieldsHtml = fieldNames.length
          ? `<span style="color:var(--c-muted);font-size:.78em">Поля: ${esc(fieldNames.join(', '))}</span>`
          : '';

        rows.push({ icon: 'ℹ', text: `${formLabel}${fieldsHtml ? '<br>' + fieldsHtml : ''}` });
      });
    } else if (Array.isArray(r.pd_fields) && r.pd_fields.length) {
      // Fallback: старый формат без pd_forms
      const fieldNames = [...new Set(r.pd_fields.map(f => {
        const m = f.match(/placeholder=([^\]]+)/);
        return m ? m[1] : (f.match(/name=([^,\]]+)/) || [])[1] || f;
      }).filter(Boolean))];
      rows.push({ icon: 'ℹ', text: `Поля: ${esc(fieldNames.join(', '))}` });
    }

    if (r.any_text_consent)
      rows.push({ icon: '⚠', cat: 'orange', text: 'Согласие оформлено только текстом «Нажимая Отправить...» — необходим отдельный чекбокс согласия', fine: FINES.forms });
  }

  // Согласие
  const consent = r.consent_level || 'не проверено';
  if (formsCount > 0) {
    if (consent === 'полное')
      rows.push({ icon: '✓', cat: 'green', text: 'Согласие на обработку ПД оформлено корректно: чекбокс + обязательные реквизиты по 152-ФЗ' });
    else if (consent === 'частичное')
      rows.push({ icon: '⚠', cat: 'orange', text: `Согласие частичное: чекбокс есть, но не все реквизиты 152-ФЗ. Отсутствуют: <strong>${esc((r.missing_requisites||[]).join(', ') || '—')}</strong>`, fine: FINES.forms });
    else if (consent === 'отсутствует')
      rows.push({ icon: '✕', cat: 'red', text: 'Согласие на обработку персональных данных отсутствует — нет чекбокса рядом с формой', fine: FINES.forms });
    else if (consent && consent.includes('текстовое'))
      rows.push({ icon: '⚠', cat: 'orange', text: `Согласие только текстом — не соответствует 152-ФЗ: нужен отдельный чекбокс. Отсутствующие реквизиты: <strong>${esc((r.missing_requisites||[]).join(', ') || '—')}</strong>`, fine: FINES.forms });
    else if (consent === 'нарушения')
      rows.push({ icon: '✕', cat: 'red', text: `Нарушения в оформлении согласия: ${esc((r.consent_violations||[]).join('; '))}`, fine: FINES.forms });
    if (Array.isArray(r.consent_violations) && r.consent_violations.length)
      r.consent_violations.forEach(v => rows.push({ icon: '✕', cat: 'red', text: esc(v), fine: FINES.forms }));
  }

  // Скриншот
  // Скриншоты: показываем все страницы (если есть)
  const screenshotFiles = Array.isArray(r.screenshot_files) && r.screenshot_files.length
    ? r.screenshot_files
    : (r.screenshot_file ? [{ url: r.url || '', file: r.screenshot_file }] : []);

  if (screenshotFiles.length > 0) {
    screenshotFiles.forEach(ss => {
      const pageLabel = ss.url ? `<span style="color:var(--c-muted);font-size:.7rem">${esc(ss.url)}</span>` : '';
      rows.push({ isScreenshot: true, screenshotFile: ss.file, screenshotLabel: pageLabel });
    });
  }

  if (r.error && r.error.includes('Playwright'))
    rows.push({ icon: 'ℹ', text: `<span style="color:var(--c-muted)">${esc(r.error)}</span>` });

  // Строим HTML
  let idx = 1;
  let html = rows.map(row => {
    if (row.isScreenshot) {
      return `
<div class="screenshot-row">
  <div class="dr-index">${idx++}</div>
  <div class="dr-status">📸</div>
  <div class="ss-label">Скриншот${row.screenshotLabel ? ' — ' + row.screenshotLabel : ' страницы (Playwright)'}</div>
  <a href="/screenshots/${esc(row.screenshotFile)}" target="_blank" class="open-link-btn">
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
    Открыть
  </a>
</div>`;
    }

    const iconColor = row.cat === 'red' ? 'var(--c-red)'
      : row.cat === 'green' ? 'var(--c-green)'
      : row.cat === 'orange' ? 'var(--c-orange)'
      : 'var(--c-muted)';

    const fineHtml = row.fine
      ? `<span style="color:var(--c-red);font-size:.65rem;font-weight:600;white-space:nowrap;opacity:.85">${row.fine}</span>`
      : '';

    return `
<div class="detail-row">
  <div class="dr-index">${String(idx++).padStart(2,'0')}</div>
  <div class="dr-status" style="color:${iconColor}">${row.icon}</div>
  <div class="dr-body" style="flex:1">${row.text}${fineHtml ? `<br>${fineHtml}` : ''}</div>
  <div class="dr-chevron">›</div>
</div>`;
  }).join('');

  // Кнопка рекомендаций AI
  const ownerBlockId = `rec-owner-${rowIndex}`;
  const devBlockId   = `rec-dev-${rowIndex}`;

  html += `
<div class="screenshot-row" style="flex-wrap:wrap;gap:10px;align-items:flex-start">
  <div class="dr-index" style="margin-top:2px">${String(idx++).padStart(2,'0')}</div>
  <div class="dr-status" style="margin-top:2px">🤖</div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;flex:1">
    <button class="open-link-btn"
            style="border-color:var(--c-green);color:var(--c-green)"
            onclick="getRecommendations(event,'${esc(r.domain)}','${ownerBlockId}','owner')"
            id="btn-owner-${rowIndex}">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
      Рекомендации владельцу
    </button>
    <button class="open-link-btn"
            onclick="getRecommendations(event,'${esc(r.domain)}','${devBlockId}','dev')"
            id="btn-dev-${rowIndex}">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
      ТЗ разработчику
    </button>
    <span style="font-size:.7rem;color:var(--c-muted)">Персонализированные документы на основе результатов проверки</span>
  </div>
</div>

<div id="${ownerBlockId}" style="display:none;border-top:1px solid var(--c-border);padding:var(--sp-lg) var(--sp-xl);background:var(--c-cyan-soft)">
  <div style="font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--c-green);margin-bottom:var(--sp-sm)">
    📋 РЕКОМЕНДАЦИИ ДЛЯ ВЛАДЕЛЬЦА САЙТА
  </div>
  <div class="rec-text" style="white-space:pre-wrap;font-size:.85rem;line-height:1.7;color:var(--c-text)"></div>
  <div class="rec-actions" style="margin-top:var(--sp-md);display:none">
    <button class="open-link-btn" style="border-color:var(--c-green);color:var(--c-green)" onclick="downloadRec(event)">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      Скачать .docx (владелец)
    </button>
  </div>
</div>

<div id="${devBlockId}" style="display:none;border-top:1px solid var(--c-border);padding:var(--sp-lg) var(--sp-xl);background:rgba(0,194,222,.05)">
  <div style="font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--c-cyan);margin-bottom:var(--sp-sm)">
    🛠 ТЕХНИЧЕСКОЕ ЗАДАНИЕ ДЛЯ РАЗРАБОТЧИКА
  </div>
  <div class="rec-text" style="white-space:pre-wrap;font-size:.85rem;line-height:1.7;color:var(--c-text)"></div>
  <div class="rec-actions" style="margin-top:var(--sp-md);display:none">
    <button class="open-link-btn" onclick="downloadRec(event)">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      Скачать .docx (разработчик)
    </button>
  </div>
</div>`;

  return { html, count: idx - 1 };
}

// ── Рендер карточек ───────────────────────────────────────────────────────────
function renderCards(results) {
  const container = document.getElementById('results-tbody');
  container.innerHTML = '';
  results.forEach((r, i) => {
    const div = document.createElement('div');
    div.innerHTML = buildDomainCard(r, i);
    container.appendChild(div.firstElementChild);
  });
  const badge = document.getElementById('results-count-badge');
  if (badge) badge.textContent = `${results.length} ${pluralDomen(results.length)}`;
}

function pluralDomen(n) {
  if (n === 1) return 'ДОМЕН';
  if (n >= 2 && n <= 4) return 'ДОМЕНА';
  return 'ДОМЕНОВ';
}

// ── Expand/collapse ───────────────────────────────────────────────────────────
function toggleExpand(block, toggle) {
  const open = block.classList.toggle('open');
  if (toggle) toggle.classList.toggle('open', open);
}

// ── Итоги ────────────────────────────────────────────────────────────────────
function showResults(results) {
  document.getElementById('results-block').style.display = 'block';
  document.getElementById('start-btn').disabled = false;
  document.getElementById('stat-total').textContent  = results.length;
  document.getElementById('stat-high').textContent   = results.filter(r => (r.risk||'').includes('ВЫСОКИЙ')).length;
  document.getElementById('stat-medium').textContent = results.filter(r => (r.risk||'').includes('СРЕДНИЙ')).length;
  document.getElementById('stat-low').textContent    = results.filter(r => (r.risk||'').includes('НИЗКИЙ')).length;
  const btns = document.getElementById('download-btns');
  if (btns) btns.style.display = 'flex';
}

function downloadReport(fmt) {
  if (currentJobId) window.location.href = `/download/${currentJobId}/${fmt}`;
}

function resetForm() {
  clearInterval(pollingTimer); currentJobId = null;
  document.getElementById('results-block').style.display  = 'none';
  document.getElementById('progress-block').style.display = 'none';
  document.getElementById('urls-textarea').value = '';
  document.getElementById('file-input').value    = '';
  document.getElementById('results-tbody').innerHTML = '';
  document.getElementById('start-btn').disabled  = false;
  const btns = document.getElementById('download-btns');
  if (btns) btns.style.display = 'none';
}

function resetUI() {
  document.getElementById('start-btn').disabled = false;
  document.getElementById('progress-block').style.display = 'none';
}

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Рекомендации AI ───────────────────────────────────────────────────────────
const recIds = {};

async function getRecommendations(event, domain, blockId, docType) {
  event.stopPropagation();
  const btn   = event.target.closest('button') || event.target;
  const block = document.getElementById(blockId);
  if (!block) return;

  block.style.display = '';
  const textEl    = block.querySelector('.rec-text');
  const actionsEl = block.querySelector('.rec-actions');
  const dlBtn     = block.querySelector('.open-link-btn[onclick*="downloadRec"]');

  const labels = {
    owner: ['⏳ Генерация рекомендаций...', '✓ Рекомендации готовы'],
    dev:   ['⏳ Генерация ТЗ...',           '✓ ТЗ готово'],
  };
  const [loadingLabel, doneLabel] = labels[docType] || ['⏳ Генерация...', '✓ Готово'];

  textEl.textContent = loadingLabel;
  btn.disabled = true;
  btn.textContent = loadingLabel;

  let rec_id;
  try {
    const resp = await fetch(`/recommend/${currentJobId}/${encodeURIComponent(domain)}`,
      { method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({doc_type: docType}) });
    const data = await resp.json();
    if (!resp.ok || data.error) {
      textEl.textContent = '❌ Ошибка: ' + (data.error || resp.statusText);
      btn.disabled = false;
      btn.textContent = docType === 'owner' ? '📋 Рекомендации владельцу' : '🛠 ТЗ разработчику';
      return;
    }
    rec_id = data.rec_id;
    recIds[blockId] = rec_id;
    if (dlBtn) dlBtn.dataset.recId = rec_id;
  } catch(e) {
    textEl.textContent = '❌ Ошибка соединения: ' + e.message;
    btn.disabled = false;
    return;
  }

  textEl.textContent = '';
  let buffer = '';

  const es = new EventSource(`/recommend/stream/${rec_id}`);

  es.onmessage = (e) => {
    const chunk = e.data;
    if (chunk === '[DONE]') {
      es.close();
      btn.textContent = doneLabel;
      if (actionsEl) actionsEl.style.display = '';
      textEl.innerHTML = formatMarkdown(buffer);
      return;
    }
    if (chunk.startsWith('[ERROR]')) {
      es.close();
      textEl.textContent = '❌ ' + chunk.replace('[ERROR] ', '');
      btn.disabled = false; btn.textContent = '🔄 Повторить';
      return;
    }
    buffer += chunk.replace(/\\n/g, '\n');
    textEl.textContent = buffer;
    textEl.scrollTop = textEl.scrollHeight;
  };

  es.onerror = () => {
    es.close();
    if (!textEl.textContent.startsWith('❌')) {
      btn.disabled = false; btn.textContent = '🔄 Повторить';
    }
  };
}

function downloadRec(event) {
  event.stopPropagation();
  const dlBtn = event.target.closest('[onclick*="downloadRec"]') || event.target;
  const recId = dlBtn.dataset.recId;
  if (recId) window.location.href = `/recommend/download/${recId}`;
}

function formatMarkdown(text) {
  return esc(text)
    .replace(/^## (.+)$/gm, '<br><strong style="font-size:1rem;color:var(--c-cyan)">$1</strong><br>')
    .replace(/^### (.+)$/gm, '<strong style="color:var(--c-text)">$1</strong><br>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code style="background:rgba(255,255,255,.07);padding:1px 5px;border-radius:2px;font-family:monospace;font-size:.88em;color:var(--c-cyan)">$1</code>')
    .replace(/^---$/gm, '<hr style="border-color:var(--c-border);margin:8px 0">')
    .replace(/\n/g, '<br>');
}

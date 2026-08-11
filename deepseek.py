# deepseek.py — генерация рекомендаций через AI API
#
# doc_type: "owner"    — рекомендации для владельца сайта (деловой язык)
#           "dev"      — ТЗ для разработчика (технический язык + код)
#           "combined" — оба раздела в одном документе (устаревший режим)
#
# Лимит токенов ответа: config.py → DEEPSEEK_MAX_TOKENS (env AI_MAX_TOKENS).

import logging
from pathlib import Path

from openai import OpenAI, APIError, APIConnectionError

from config import (
    get_ai_config,
    DEEPSEEK_MAX_TOKENS,
    DEEPSEEK_TEMPERATURE,
    RUSSIAN_ANALYTICS,
    RESOURCE_DIR,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(RESOURCE_DIR) / "prompts"


# ── Вспомогательные загрузчики ────────────────────────────────────────────────

def _load_system_prompt() -> str:
    path = PROMPTS_DIR / "system_prompt.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return (
        "Ты — эксперт по 152-ФЗ «О персональных данных» и требованиям "
        "АС МПДн Роскомнадзора. Пишешь на русском языке. "
        "Отвечай структурированно по заданному шаблону."
    )


def _load_knowledge_base() -> str:
    kb_dir = PROMPTS_DIR / "knowledge"
    if not kb_dir.exists():
        return ""
    sections = []
    for f in sorted(kb_dir.glob("*.txt")):
        content = f.read_text(encoding="utf-8").strip()
        if content:
            sections.append("### " + f.stem.upper() + "\n\n" + content)
    return "\n\n---\n\n".join(sections)


def _get_client() -> OpenAI:
    cfg = get_ai_config()
    if not cfg["api_key"]:
        raise ValueError(
            "API-ключ не задан. Настройте в админ-панели (AI модель) или в .env: HUBRIS_API_KEY=sk-..."
        )
    return OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])


# ── Общий контекст аудита ─────────────────────────────────────────────────────

def _build_audit_context(result: dict) -> str:
    """Формирует блок с результатами аудита — общий для обоих промптов."""
    from datetime import datetime
    today = datetime.now().strftime("%d.%m.%Y")

    domain     = result.get("domain", "сайт")
    url        = result.get("url", "")
    risk       = result.get("risk", "—")
    cms        = result.get("cms", "")
    violations = result.get("violations") or []

    policy_lines = []
    if not result.get("policy_found"):
        policy_lines.append("Политика обработки ПД НЕ найдена на сайте")
    else:
        if not result.get("policy_in_footer"):
            policy_lines.append("Политика не размещена в футере сайта")
        if result.get("policy_is_pdf"):
            policy_lines.append("Политика в формате PDF (АС МПДн не парсит PDF)")
        ms = result.get("sections_missing") or []
        if ms:
            policy_lines.append(f"Отсутствующие разделы по 152-ФЗ: {', '.join(ms)}")
        if not result.get("operator_found"):
            policy_lines.append("Реквизиты оператора не найдены")

    cookie_lines = []
    systems  = result.get("analytics_systems") or []
    foreign  = result.get("foreign_resources") or []
    trackers = result.get("trackers_before_consent") or []
    russian_systems = [s for s in systems if s in RUSSIAN_ANALYTICS]
    foreign_systems = [s for s in systems if s not in RUSSIAN_ANALYTICS]
    # CDN/виджеты и т.п. — без дублирования имён уже указанных как аналитика
    foreign_other = [f for f in foreign if f not in systems]
    if systems:
        if russian_systems:
            cookie_lines.append(
                f"Системы аналитики (российские, данные хранятся в РФ, "
                f"НЕ трансграничная передача): {', '.join(russian_systems)}"
            )
        if foreign_systems:
            cookie_lines.append(
                f"Системы аналитики (иностранные, трансграничная передача): "
                f"{', '.join(foreign_systems)}"
            )
    if foreign_other:
        cookie_lines.append(
            f"Иностранные ресурсы (трансграничная передача данных): "
            f"{', '.join(foreign_other)}"
        )
    if not result.get("has_cookie_banner") and systems:
        cookie_lines.append("Cookie-баннер ОТСУТСТВУЕТ")
    elif result.get("has_cookie_banner") and not result.get("has_decline_button"):
        cookie_lines.append("Нет кнопки «Отказаться» в cookie-баннере")
    if result.get("checked_by_default"):
        cookie_lines.append("Галочка согласия предустановлена по умолчанию")
    if trackers:
        cookie_lines.append(f"Трекеры загружаются ДО согласия: {', '.join(trackers)}")
    if result.get("playwright_used") and not result.get("banner_visible"):
        cookie_lines.append("Спрятанный баннер — не виден при первом посещении")

    forms_lines = []
    forms_count = result.get("pd_forms_count") or 0
    consent     = result.get("consent_level") or "не проверено"
    if forms_count:
        forms_lines.append(f"Форм с ПД: {forms_count}")
        fields = result.get("pd_fields") or []
        if fields:
            forms_lines.append(f"Поля ПД: {', '.join(fields[:5])}")
        forms_lines.append(f"Согласие: {consent}")
        mr = result.get("missing_requisites") or []
        if mr:
            forms_lines.append(f"Отсутствующие реквизиты: {', '.join(mr)}")
        for v in (result.get("consent_violations") or []):
            forms_lines.append(f"Нарушение: {v}")

    max_fine = 0
    has_transborder = bool(foreign) or bool(foreign_systems)
    trackers_foreign = [t for t in trackers if t not in RUSSIAN_ANALYTICS]
    if has_transborder or trackers_foreign:
        max_fine = max(max_fine, 500_000)
    elif trackers:
        max_fine = max(max_fine, 300_000)  # только российские трекеры до согласия
    if not result.get("has_cookie_banner") and systems: max_fine = max(max_fine, 300_000)
    if result.get("has_cookie_banner") and not result.get("has_decline_button"): max_fine = max(max_fine, 300_000)
    if not result.get("policy_found"):max_fine = max(max_fine, 150_000)
    if result.get("sections_missing") or not result.get("operator_found"): max_fine = max(max_fine, 150_000)
    if forms_count and (consent in ("отсутствует", "нарушения") or "текстовое" in (consent or "")): max_fine = max(max_fine, 100_000)

    # Что соответствует требованиям — явный список для AI
    ok_lines = []
    if result.get("policy_found"):
        ok_lines.append("Политика обработки ПД найдена на сайте")
        if not result.get("sections_missing"):
            ok_lines.append("Все обязательные разделы политики по 152-ФЗ присутствуют")
        if result.get("operator_found"):
            ok_lines.append("Реквизиты оператора найдены в политике")
        if result.get("policy_in_footer"):
            ok_lines.append("Ссылка на политику размещена в футере")
    if result.get("has_cookie_banner"):
        ok_lines.append("Cookie-баннер присутствует")
    if result.get("has_decline_button"):
        ok_lines.append("Кнопка «Отказаться» в cookie-баннере присутствует")
    if not foreign and not systems:
        ok_lines.append("Иностранные ресурсы и системы аналитики не обнаружены")
    elif not foreign and not foreign_systems:
        ok_lines.append(
            "Иностранных ресурсов и иностранной аналитики нет "
            "(Яндекс.Метрика и др. российская аналитика — не трансграничная передача)"
        )
    if result.get("playwright_used") and not trackers:
        ok_lines.append("Трекеры не загружаются до получения согласия пользователя")

    nl = "\n"
    cms_line = f"\n**CMS / платформа сайта:** {cms}" if cms else "\n**CMS / платформа сайта:** не определена"
    return f"""Сегодняшняя дата: **{today}**. Используй именно эту дату. Не используй другие даты.

Проведён автоматический аудит сайта **{domain}** ({url}).{cms_line}

## РЕЗУЛЬТАТЫ АУДИТА

**Оценка риска:** {risk}
**Сумма возможных штрафов:** до {max_fine:,} ₽

### НАРУШЕНИЯ (только они требуют исправления):
{nl.join(f"- {v}" for v in violations) if violations else "- Нарушений не выявлено"}

### Политика обработки ПД:
{nl.join(f"- ❌ {s}" for s in policy_lines) if policy_lines else "- ✅ Соответствует требованиям"}

### Cookie-баннер и аналитика:
{nl.join(f"- ❌ {s}" for s in cookie_lines) if cookie_lines else "- ✅ Соответствует требованиям"}

### Формы и согласие:
{nl.join(f"- ❌ {s}" for s in forms_lines) if forms_lines else "- ✅ Соответствует требованиям или формы не найдены"}

### ЧТО УЖЕ СООТВЕТСТВУЕТ ТРЕБОВАНИЯМ (НЕ упоминать как нарушение):
{nl.join(f"- ✅ {s}" for s in ok_lines) if ok_lines else "- (нет подтверждённых соответствий)"}

---
⚠️ КРИТИЧЕСКИ ВАЖНО: Составляй документ СТРОГО на основе данных выше.
- ЗАПРЕЩЕНО упоминать нарушения которых нет в разделе НАРУШЕНИЯ.
- ЗАПРЕЩЕНО указывать «Отсутствие политики» если в разделе «Политика» стоит ✅.
- ЗАПРЕЩЕНО придумывать штрафы или нарушения которых нет в данных.
- Раздел «ЧТО УЖЕ СООТВЕТСТВУЕТ» — это факты. Не превращай их в нарушения."""


# ── Промпты ───────────────────────────────────────────────────────────────────

def _build_cms_cookie_section(result: dict) -> str:
    """
    Возвращает CMS-специфичные инструкции по установке куки-баннера
    и блокировке трекеров. Включает готовый рабочий код.
    """
    cms = result.get("cms", "")
    systems = result.get("analytics_systems") or []
    foreign = result.get("foreign_resources") or []
    needs_banner = (not result.get("has_cookie_banner") and (systems or foreign)) \
                   or result.get("trackers_before_consent")

    if not needs_banner:
        return ""

    # Универсальный JS-код баннера (работает на любой платформе)
    universal_banner = '''
### ГОТОВЫЙ КОД: Универсальный cookie-баннер (работает на любой CMS)

Вставь в `<head>` сайта **ПЕРВЫМ** скриптом — до любых скриптов аналитики:

```html
<!-- === COOKIE CONSENT BANNER (152-ФЗ) === -->
<script>
(function() {
  // Ключ в localStorage для хранения согласия
  var CONSENT_KEY = 'cookie_consent_152fz';
  var CONSENT_VAL = localStorage.getItem(CONSENT_KEY);

  // Если согласие уже дано — загружаем аналитику сразу
  if (CONSENT_VAL === 'accepted') {
    window.__ANALYTICS_ALLOWED = true;
    return;
  }
  // Если явно отказался — не загружаем аналитику
  if (CONSENT_VAL === 'declined') {
    window.__ANALYTICS_ALLOWED = false;
    return;
  }

  // Согласие ещё не получено — блокируем аналитику
  window.__ANALYTICS_ALLOWED = false;

  // Показываем баннер после загрузки DOM
  document.addEventListener('DOMContentLoaded', function() {
    var banner = document.getElementById('cookie-consent-banner');
    if (banner) banner.style.display = 'flex';
  });
})();

function cookieAccept() {
  localStorage.setItem('cookie_consent_152fz', 'accepted');
  document.getElementById('cookie-consent-banner').style.display = 'none';
  window.__ANALYTICS_ALLOWED = true;
  // Загружаем аналитику после согласия
  if (typeof loadAnalytics === 'function') loadAnalytics();
}

function cookieDecline() {
  localStorage.setItem('cookie_consent_152fz', 'declined');
  document.getElementById('cookie-consent-banner').style.display = 'none';
  window.__ANALYTICS_ALLOWED = false;
}
</script>

<!-- HTML баннера — вставь в <body> -->
<div id="cookie-consent-banner" style="display:none;position:fixed;bottom:0;left:0;right:0;
  background:#1a1a2e;color:#e0e0e0;padding:16px 24px;z-index:99999;
  flex-wrap:wrap;align-items:center;gap:12px;border-top:2px solid #00c2de;
  font-family:sans-serif;font-size:14px;line-height:1.5">
  <div style="flex:1;min-width:260px">
    Мы используем файлы cookie и счётчики статистики для улучшения работы сайта.
    Нажимая «Принять», вы соглашаетесь с
    <a href="/privacy" style="color:#00c2de">политикой обработки персональных данных</a>.
  </div>
  <div style="display:flex;gap:10px;flex-shrink:0">
    <button onclick="cookieDecline()"
      style="padding:9px 18px;border:1px solid #555;background:transparent;
             color:#aaa;border-radius:4px;cursor:pointer;font-size:14px">
      Только необходимые
    </button>
    <button onclick="cookieAccept()"
      style="padding:9px 22px;border:none;background:#00c2de;
             color:#000;border-radius:4px;cursor:pointer;font-size:14px;font-weight:600">
      Принять
    </button>
  </div>
</div>

<!-- Аналитика — загружается ТОЛЬКО после согласия -->
<script>
function loadAnalytics() {
  if (!window.__ANALYTICS_ALLOWED) return;
  // Сюда перенеси все скрипты аналитики из <head>:
  // ── Яндекс.Метрика ──
  // (function(m,e,t,r,i,k,a){...})(window,...);
  // ── Google Analytics ──
  // var s=document.createElement('script');s.src='...';document.head.appendChild(s);
}
// Если согласие уже было дано ранее — запускаем сразу
if (window.__ANALYTICS_ALLOWED) loadAnalytics();
</script>
<!-- === /COOKIE CONSENT BANNER === -->
```

**Критерий приёмки:** открыть сайт в режиме инкогнито → в DevTools → Network не должно быть запросов к аналитике до нажатия «Принять».
'''

    if not cms:
        return universal_banner

    cms_lower = cms.lower()

    if "tilda" in cms_lower:
        return universal_banner + '''
### УСТАНОВКА НА TILDA

**Важно:** Tilda загружает все скрипты аналитики через собственный загрузчик. Прямой контроль порядка загрузки невозможен через стандартные настройки.

**Шаг 1 — вставь код баннера:**
Настройки сайта → Ещё → HTML-код в `<head>` → вставь весь блок `<!-- COOKIE CONSENT BANNER -->` выше.

**Шаг 2 — отключи встроенную аналитику Tilda:**
Настройки сайта → Аналитика → **отключи** Яндекс.Метрику и Google Analytics.

**Шаг 3 — добавь аналитику через loadAnalytics():**
В функцию `loadAnalytics()` вставь коды счётчиков вручную (не через интерфейс Tilda).

**Шаг 4 — проверь:**
Сайт в режиме инкогнито → DevTools → Network → при первом открытии не должно быть запросов к mc.yandex.ru или google-analytics.com.
'''

    if "bitrix" in cms_lower:
        return universal_banner + '''
### УСТАНОВКА НА 1С-БИТРИКС

**Вариант A — встроенный модуль (рекомендуется):**
1. Административная панель → Marketplace → найди и установи компонент **«Политика использования cookies»** (bitrix.cookie.agreement)
2. Добавь компонент в шаблон сайта: `header.php` или `footer.php`
3. Настрой: Сайт → Настройки → Политика конфиденциальности

**Вариант B — ручная установка кода баннера:**
Открой `/bitrix/templates/НАЗВАНИЕ_ШАБЛОНА/header.php` и вставь код баннера в самом начале `<head>`.

**Блокировка аналитики в Битрикс:**
```php
// В header.php ПЕРЕД тегами аналитики:
<?php
$consent = $_COOKIE['cookie_consent_152fz'] ?? '';
$analytics_allowed = ($consent === 'accepted');
?>
<?php if ($analytics_allowed): ?>
<!-- Вставь сюда скрипты аналитики -->
<?php endif; ?>
```

**Важно:** компонент Google Analytics в Битрикс (bitrix.googleanalytics) нужно **отключить** в настройках сайта и подключать только через `loadAnalytics()`.
'''

    if "wordpress" in cms_lower:
        return universal_banner + '''
### УСТАНОВКА НА WORDPRESS

**Вариант A — плагин (рекомендуется для нетехнических пользователей):**
Установи плагин **Complianz** (бесплатный) или **CookieYes**:
Плагины → Добавить → поиск «Complianz» → Установить → Активировать → пройти мастер настройки.

**Важно при выборе плагина:** убедись что он использует режим «Script Blocker» — именно он блокирует загрузку аналитики до согласия. Просто «визуальный баннер» не соответствует 152-ФЗ.

**Вариант B — ручная установка кода баннера:**
```php
// В functions.php темы:
function add_cookie_banner_scripts() {
    // Вставляем баннер первым в head
    echo '<script>/* весь JS баннера */</script>';
}
add_action('wp_head', 'add_cookie_banner_scripts', 1); // приоритет 1 = первый

// Блокируем скрипты аналитики до согласия:
function maybe_dequeue_analytics() {
    $consent = $_COOKIE['cookie_consent_152fz'] ?? '';
    if ($consent !== 'accepted') {
        wp_dequeue_script('google-analytics');
        wp_dequeue_script('yandex-metrika');
        // добавь все handle-имена своих скриптов аналитики
    }
}
add_action('wp_enqueue_scripts', 'maybe_dequeue_analytics', 99);
```

**Критерий приёмки:** плагин Query Monitor → вкладка Scripts → при первом посещении не должно быть скриптов аналитики в очереди.
'''

    if "joomla" in cms_lower:
        return universal_banner + '''
### УСТАНОВКА НА JOOMLA

**Вариант A — расширение:**
Расширения → Manage → Install → найди **«Cookie Notice»** или **«JCookies»** на extensions.joomla.org.

**Вариант B — ручная установка:**
Открой шаблон: Расширения → Шаблоны → твой шаблон → index.php.
Вставь код баннера в `<head>` **до** любых других `<script>` тегов.

```php
// В index.php шаблона, перед подключением аналитики:
<?php
$consent = isset($_COOKIE['cookie_consent_152fz']) ? $_COOKIE['cookie_consent_152fz'] : '';
if ($consent === 'accepted'):
?>
<!-- Google Analytics, Яндекс.Метрика и т.п. -->
<?php endif; ?>
```
'''

    if "getcourse" in cms_lower:
        return universal_banner + '''
### УСТАНОВКА НА GETCOURSE

GetCourse имеет ограниченный доступ к коду страниц.

**Способ установки:**
Сайт → Дизайн → Редактор сайта → выбери нужный блок/виджет → HTML-редактор → вставь код баннера.

Либо: Настройки сайта → Дополнительный HTML в `<head>` → вставь JS-часть баннера.
HTML баннера добавь через блок «Произвольный HTML» на главной странице.

**Ограничение:** GetCourse не позволяет полностью заблокировать встроенные трекеры платформы. Зафиксируй этот факт в политике ПД как «технически необходимые cookies».
'''

    if "shopify" in cms_lower:
        return universal_banner + '''
### УСТАНОВКА НА SHOPIFY

**Вариант A — приложение:**
Shopify App Store → Cookie Consent Banner (например, «Pandectes GDPR»).

**Вариант B — ручная установка:**
Online Store → Themes → Edit code → `layout/theme.liquid` → вставь код в `<head>`.

```liquid
{% comment %} COOKIE CONSENT {% endcomment %}
{% unless customer.accepts_marketing %}
  <script>/* JS баннера */</script>
{% endunless %}
```

**Блокировка аналитики:**
В `layout/theme.liquid` оберни теги аналитики:
```liquid
{% if cookies.cookie_consent_152fz == 'accepted' %}
  {{ content_for_header }}
{% endif %}
```
'''

    if "webflow" in cms_lower:
        return universal_banner + '''
### УСТАНОВКА НА WEBFLOW

Project Settings → Custom Code → **Head Code** → вставь JS-часть баннера.
Project Settings → Custom Code → **Footer Code** → вставь HTML-баннера.

**Важно:** в Webflow Editor нельзя управлять порядком загрузки встроенных скриптов. Все сторонние интеграции (Google Analytics, Facebook Pixel) — убери из стандартных настроек Webflow и подключай ТОЛЬКО через функцию `loadAnalytics()` в кастомном коде.
'''

    if "wix" in cms_lower:
        return universal_banner + '''
### УСТАНОВКА НА WIX

**Встроенный инструмент:**
Wix Dashboard → Marketing & SEO → Cookie Consent Tool — настрой встроенный баннер.

**Важно:** встроенный инструмент Wix является «косметическим» и не блокирует реальную загрузку аналитики. Для соответствия 152-ФЗ используй сторонний скрипт:

Wix Dashboard → Settings → Custom Code → Add Custom Code → вставь код баннера в `<head>`, Load: **First**.

Все скрипты аналитики перенеси из стандартных настроек Wix в функцию `loadAnalytics()`.
'''

    # Для остальных CMS — универсальный код с общей инструкцией
    return universal_banner + f'''
### УСТАНОВКА НА {cms.upper()}

Найди в исходном коде файл шаблона/макета, который отвечает за вывод `<head>` на всех страницах сайта. Вставь код баннера **первым** в `<head>` — до любых скриптов аналитики.

Все скрипты счётчиков (Яндекс.Метрика, Google Analytics, Facebook Pixel и т.п.) перенеси из прямого подключения в функцию `loadAnalytics()`, которая вызывается только после получения согласия пользователя.

'''

def _build_prompt_owner(result: dict) -> str:
    """Рекомендации для владельца — деловой язык, без кода."""
    domain = result.get("domain", "сайт")
    cms    = result.get("cms", "")
    ctx    = _build_audit_context(result)
    kb     = _load_knowledge_base()
    kb_sec = f"\n\n---\n## СПРАВОЧНАЯ БАЗА ЗНАНИЙ\nИспользуй при расчёте штрафов. Не придумывай суммы — бери только отсюда.\n\n{kb}" if kb else ""
    cms_note = f"\n\nСайт работает на **{cms}**. Учти особенности этой платформы при формулировке рекомендаций владельцу." if cms else ""

    return f"""Ты — эксперт по 152-ФЗ и требованиям Роскомнадзора. Пиши на русском языке.

{ctx}
{cms_note}

---
ПРАВИЛО: Работай ТОЛЬКО с данными выше. Каждый пункт — строго из строк ❌ раздела НАРУШЕНИЯ.
Строки ✅ — уже соответствуют требованиям. Не превращай их в нарушения.
Запрещено придумывать нарушения которых нет в данных аудита.
---

Составь подробный документ **«Рекомендации для владельца сайта {domain}»**.
Язык — деловой, понятный руководителю. Без технического кода.
Владелец должен понять: что нарушено, почему это проблема, что заказать у разработчика или юриста, какой риск штрафа.

Структура:

# РЕКОМЕНДАЦИИ ДЛЯ ВЛАДЕЛЬЦА САЙТА {domain.upper()}
**Дата аудита:** [сегодняшняя дата]
**Статус:** [Требуется исправление / Соответствует требованиям]
{'**Платформа сайта:** ' + cms if cms else ''}

## КРАТКОЕ РЕЗЮМЕ
3–5 предложений. Объясни владельцу: что проверялось, что нашли, какой суммарный финансовый риск. Упомяни конкретный сайт и конкретные нарушения.

## НАРУШЕНИЯ И РЕКОМЕНДАЦИИ
Для каждого нарушения из строк ❌ — отдельный развёрнутый подраздел. Минимум 5–7 предложений на каждое нарушение.

### [Название нарушения]
**Что это значит для вашего бизнеса:**
Объясни простым языком что именно нарушено и почему это проблема для владельца сайта (2–3 предложения).

**Какие последствия:**
- Конкретный штраф по КоАП (статья, часть, сумма) — только из данных аудита
- Возможная проверка Роскомнадзором
- Репутационные риски при утечке или жалобе пользователя

**Что нужно сделать владельцу:**
Пошаговая инструкция: кому поручить (юрист / разработчик / сам владелец), что конкретно заказать, как проверить выполнение.

**Приоритет:** СРОЧНО / ВАЖНО / РЕКОМЕНДУЕТСЯ
**Срок:** конкретные сроки (например: в течение 2 недель)

Сортируй нарушения: СРОЧНО — первыми.

## ЧТО УЖЕ ВЫПОЛНЕНО ПРАВИЛЬНО
Перечисли пункты из строк ✅ выше с коротким объяснением почему это важно.

## ПЛАН ДЕЙСТВИЙ
Конкретный пронумерованный чек-лист с исполнителями и сроками.
Формат: **[N]. [Задача]** — Исполнитель: [кто], Срок: [когда], Приоритет: [СРОЧНО/ВАЖНО]

## ИТОГ
Сводная таблица: Нарушение | Штраф | Исполнитель | Срок | Статус
Последняя строка: итоговая сумма возможных штрафов.{kb_sec}"""


def _build_prompt_dev(result: dict) -> str:
    """ТЗ для разработчика — технический язык, с примерами кода."""
    domain  = result.get("domain", "сайт")
    cms     = result.get("cms", "")
    ctx     = _build_audit_context(result)
    kb      = _load_knowledge_base()
    kb_sec  = f"\n\n---\n## СПРАВОЧНАЯ БАЗА ЗНАНИЙ\n{kb}" if kb else ""
    cms_banner_sec = _build_cms_cookie_section(result)

    cms_header = f"\n**CMS / платформа:** {cms}" if cms else ""

    return f"""Ты — эксперт по веб-разработке и требованиям 152-ФЗ.

{ctx}

---
ПРАВИЛО: Работай ТОЛЬКО с данными выше. Задачи — только по строкам ❌.
Строки ✅ — уже сделано, задач по ним нет. Запрещено придумывать задачи которых нет в данных.
---

Составь документ **«Техническое задание разработчику»** для устранения нарушений на сайте {domain}.

Структура:

# ТЕХНИЧЕСКОЕ ЗАДАНИЕ: {domain.upper()}
**Дата:** [сегодняшняя дата]
**Исполнитель:** разработчик сайта{cms_header}

## ЗАДАЧИ

Для каждого нарушения блок:

### Задача [N]: [Название задачи]
**Приоритет:** КРИТИЧНО / ВЫСОКИЙ / СРЕДНИЙ
**Трудоёмкость:** [оценка в часах]

**Описание:**
Что именно нарушено технически и почему критично.

**Что изменить:**
Конкретные файлы, блоки, компоненты, настройки.

**Реализация:**
```html
<!-- Рабочий пример кода -->
```

**Критерий приёмки:**
Как проверить что задача выполнена (конкретный инструмент / шаг).

---

Пиши конкретно. Все примеры кода — рабочие, применимые к {cms if cms else 'WordPress / чистому HTML / PHP'}.

{cms_banner_sec}{kb_sec}"""


def _build_prompt(result: dict) -> str:
    """Обратная совместимость — оба раздела в одном промпте."""
    ctx = _build_audit_context(result)
    kb  = _load_knowledge_base()
    kb_sec = f"\n\n---\n## СПРАВОЧНАЯ БАЗА ЗНАНИЙ\n{kb}" if kb else ""

    return f"""Ты — эксперт по 152-ФЗ и требованиям Роскомнадзора.

{ctx}

---

Составь подробный документ, строго следуя структуре ниже.

## РАЗДЕЛ 1. РЕКОМЕНДАЦИИ ДЛЯ ВЛАДЕЛЬЦА САЙТА
Деловой язык, без кода. Для каждого нарушения: что нарушено, что сделать, приоритет (СРОЧНО / ВАЖНО / РЕКОМЕНДУЕТСЯ). Сортируй по убыванию приоритета.

## РАЗДЕЛ 2. ТЕХНИЧЕСКОЕ ЗАДАНИЕ ДЛЯ РАЗРАБОТЧИКА
Технический язык. Для каждого нарушения: задача, что изменить, пример кода, критерий приёмки.{kb_sec}"""


# ── Генерация ─────────────────────────────────────────────────────────────────

def _stream(result: dict, prompt: str):
    """Внутренний SSE-генератор для любого промпта."""
    try:
        client = _get_client()
    except ValueError as e:
        yield f"data: [ERROR] {e}\n\n"
        return

    try:
        logger.info("AI stream start for %s", result.get("domain"))
        stream = client.chat.completions.create(
            model=get_ai_config()["model"],
            messages=[
                {"role": "system", "content": _load_system_prompt()},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=DEEPSEEK_MAX_TOKENS,
            temperature=DEEPSEEK_TEMPERATURE,
            stream=True,
        )
        full_len = 0
        finish_reason = None
        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta.content:
                text = delta.content
                full_len += len(text)
                yield f"data: {text.replace(chr(10), chr(92) + 'n')}\n\n"
            # reasoning/thinking tokens (some models) — skip silently if present
            # ChoiceDelta in OpenAI SDK has no .reasoning; use getattr
            elif getattr(delta, "reasoning", None):
                pass

        if finish_reason == "length":
            logger.warning(
                "AI stream truncated (max_tokens=%s) for %s (%d chars) — "
                "увеличьте AI_MAX_TOKENS в .env",
                DEEPSEEK_MAX_TOKENS, result.get("domain"), full_len,
            )
        logger.info(
            "AI stream done for %s (%d chars, finish=%s, max_tokens=%s)",
            result.get("domain"), full_len, finish_reason, DEEPSEEK_MAX_TOKENS,
        )
        yield "data: [DONE]\n\n"

    except APIConnectionError as e:
        logger.error("AI connection error: %s", e)
        yield f"data: [ERROR] Ошибка соединения с AI API: {e}\n\n"
    except APIError as e:
        logger.error("AI API error: %s", e)
        yield f"data: [ERROR] Ошибка AI API: {e}\n\n"
    except Exception as e:
        logger.error("AI unexpected error: %s", e)
        yield f"data: [ERROR] Неожиданная ошибка: {e}\n\n"


def stream_recommendations(result: dict, doc_type: str = "combined"):
    """
    SSE-генератор рекомендаций.
    doc_type: "owner" | "dev" | "combined"
    """
    if doc_type == "owner":
        prompt = _build_prompt_owner(result)
    elif doc_type == "dev":
        prompt = _build_prompt_dev(result)
    else:
        prompt = _build_prompt(result)
    yield from _stream(result, prompt)


def get_recommendations_sync(result: dict, doc_type: str = "combined") -> str:
    """
    Синхронный вариант для генерации .docx.
    doc_type: "owner" | "dev" | "combined"
    """
    if doc_type == "owner":
        prompt = _build_prompt_owner(result)
    elif doc_type == "dev":
        prompt = _build_prompt_dev(result)
    else:
        prompt = _build_prompt(result)

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=get_ai_config()["model"],
            messages=[
                {"role": "system", "content": _load_system_prompt()},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=DEEPSEEK_MAX_TOKENS,
            temperature=DEEPSEEK_TEMPERATURE,
            stream=False,
        )
        if response.choices:
            return response.choices[0].message.content or ""
        return ""
    except Exception as e:
        logger.error("AI sync error: %s", e)
        raise

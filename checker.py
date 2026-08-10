# checker.py — проверка сайтов по критериям 152-ФЗ и требованиям АС МПДн РКН
#
# Архитектура:
#   1. requests + BeautifulSoup — базовая проверка (всегда)
#   2. Playwright               — углублённая проверка (опционально)
#      Добавляет: трекеры до согласия, видимость баннера, скриншот,
#                 JS-формы, динамически загружаемые элементы

import asyncio
import logging
import os
import random
import re
import time
import urllib.parse
import warnings

import requests
from bs4 import BeautifulSoup

from config import (
    ANALYTICS_SIGNATURES,
    CONSENT_MIXED_KEYWORDS,
    CONSENT_REQUISITES,
    COOKIE_BANNER_KEYWORDS,
    COOKIE_DECLINE_KEYWORDS,
    FOREIGN_RESOURCES,
    RUSSIAN_ANALYTICS,
    OPERATOR_KEYWORDS,
    PD_FIELD_KEYWORDS,
    POLICY_LINK_KEYWORDS,
    POLICY_REQUIRED_SECTIONS,
    POLICY_TEXT_KEYWORDS,
    PW_ACCEPT_TEXTS,
    PW_BANNER_SELECTORS,
    PW_DECLINE_TEXTS,
    PW_HEADLESS,
    PW_LOCALE,
    PW_TIMEOUT_MS,
    PW_TIMEZONE,
    PW_WAIT_AFTER_MS,
    REQUEST_TIMEOUT,
    REQUEST_VERIFY_SSL,
    SCREENSHOTS_DIR,
    SUBMIT_BUTTON_TEXTS,
    TEXT_CONSENT_PATTERNS,
    USER_AGENTS,
    should_skip_crawl_url,
)

warnings.filterwarnings("ignore", message="Unverified HTTPS request")
logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# Определение CMS
# ═════════════════════════════════════════════════════════════════════════════

# Каждая запись: (название_cms, [(тип_проверки, паттерн), ...])
# Типы: "html" — re.search по HTML, "header" — по заголовкам HTTP,
#       "url"  — по структуре URL/путям, "meta" — по <meta> тегам
_CMS_SIGNATURES: list[tuple[str, list[tuple[str, str]]]] = [
    # ── Битрикс — ПЕРВЫМ: высокий риск ложного определения Тильдой ──────────
    # «probe» = реальный HTTP-запрос к характерному пути
    ("Bitrix", [
        ("probe",  "/bitrix/"),                          # самый надёжный признак
        ("html",   r'/bitrix/js/|/bitrix/templates/|/bitrix/components/'),
        ("html",   r'bitrix_sessid|bx-composite-|BX\.message\(|BX\.ready\('),
        ("html",   r'data-bx-|bitrix\.infra'),
        ("header", r'X-Powered-CMS.*Bitrix|Powered-By.*Bitrix'),
    ]),
    # ── Тильда — только строгие признаки ──────────────────────────────────
    # tildacdn.com сам по себе НЕ признак: Битрикс-сайты часто
    # используют Tilda-виджеты или подгружают контент с tildacdn.com
    ("Tilda", [
        ("html",   r'tilda-publish|tilda\.ws/page'),
        ("html",   r'data-record-type="\d+"|t-records|t-body\b|t-cover\b'),
        ("meta",   r'tilda\.cc|tilda\.ws'),
        ("html",   r'zero\.tilda\.ws|tildacdn\.com/tild'),
    ]),
    ("WordPress", [
        ("html",   r'/wp-content/|/wp-includes/|wp-json'),
        ("html",   r'wordpress|wp-embed\.min\.js'),
        ("header", r'WordPress'),
    ]),
    ("Joomla", [
        ("html",   r'/media/jui/|Joomla!|com_content|option=com_'),
        ("header", r'Joomla'),
    ]),
    ("Drupal", [
        ("html",   r'Drupal\.settings|drupal\.js|/sites/default/files/'),
        ("header", r'X-Generator.*Drupal|Drupal'),
    ]),
    ("OpenCart", [
        ("html",   r'catalog/view/javascript|route=common/home|OpenCart'),
        ("html",   r'/index\.php\?route='),
    ]),
    ("Shopify", [
        ("html",   r'cdn\.shopify\.com|Shopify\.theme|myshopify\.com'),
        ("header", r'X-ShopId|Shopify'),
    ]),
    ("Wix", [
        ("html",   r'wix\.com|wixstatic\.com|_wixCIDX'),
        ("html",   r'X-Wix-Published-Version'),
    ]),
    ("Weebly", [
        ("html",   r'weebly\.com|editmysite\.com'),
    ]),
    ("ModX", [
        ("html",   r'manager/min/|assets/components/|class="modx'),
        ("header", r'MODx|MODX'),
    ]),
    ("Webflow", [
        ("html",   r'webflow\.com|data-wf-page|data-wf-site'),
        ("header", r'Webflow'),
    ]),
    ("Ghost", [
        ("html",   r'ghost\.io|content\.ghost\.org'),
        ("header", r'X-Ghost-Cache|Ghost'),
    ]),
    ("Readymag", [
        ("html",   r'readymag\.com|rmag\.co'),
    ]),
    ("LP-платформа / конструктор", [
        ("html",   r'lpgenerator\.ru|lp\.lpgenerator|lptracker'),
        ("html",   r'platformlp\.ru'),
    ]),
    ("GetCourse", [
        ("html",   r'getcourse\.ru|GC\.application|gc-header'),
    ]),
    ("AmoCRM / Сайты", [
        ("html",   r'amocrm\.ru|amoforms\.com'),
    ]),
]

# Куки и JS-признаки платформ (дополнительный сигнал)
_CMS_COOKIE_HINTS = {
    "Bitrix":    ["BITRIX_SM_LOGIN", "bitrix_sessid"],
    "WordPress": ["wordpress_logged_in", "wp-settings-"],
    "Joomla":    ["joomla_user_state"],
    "GetCourse": ["gc_session"],
}


def detect_cms(html: str, headers: dict, url: str = "",
               session: "requests.Session | None" = None) -> str:
    """
    Определяет CMS/платформу сайта по HTML, HTTP-заголовкам, URL
    и опциональным probe-запросам к характерным путям.
    Возвращает название CMS или пустую строку если не определена.
    """
    html_lower = html.lower() if html else ""

    # Базовый URL для probe-запросов
    from urllib.parse import urlparse
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""

    for cms_name, checks in _CMS_SIGNATURES:
        for check_type, pattern in checks:
            try:
                if check_type == "probe" and session and base_url:
                    # HEAD-запрос к характерному пути (например /bitrix/)
                    probe_url = base_url.rstrip("/") + pattern
                    try:
                        r = session.head(probe_url, timeout=5,
                                         verify=False, allow_redirects=True)
                        if r.status_code in (200, 301, 302, 403):
                            # 403 тоже считается — /bitrix/ существует, но закрыт
                            logger.debug("CMS detected via probe %s: %s (HTTP %s)",
                                         cms_name, probe_url, r.status_code)
                            return cms_name
                    except Exception as probe_err:
                        logger.debug("Probe failed %s: %s", probe_url, probe_err)
                    continue

                elif check_type == "html" and html_lower:
                    if re.search(pattern, html_lower, re.IGNORECASE):
                        logger.debug("CMS detected via html: %s (%s)", cms_name, pattern[:40])
                        return cms_name

                elif check_type == "header":
                    header_str = " ".join(f"{k}: {v}" for k, v in headers.items())
                    if re.search(pattern, header_str, re.IGNORECASE):
                        logger.debug("CMS detected via header: %s", cms_name)
                        return cms_name

                elif check_type == "meta" and html_lower:
                    meta_match = re.search(
                        r'<meta[^>]+name=["\']generator["\'][^>]*content=["\']([^"\']+)["\']',
                        html_lower, re.IGNORECASE
                    )
                    if meta_match and re.search(pattern, meta_match.group(1), re.IGNORECASE):
                        logger.debug("CMS detected via meta: %s", cms_name)
                        return cms_name

                elif check_type == "url" and url:
                    if re.search(pattern, url, re.IGNORECASE):
                        return cms_name

            except re.error:
                continue

    return ""

# ── Типы <input> ───────────────────────────────────────────────────────────────
PD_INPUT_TYPES     = {"tel", "email"}
NON_PD_INPUT_TYPES = {"submit", "button", "reset", "hidden",
                       "image", "file", "checkbox", "radio"}

# ── Дополнительные ключевые слова имён полей ──────────────────────────────────
_EXTRA_FIELD_KW = [
    "your-name", "your-phone", "your-email", "your-tel",
    "user_name", "user_phone", "user_email", "user_login",
    "uf_phone", "uf_email", "uf_name",
    "first_name", "last_name", "phone_number", "phone_num",
    "user-name", "user-phone", "user-email",
    "mobile", "cell", "cellphone", "fio", "fullname", "full_name",
    "patronymic", "middle_name",
    "ваш телефон", "ваше имя", "ваш email", "ваш e-mail",
    "ваша почта", "ваш номер", "как вас зовут",
    "введите имя", "введите телефон", "введите email",
    "номер телефона", "имя и фамилия", "ваши данные",
    "ваша фамилия", "ваш город", "ваш адрес",
]

# ── Проверяем доступность Playwright ─────────────────────────────────────────
# Широкий except — на Windows Playwright может падать не с ImportError,
# а с OSError, NotImplementedError или другим исключением при импорте.
PLAYWRIGHT_AVAILABLE = False
_pw_unavailable_reason = ""
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError as e:
    _pw_unavailable_reason = f"ImportError: {e}"
except Exception as e:
    _pw_unavailable_reason = f"{type(e).__name__}: {e}"

if not PLAYWRIGHT_AVAILABLE:
    logger.warning("Playwright недоступен: %s", _pw_unavailable_reason)


# ═════════════════════════════════════════════════════════════════════════════
# Вспомогательные функции
# ═════════════════════════════════════════════════════════════════════════════

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    s.max_redirects = 5
    return s


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if not re.match(r"https?://", url, re.IGNORECASE):
        url = "https://" + url
    return url


def fetch_page(session: requests.Session, url: str) -> tuple[int, str, str]:
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT,
                        verify=REQUEST_VERIFY_SSL, allow_redirects=True)
        return r.status_code, r.text, r.url
    except requests.exceptions.SSLError as e:
        logger.warning("SSL %s: %s", url, e)
    except requests.exceptions.Timeout:
        logger.warning("Timeout %s", url)
    except requests.exceptions.ConnectionError as e:
        logger.warning("Conn %s: %s", url, e)
    except Exception as e:
        logger.error("Error %s: %s", url, e)
    return 0, "", url


def fetch_page_with_headers(session: requests.Session, url: str) -> tuple[int, str, str, dict]:
    """Как fetch_page, но дополнительно возвращает HTTP-заголовки ответа."""
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT,
                        verify=REQUEST_VERIFY_SSL, allow_redirects=True)
        return r.status_code, r.text, r.url, dict(r.headers)
    except requests.exceptions.SSLError as e:
        logger.warning("SSL %s: %s", url, e)
    except requests.exceptions.Timeout:
        logger.warning("Timeout %s", url)
    except requests.exceptions.ConnectionError as e:
        logger.warning("Conn %s: %s", url, e)
    except Exception as e:
        logger.error("Error %s: %s", url, e)
    return 0, "", url, {}


def _has_any(text: str, keywords: list) -> bool:
    tl = text.lower()
    return any(kw.lower() in tl for kw in keywords)


def _all_pd_keywords() -> list:
    return list(PD_FIELD_KEYWORDS) + _EXTRA_FIELD_KW


# ═════════════════════════════════════════════════════════════════════════════
# PLAYWRIGHT — углублённая проверка (асинхронная)
# ═════════════════════════════════════════════════════════════════════════════

async def _pw_check_async(url: str, screenshots_dir: str) -> dict:
    """
    Playwright-проверка в чистом браузерном контексте (без кэша/cookies).

    Что делает:
    1. Открывает страницу как новый пользователь (инкогнито)
    2. Перехватывает ВСЕ сетевые запросы до клика «Принять»
    3. Кликает «Принять» → перехватывает запросы после
    4. Фиксирует: какие трекеры загрузились ДО согласия (нарушение)
    5. Делает скриншот страницы
    6. Парсит HTML после выполнения JS (ловит динамические формы)
    """
    result = {
        "playwright_used":          True,
        "screenshot_file":          "",
        "banner_visible":           False,
        "accept_btn_found":         False,
        "decline_btn_found":        False,
        "trackers_before_consent":  [],   # грузились ДО клика «Принять»
        "trackers_after_consent":   [],   # появились ПОСЛЕ клика
        "pd_forms_count_js":        0,    # формы найденные после рендера JS
        "pw_html":                  "",   # полный HTML страницы после рендера JS
        "pw_error":                 "",
    }

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=PW_HEADLESS)
            context = await browser.new_context(
                locale=PW_LOCALE,
                timezone_id=PW_TIMEZONE,
                # Чистый контекст — эмуляция первого визита
            )
            page = await context.new_page()

            # ── Перехват сетевых запросов ─────────────────────────────────
            pre_requests:  list[str] = []
            post_requests: list[str] = []
            consent_clicked = [False]

            def _on_request(req):
                if not consent_clicked[0]:
                    pre_requests.append(req.url)
                else:
                    post_requests.append(req.url)

            page.on("request", _on_request)

            # ── Навигация ─────────────────────────────────────────────────
            try:
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=PW_TIMEOUT_MS)
                await page.wait_for_timeout(PW_WAIT_AFTER_MS)
            except Exception as e:
                logger.warning("PW navigate %s: %s", url, e)

            # ── Скриншот ─────────────────────────────────────────────────
            domain = urllib.parse.urlparse(url).netloc.replace(".", "_")
            ts     = int(time.time())
            fname  = f"{domain}_{ts}.png"
            fpath  = os.path.join(screenshots_dir, fname)
            try:
                # Прокручиваем страницу до конца чтобы активировать lazy-load
                await page.evaluate("""async () => {
                    await new Promise(resolve => {
                        let total = 0;
                        const step = () => {
                            window.scrollBy(0, 600);
                            total += 600;
                            if (total < document.body.scrollHeight) {
                                setTimeout(step, 120);
                            } else {
                                window.scrollTo(0, 0);
                                setTimeout(resolve, 300);
                            }
                        };
                        step();
                    });
                }""")
                await page.screenshot(path=fpath, full_page=True)
                result["screenshot_file"] = fname
            except Exception as e:
                logger.warning("PW screenshot %s: %s", url, e)

            # ── Cookie-баннер виден? ──────────────────────────────────────
            for sel in PW_BANNER_SELECTORS:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        result["banner_visible"] = True
                        break
                except Exception:
                    pass

            # ── Поиск кнопки «Принять» и клик ────────────────────────────
            for text in PW_ACCEPT_TEXTS:
                if result["accept_btn_found"]:
                    break
                try:
                    btn = page.get_by_text(
                        re.compile(rf"\b{re.escape(text)}\b", re.IGNORECASE)
                    ).first
                    if await btn.is_visible(timeout=800):
                        result["accept_btn_found"] = True
                        consent_clicked[0] = True
                        await btn.click()
                        await page.wait_for_timeout(1500)
                except Exception:
                    pass

            # ── Поиск кнопки «Отказаться» ────────────────────────────────
            for text in PW_DECLINE_TEXTS:
                try:
                    btn = page.get_by_text(
                        re.compile(rf"\b{re.escape(text)}\b", re.IGNORECASE)
                    ).first
                    if await btn.is_visible(timeout=500):
                        result["decline_btn_found"] = True
                        break
                except Exception:
                    pass

            # ── Анализ запросов до согласия ───────────────────────────────
            def _classify_requests(req_list: list[str]) -> list[str]:
                """Возвращает имена систем/ресурсов по URL запросов."""
                found = []
                for req_url in req_list:
                    for name, sigs in {**ANALYTICS_SIGNATURES,
                                       **FOREIGN_RESOURCES}.items():
                        if sigs and any(s.lower() in req_url.lower()
                                        for s in sigs):
                            label = f"{name}"
                            if label not in found:
                                found.append(label)
                return found

            result["trackers_before_consent"] = _classify_requests(pre_requests)
            result["trackers_after_consent"]  = _classify_requests(post_requests)

            # ── JS-рендер: ждём завершения JS, сохраняем HTML ───────────
            try:
                # Дополнительное ожидание — JS-формы могут рендериться позже
                await page.wait_for_timeout(1500)
                html_js = await page.content()
                result["pw_html"] = html_js
                logger.info("PW html_js length: %d bytes for %s", len(html_js), url)
                js_forms = check_forms(html_js)
                result["pd_forms_count_js"] = js_forms["pd_forms_count"]
                logger.info("PW forms found: %d for %s", js_forms["pd_forms_count"], url)

                # ── Проверяем checked через JS-свойство, а не HTML-атрибут ──
                # page.content() сериализует DOM и может выдать checked=""
                # даже для визуально не отмеченных чекбоксов (атрибут ≠ свойство).
                # Истинное состояние только через element.checked (JS-свойство).
                try:
                    checked_ids = await page.evaluate("""() => {
                        const cbs = Array.from(
                            document.querySelectorAll('input[type="checkbox"]')
                        );
                        return cbs
                            .filter(cb => cb.checked)   // .checked = JS-свойство
                            .map(cb => cb.id || cb.name || '__unnamed__');
                    }""")
                    result["pw_checked_cb_ids"] = checked_ids
                    logger.info("PW checked checkboxes: %s for %s", checked_ids, url)
                except Exception as e:
                    result["pw_checked_cb_ids"] = []
                    logger.warning("PW checked eval error %s: %s", url, e)

            except Exception as e:
                logger.error("PW content() error %s: %s", url, e)
                result["pw_html"] = ""
                result["pw_checked_cb_ids"] = []

            await context.close()
            await browser.close()

    except Exception as e:
        result["pw_error"]        = str(e)[:300]
        result["playwright_used"] = False
        logger.error("PW error %s: %s", url, e)

    return result


def playwright_check(url: str) -> dict:
    """
    Синхронная обёртка для _pw_check_async.
    Безопасно вызывается из threading.Thread (нет активного event loop).
    """
    empty = {
        "playwright_used":         False,
        "screenshot_file":         "",
        "banner_visible":          False,
        "accept_btn_found":        False,
        "decline_btn_found":       False,
        "trackers_before_consent": [],
        "trackers_after_consent":  [],
        "pd_forms_count_js":       0,
        "pw_html":                 "",
        "pw_checked_cb_ids":       [],
        "pw_error":                "Playwright не установлен",
    }

    if not PLAYWRIGHT_AVAILABLE:
        return empty

    try:
        return asyncio.run(_pw_check_async(url, SCREENSHOTS_DIR))
    except Exception as e:
        logger.error("PW wrapper %s: %s", url, e)
        empty["pw_error"] = str(e)[:200]
        return empty


# ═════════════════════════════════════════════════════════════════════════════
# Критерий 1: Политика обработки ПД
# ═════════════════════════════════════════════════════════════════════════════

def check_policy(session: requests.Session, base_url: str, html: str) -> dict:
    """
    Проверяет: ссылку на политику, доступность, 11 разделов 152-ФЗ,
    PDF-формат, реквизиты оператора, наличие в футере.
    """
    result = {
        "found":            False,
        "policy_url":       "",
        "policy_status":    0,
        "policy_text_len":  0,
        "policy_is_pdf":    False,
        "policy_in_footer": False,
        "sections_found":   [],
        "sections_missing": list(POLICY_REQUIRED_SECTIONS.keys()),
        "operator_found":   False,
        "keywords_found":   [],
    }

    soup = BeautifulSoup(html, "lxml")
    policy_link = None

    # Ключевые слова только для текста ссылки (широкий набор)
    POLICY_TEXT_KW = [
        "политика конфиденциальности",
        "политика обработки персональных данных",
        "политика обработки",
        "обработка персональных данных",
        "персональные данные",
        "конфиденциальность",
        "политика пд",
        "политика пользователя",
        "privacy policy",
        "privacy",
    ]
    # Ключевые слова только для href (транслитерация, URL-slug)
    POLICY_HREF_KW = [
        "politika-pd", "politika_pd",
        "politika-konfidencialnosti", "politika-konfidentsialnosti",
        "privacy-policy", "personal-data", "personalnye-dannye",
        "privacy", "policy", "conf", "politika",
        "/pd/",
    ]

    def _is_policy_link(a_tag) -> bool:
        text = a_tag.get_text(strip=True).lower()
        href = a_tag["href"].lower()
        return (
            any(kw in text for kw in POLICY_TEXT_KW)
            or any(kw in href for kw in POLICY_HREF_KW)
        )

    # Приоритет — футер (РКН требует ссылку именно там)
    footer = soup.find("footer")
    if footer:
        for a in footer.find_all("a", href=True):
            if _is_policy_link(a):
                policy_link = a["href"]
                result["policy_in_footer"] = True
                break

    # Если не в футере — ищем по всей странице
    if not policy_link:
        for zone in [soup.find("header"), soup.find("nav"), soup]:
            if not zone:
                continue
            for a in zone.find_all("a", href=True):
                if _is_policy_link(a):
                    policy_link = a["href"]
                    break
            if policy_link:
                break

    if not policy_link:
        return result

    # PDF — предупреждение (АС МПДн не парсит PDF)
    if policy_link.lower().endswith(".pdf") or ".pdf" in policy_link.lower():
        result["policy_is_pdf"] = True
        result["policy_url"]    = urllib.parse.urljoin(base_url, policy_link)
        result["found"]         = True
        return result

    policy_url = urllib.parse.urljoin(base_url, policy_link)
    status, policy_html, final_url = fetch_page(session, policy_url)

    if status != 200 or not policy_html:
        result["policy_url"]    = policy_url
        result["policy_status"] = status
        return result

    text       = BeautifulSoup(policy_html, "lxml").get_text(" ", strip=True)
    text_lower = text.lower()
    found_kw   = [kw for kw in POLICY_TEXT_KEYWORDS if kw in text_lower]

    # 11 разделов
    sections_found   = []
    sections_missing = []
    for name, kws in POLICY_REQUIRED_SECTIONS.items():
        (sections_found if any(kw.lower() in text_lower for kw in kws)
         else sections_missing).append(name)

    result.update({
        "found":            len(found_kw) >= 3,
        "policy_url":       final_url,
        "policy_status":    status,
        "policy_text_len":  len(text),
        "sections_found":   sections_found,
        "sections_missing": sections_missing,
        "operator_found":   _has_any(text, OPERATOR_KEYWORDS),
        "keywords_found":   found_kw,
    })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# Критерий 2: Метрика, cookies и иностранные ресурсы
# ═════════════════════════════════════════════════════════════════════════════

def check_analytics(html: str) -> dict:
    """
    Статический анализ HTML:
    - системы аналитики
    - иностранные ресурсы (трансграничная передача)
    - cookie-баннер и кнопка отказа
    - предустановленные галочки
    """
    hl   = html.lower()
    soup = BeautifulSoup(html, "lxml")

    found_systems  = [n for n, sigs in ANALYTICS_SIGNATURES.items()
                      if any(s.lower() in hl for s in sigs)]
    found_foreign  = [n for n, sigs in FOREIGN_RESOURCES.items()
                      if sigs and any(s.lower() in hl for s in sigs)]
    # Иностранная аналитика (GA, Meta Pixel, Hotjar, SimilarWeb) = трансграничка.
    # Яндекс.Метрика / LiveInternet / Roistat — только analytics_systems.
    for name in found_systems:
        if name not in RUSSIAN_ANALYTICS and name not in found_foreign:
            found_foreign.append(name)
    has_banner     = _has_any(html, COOKIE_BANNER_KEYWORDS)
    has_decline    = _has_any(html, COOKIE_DECLINE_KEYWORDS)

    # Предустановленный чекбокс в баннере
    checked_by_default = False
    for cb in soup.find_all("input", {"type": "checkbox"}):
        if cb.get("checked") is not None:
            ctx = ""
            for parent in cb.parents:
                ctx = parent.get_text(strip=True).lower()
                if len(ctx) > 20:
                    break
            if _has_any(ctx, ["cookie", "куки", "аналитик", "маркетинг"]):
                checked_by_default = True
                break

    return {
        "analytics_systems":  found_systems,
        "foreign_resources":  found_foreign,
        "has_cookie_banner":  has_banner,
        "has_decline_button": has_decline,
        "checked_by_default": checked_by_default,
    }


# ═════════════════════════════════════════════════════════════════════════════
# Критерий 3: Формы сбора ПД — расширенное обнаружение
# ═════════════════════════════════════════════════════════════════════════════

def _is_pd_input(inp, soup: BeautifulSoup) -> tuple[bool, list]:
    """Определяет является ли поле сбором ПД. Убирает * из placeholder."""
    matched    = []
    all_kw     = _all_pd_keywords()
    input_type = inp.get("type", "text").lower()

    if input_type in PD_INPUT_TYPES:
        matched.append(f"type={input_type}")
        return True, matched
    if input_type in NON_PD_INPUT_TYPES:
        return False, []

    for attr in ("name", "id", "placeholder", "aria-label",
                 "data-placeholder", "data-name", "title"):
        raw_val = inp.get(attr, "")
        val = re.sub(r"[*·•!†]", "", raw_val).lower().strip()
        if not val:
            continue
        for kw in all_kw:
            if kw.lower() in val:
                matched.append(f"{attr}={raw_val[:40]}")
                return True, matched

    fid = inp.get("id", "")
    if fid:
        lbl = soup.find("label", {"for": fid})
        if lbl:
            lt = re.sub(r"[*·•!†]", "", lbl.get_text(strip=True)).lower()
            for kw in all_kw:
                if kw.lower() in lt:
                    matched.append(f"label={lt[:40]}")
                    return True, matched

    # Ближайший sibling / parent (для полей без label)
    for candidate in [inp.find_previous_sibling(), inp.parent]:
        if candidate and candidate.name not in ("form", "body", "html", None):
            ctext = re.sub(r"[*·•!†]", "", candidate.get_text(strip=True)).lower()
            if 2 < len(ctext) < 60:
                for kw in all_kw:
                    if kw.lower() in ctext:
                        matched.append(f"nearby={ctext[:40]}")
                        return True, matched

    return False, []


def _has_text_consent(context: str) -> bool:
    """Обнаруживает текстовое согласие 'Нажимая Отправить, вы соглашаетесь...'"""
    ctx_low = context.lower()
    return any(re.search(p, ctx_low) for p in TEXT_CONSENT_PATTERNS)


def _analyse_container(el, html: str, soup: BeautifulSoup) -> dict | None:
    """Анализирует элемент (form или div) на ПД-поля. None если ПД нет."""
    pd_fields = []
    for inp in el.find_all(["input", "textarea", "select"]):
        is_pd, matched = _is_pd_input(inp, soup)
        if is_pd:
            pd_fields.append({
                "tag": (f"{inp.name}[type={inp.get('type','text')},"
                        f"name={inp.get('name','')},"
                        f"placeholder={inp.get('placeholder','')}]"),
                "matched": matched,
            })
    if not pd_fields:
        return None

    el_str      = str(el)
    idx         = html.find(el_str[:80])
    ctx         = (html[max(0, idx-500): idx+len(el_str)+800] if idx != -1 else el_str)
    has_warning = bool(re.search(r"персональн|обработк|политик", ctx, re.I))
    text_cons   = _has_text_consent(ctx)

    consent_cb = checkbox_checked = checkbox_newtab = checkbox_mixed = False
    for cb in el.find_all("input", {"type": "checkbox"}):
        cb_id      = cb.get("id", "")
        label_text = ""
        if cb_id:
            lbl = soup.find("label", {"for": cb_id})
            if lbl:
                label_text = lbl.get_text(strip=True).lower()
        # Чекбокс считается чекбоксом согласия на ПД только если текст
        # содержит ОДНОВРЕМЕННО:
        #   - слово согласия: «согласи», «согласен», «согласна»
        #   - И слово про ПД: «персональн», «обработк», «конфиденц»
        # Это отсеивает «Согласен с условиями использования» (нет слова ПД)
        # и «Обработка данных» без явного согласия.
        _agree_words = ("согласи", "согласен", "согласна")
        _pd_words    = (
            "персональн", "обработк", "конфиденц",
            "передач", "данных", "политик",  # «передачи данных», «политике»
        )

        # Текст: label + родительский элемент чекбокса
        _near = (label_text + " " +
                 (cb.parent.get_text(strip=True) if cb.parent else "")).lower()

        _has_agree = any(w in _near for w in _agree_words)
        _has_pd    = any(w in _near for w in _pd_words)

        if _has_agree and _has_pd:
            consent_cb = True
            # Правильная проверка checked:
            # - checked="" или checked (boolean) → True (предустановлен)
            # - checked="false" / checked="0" / checked="no" → False (не отмечен)
            # - атрибут отсутствует → False
            _checked_val = cb.get("checked")
            if _checked_val is not None and str(_checked_val).lower() not in ("false", "0", "no"):
                checkbox_checked = True
            if _has_any(label_text, CONSENT_MIXED_KEYWORDS):
                checkbox_mixed = True
            if cb_id:
                lt = soup.find("label", {"for": cb_id})
                if lt:
                    for a in lt.find_all("a", href=True):
                        if a.get("target") == "_blank":
                            checkbox_newtab = True
            break

    return {
        "pd_fields":            [f["tag"] for f in pd_fields],
        "has_warning":          has_warning,
        "text_consent":         text_cons,
        "ctx_text":             ctx,   # сохраняем контекст для check_consent
        "method":               el.get("method", "POST").upper(),
        "has_consent_checkbox": consent_cb,
        "checkbox_checked":     checkbox_checked,
        "checkbox_newtab":      checkbox_newtab,
        "checkbox_mixed":       checkbox_mixed,
        "is_formless":          el.name != "form",
        # Идентификаторы формы для отчёта — нужны для поиска в коде
        "form_id":              (el.get("id")     or "").strip(),
        "form_name":            (el.get("name")   or "").strip(),
        "form_action":          (el.get("action") or "").strip(),
    }


def _find_formless(soup: BeautifulSoup) -> list:
    """
    Ищет кластеры input + кнопка-отправки вне тегов <form>.
    Поднимается до 5 уровней от кнопки до контейнера с input-ами.
    """
    btn_texts = [t.lower() for t in SUBMIT_BUTTON_TEXTS]
    candidates = []

    for el in soup.find_all(["button", "input"], {"type": ["submit", "button"]}):
        candidates.append(el)
    for el in soup.find_all(["button", "a", "div", "span"]):
        txt = el.get_text(strip=True).lower()
        # Точное совпадение: текст кнопки должен быть именно submit-словом,
        # а не просто содержать его как подстроку длинного текста
        if len(txt) < 50 and any(b == txt or txt.startswith(b) for b in btn_texts):
            if el not in candidates:
                candidates.append(el)

    found, seen = [], set()
    for btn in candidates:
        node = btn.parent
        for _ in range(5):
            if node is None or node.name in ("body", "html", "form"):
                break
            inputs = node.find_all(["input", "textarea"])
            # Требуем минимум 2 поля ввода (не считая hidden/submit/button),
            # чтобы не захватывать поля поиска или одиночные поля в шапке
            pd_like = [i for i in inputs
                       if i.get("type", "text").lower() not in NON_PD_INPUT_TYPES]
            if (len(pd_like) >= 2 and id(node) not in seen
                    and node.find_parent("form") is None):
                found.append(node)
                seen.add(id(node))
                break
            node = node.parent
    return found


def check_forms(html: str) -> dict:
    """
    Обнаруживает ВСЕ формы сбора ПД:
    1. Стандартные <form>
    2. Кластеры input+кнопка без <form> (кастомные JS-формы)

    Дедупликация здесь НЕ делается — две формы с одинаковыми полями
    (напр. две разные формы «Имя + Телефон» на одной странице) — это
    разные формы. Дедупликация между страницами — в _merge_results.
    """
    soup     = BeautifulSoup(html, "lxml")
    pd_forms = []

    for i, form in enumerate(soup.find_all("form")):
        data = _analyse_container(form, html, soup)
        if data:
            data["position"] = i   # порядковый номер формы на странице
            pd_forms.append(data)

    formless_start = len(pd_forms)
    for j, container in enumerate(_find_formless(soup)):
        data = _analyse_container(container, html, soup)
        if data:
            data["position"] = formless_start + j
            pd_forms.append(data)

    return {
        "pd_forms_count":   len(pd_forms),
        "pd_forms":         pd_forms,
        "all_pd_fields":    list({f for fm in pd_forms for f in fm["pd_fields"]}),
        "any_warning":      any(f["has_warning"] for f in pd_forms),
        "any_text_consent": any(f.get("text_consent") for f in pd_forms),
    }



# ═════════════════════════════════════════════════════════════════════════════
# Критерий 4: Согласие на обработку ПД
# ═════════════════════════════════════════════════════════════════════════════

def check_consent(html: str, pd_forms: list) -> dict:
    """
    Уровни согласия:
      полное             — чекбокс + 4+ реквизита, без нарушений
      частичное          — чекбокс есть, реквизиты неполные
      текстовое          — нет чекбокса, но «Нажимая Отправить...»
      нарушения          — чекбокс с нарушениями
      отсутствует        — нет ни чекбокса, ни текста

    ВАЖНО: анализ чекбоксов берётся СТРОГО из pd_forms (данные _analyse_container),
    а не из повторного парсинга HTML. Это исключает ложные срабатывания когда:
    - большой ctx захватывает чекбокс из соседней формы/popup
    - чекбокс найден в <form>, которая не является ПД-формой
    - атрибут checked="false" ошибочно трактуется как «отмечен»
    """
    if not pd_forms:
        return {"consent_level": "не применимо", "missing_requisites": [],
                "has_checkbox": False, "has_text_consent": False,
                "found_requisites": [], "violations": []}

    has_text_cons = any(f.get("text_consent") for f in pd_forms)
    found_req: set = set()
    violations: list = []
    has_checkbox = False

    for form in pd_forms:
        # ── Чекбокс и его нарушения ───────────────────────────────────────────
        # has_consent_checkbox и checkbox_checked уже вычислены в _analyse_container
        # строго внутри контейнера формы — без риска захватить соседние элементы.
        if form.get("has_consent_checkbox"):
            has_checkbox = True

            # Проверка checked: атрибут checked="" или checked (boolean) = True,
            # но checked="false" / checked="0" / checked="no" = НЕ отмечен.
            # _analyse_container сохраняет результат в checkbox_checked (bool).
            if form.get("checkbox_checked"):
                viol = "чекбокс предустановлен (checked по умолчанию)"
                if viol not in violations:
                    violations.append(viol)

            if form.get("checkbox_mixed"):
                viol = "чекбокс совмещён с согласием на рассылку/оферту"
                if viol not in violations:
                    violations.append(viol)

        # ── Реквизиты из контекста формы ─────────────────────────────────────
        # Берём ctx из pd_form если он сохранён; иначе пропускаем.
        ctx_low = form.get("ctx_text", "").lower()
        if ctx_low:
            for req, kws in CONSENT_REQUISITES.items():
                if any(kw in ctx_low for kw in kws):
                    found_req.add(req)

    missing = [r for r in CONSENT_REQUISITES if r not in found_req]

    if has_checkbox and len(found_req) >= 4 and not violations:
        level = "полное"
    elif has_checkbox and not violations:
        level = "частичное"
    elif has_checkbox:
        level = "нарушения"
    elif has_text_cons:
        level = "текстовое (нет чекбокса)"
        violations.append(
            "согласие только текстом «Нажимая Отправить...» — "
            "нет отдельного чекбокса, не соответствует 152-ФЗ"
        )
    else:
        level = "отсутствует"

    return {
        "consent_level":      level,
        "has_checkbox":       has_checkbox,
        "has_text_consent":   has_text_cons,
        "found_requisites":   list(found_req),
        "missing_requisites": missing,
        "violations":         violations,
    }



# ═════════════════════════════════════════════════════════════════════════════
# Оценка риска
# ═════════════════════════════════════════════════════════════════════════════

def assess_risk(policy: dict, analytics: dict, forms: dict,
                consent: dict, pw: dict) -> tuple[str, list]:
    """
    Накопительная балльная система оценки риска.

    Каждое нарушение добавляет штрафные баллы (≈ размер штрафа по КоАП).
    Выполненные требования дают «защитные» баллы, снижающие итог.
    Уровень риска определяется по итоговому score:

        КРИТИЧЕСКИЙ  score ≥ 900 000
        ВЫСОКИЙ      score ≥ 450 000
        СРЕДНИЙ      score ≥ 150 000
        НИЗКИЙ       score <  150 000
    """
    violations = []
    penalty    = 0   # штрафные баллы (нарушения)
    protection = 0   # защитные баллы (выполненные требования)

    # ── Иностранные ресурсы / трансграничная передача ─────────────────────────
    # foreign_resources уже включает CDN/виджеты + иностранную аналитику
    # (GA, Meta, Hotjar, SimilarWeb). Российская аналитика (Яндекс.Метрика и др.)
    # туда не входит — см. check_analytics / RUSSIAN_ANALYTICS.
    systems   = list(analytics.get("analytics_systems") or [])
    foreign   = list(analytics.get("foreign_resources") or [])
    for s in systems:
        if s not in RUSSIAN_ANALYTICS and s not in foreign:
            foreign.append(s)

    pw_before = list(pw.get("trackers_before_consent") or [])
    foreign_before  = [t for t in pw_before if t not in RUSSIAN_ANALYTICS]
    russian_before  = [t for t in pw_before if t in RUSSIAN_ANALYTICS]

    if pw_before:
        # Cookie: любой трекер до согласия — нарушение (в т.ч. Яндекс.Метрика).
        # В список трансгранички попадают только иностранные имена.
        for t in foreign_before:
            if t not in foreign:
                foreign.append(t)
        if foreign_before:
            violations.append(
                f"Трекеры грузятся ДО согласия пользователя: "
                f"{', '.join(pw_before)} → до 500 000 руб."
            )
            penalty += 500_000
        else:
            # Только российская аналитика до согласия — cookie, не «за рубеж»
            violations.append(
                f"Трекеры грузятся ДО согласия пользователя: "
                f"{', '.join(russian_before)} → до 300 000 руб."
            )
            penalty += 300_000

    if foreign:
        violations.append(
            f"Иностранные ресурсы (трансграничная передача): "
            f"{', '.join(foreign)} → до 500 000 руб."
        )
        # Смягчающий фактор: если есть баннер с кнопкой отказа —
        # пользователь мог отказаться, риск ниже
        has_banner  = analytics.get("has_cookie_banner") or pw.get("accept_btn_found")
        has_decline = analytics.get("has_decline_button") or pw.get("decline_btn_found")
        if has_banner and has_decline:
            penalty += 250_000   # есть согласие → штраф вдвое меньше
        elif has_banner:
            penalty += 375_000   # баннер есть, но без кнопки отказа
        else:
            penalty += 500_000   # нет баннера вообще → максимум
    elif systems and all(s in RUSSIAN_ANALYTICS for s in systems):
        # Только отечественные трекеры — нет трансграничной передачи
        protection += 100_000

    # ── Cookie-баннер ─────────────────────────────────────────────────────────
    has_banner  = analytics.get("has_cookie_banner") or pw.get("accept_btn_found")
    has_decline = analytics.get("has_decline_button") or pw.get("decline_btn_found")
    systems     = analytics.get("analytics_systems", [])

    if not has_banner and systems:
        violations.append("Нет cookie-баннера при наличии трекеров → до 300 000 руб.")
        penalty += 300_000
    elif has_banner:
        protection += 150_000   # баннер есть — это серьёзный плюс
        if has_decline:
            protection += 100_000   # кнопка отказа — ещё плюс
        else:
            violations.append("Нет кнопки «Отказаться» в cookie-баннере → до 300 000 руб.")
            penalty += 200_000   # есть баннер, но без кнопки — меньше чем без баннера

    if analytics.get("checked_by_default"):
        violations.append("Галочка согласия предустановлена → до 300 000 руб.")
        penalty += 300_000

    if pw.get("playwright_used") and not pw.get("banner_visible") and has_banner:
        violations.append("Баннер не виден при первом посещении → до 300 000 руб.")
        penalty += 200_000   # баннер есть, но прячется

    # ── Политика ПД ───────────────────────────────────────────────────────────
    if not policy.get("found"):
        violations.append("Политика обработки ПД не найдена → до 150 000 руб.")
        penalty += 150_000
    else:
        protection += 75_000    # политика найдена — базовый плюс

        missing_sections = policy.get("sections_missing", [])
        if missing_sections:
            # Штраф пропорционален количеству отсутствующих разделов
            section_penalty = min(len(missing_sections) * 12_000, 100_000)
            violations.append(
                f"Отсутствующие разделы политики ({len(missing_sections)} из 11): "
                f"{', '.join(missing_sections)} → до 150 000 руб."
            )
            penalty += section_penalty
        else:
            protection += 75_000    # все разделы на месте

        if not policy.get("policy_in_footer"):
            violations.append("Ссылка на политику отсутствует в футере → до 150 000 руб.")
            penalty += 50_000
        else:
            protection += 25_000

        if policy.get("policy_is_pdf"):
            violations.append("Политика в формате PDF — АС МПДн может не распарсить")
            penalty += 30_000

        if not policy.get("operator_found"):
            violations.append("Реквизиты оператора не найдены в политике → до 150 000 руб.")
            penalty += 50_000
        else:
            protection += 25_000

    # ── Формы и согласие ──────────────────────────────────────────────────────
    forms_count = max(
        forms.get("pd_forms_count", 0),
        pw.get("pd_forms_count_js", 0),
    )
    if forms_count > 0:
        no_checkbox = any(
            not f.get("has_consent_checkbox")
            for f in forms.get("pd_forms", [])
        )
        if no_checkbox:
            violations.append(
                "Форма с ПД без чекбокса согласия → до 100 000 руб."
            )
            penalty += 100_000
        else:
            protection += 50_000   # формы есть, согласие оформлено

        for v in consent.get("violations", []):
            violations.append(f"{v.capitalize()} → до 100 000 руб.")
            penalty += 60_000   # нарушение в деталях согласия — не максимум

    # ── Итоговый score и уровень риска ───────────────────────────────────────
    # Защитные баллы снижают итог, но не более чем на 40% от штрафов
    discount   = min(protection, int(penalty * 0.4))
    score      = max(0, penalty - discount)

    if score >= 700_000:
        risk = "🔴 КРИТИЧЕСКИЙ"
    elif score >= 220_000:
        risk = "🔴 ВЫСОКИЙ"
    elif score >= 100_000:
        risk = "🟡 СРЕДНИЙ"
    else:
        risk = "🟢 НИЗКИЙ"

    return risk, violations


# ═════════════════════════════════════════════════════════════════════════════
# Главная функция
# ═════════════════════════════════════════════════════════════════════════════

def check_site(url: str, criteria: dict | None = None,
               forms_html_override: str = "") -> dict:
    """
    Проверяет один сайт.

    criteria может содержать ключ 'use_playwright': True/False
    """
    if criteria is None:
        criteria = {k: True for k in
                    ("check_policy", "check_analytics",
                     "check_forms", "check_consent")}

    use_pw = criteria.get("use_playwright", False) and PLAYWRIGHT_AVAILABLE

    start  = time.time()
    url    = normalize_url(url)
    domain = urllib.parse.urlparse(url).netloc or url

    result = {
        "domain":              domain,
        "url":                 url,
        "accessible":          False,
        "http_status":         0,
        "error":               "",
        # Критерий 1
        "policy_found":        False,
        "policy_url":          "",
        "policy_status":       0,
        "policy_text_len":     0,
        "policy_is_pdf":       False,
        "policy_in_footer":    False,
        "sections_found":      [],
        "sections_missing":    [],
        "operator_found":      False,
        # Критерий 2
        "analytics_systems":   [],
        "foreign_resources":   [],
        "has_cookie_banner":   False,
        "has_decline_button":  False,
        "checked_by_default":  False,
        # Критерий 3
        "pd_forms_count":      0,
        "pd_forms":            [],
        "pd_fields":           [],
        "forms_have_warning":  False,
        # Критерий 4
        "consent_level":       "не проверено",
        "missing_requisites":  [],
        "consent_violations":  [],
        # Playwright
        "playwright_used":           False,
        "screenshot_file":           "",
        "banner_visible":            False,
        "trackers_before_consent":   [],
        "accept_btn_found":          False,
        "decline_btn_found":         False,
        # Итог
        "risk":            "🟡 СРЕДНИЙ",
        "violations":      [],
        "check_time_sec":  0,
        "cms":             "",
    }

    # ── Playwright (до requests — пока страница «свежая») ─────────────────────
    pw = {
        "playwright_used": False, "screenshot_file": "",
        "banner_visible": False, "accept_btn_found": False,
        "decline_btn_found": False, "trackers_before_consent": [],
        "trackers_after_consent": [], "pd_forms_count_js": 0, "pw_html": "", "pw_error": "",
    }
    if use_pw:
        logger.info("Playwright check: %s", url)
        pw = playwright_check(url)
        result.update({
            "playwright_used":         pw["playwright_used"],
            "screenshot_file":         pw["screenshot_file"],
            "banner_visible":          pw["banner_visible"],
            "trackers_before_consent": pw["trackers_before_consent"],
            "accept_btn_found":        pw["accept_btn_found"],
            "decline_btn_found":       pw["decline_btn_found"],
        })

    # ── Базовая загрузка страницы (requests) ──────────────────────────────────
    session = make_session()
    status, html, final_url, resp_headers = fetch_page_with_headers(session, url)
    result["http_status"] = status
    result["url"]         = final_url

    # Если Playwright использовался — всегда предпочитаем его HTML.
    # Причина: requests получает статический HTML без JS-рендера,
    # а Playwright — полностью отрендеренную страницу с Vue/React-формами.
    # Пример: toplife.agency возвращает requests 200 + 636кб статики без форм,
    # а Playwright находит 3 формы в том же объёме после рендера Vue.js.
    pw_html_len = len(pw.get("pw_html", ""))
    if use_pw and pw_html_len > 500:
        logger.info("PW HTML используется: %d байт (requests: %d байт, status=%s) для %s",
                    pw_html_len, len(html), status, url)
        html      = pw["pw_html"]
        final_url = url
        if status == 0:
            status = 200
        result["http_status"] = status
        result["url"]         = final_url
    elif status != 200 or len(html.strip()) < 500:
        logger.warning("requests вернул %s / %d байт, PW недоступен: %s",
                       status, len(html), url)
    result["_html_snapshot"] = html   # сохраняем для _merge_results

    if status == 0 or not html:
        result["error"]          = "Сайт недоступен"
        result["check_time_sec"] = round(time.time() - start, 1)
        return result

    result["accessible"] = True

    # ── Определение CMS ───────────────────────────────────────────────────────
    cms = detect_cms(html, resp_headers, final_url, session=session)
    result["cms"] = cms
    if cms:
        logger.info("[%s] CMS detected: %s", domain, cms)
    policy = {
        "found": False, "policy_url": "", "policy_status": 0,
        "policy_text_len": 0, "policy_is_pdf": False,
        "policy_in_footer": False, "sections_found": [],
        "sections_missing": list(POLICY_REQUIRED_SECTIONS.keys()),
        "operator_found": False, "keywords_found": [],
    }
    if criteria.get("check_policy"):
        policy = check_policy(session, final_url, html)
        # Резерв: если политика не найдена на текущей странице (например, пользователь
        # передал URL внутренней страницы), пробуем главную страницу сайта.
        if not policy.get("found") and not policy.get("policy_url"):
            parsed = urllib.parse.urlparse(final_url)
            homepage_url = f"{parsed.scheme}://{parsed.netloc}/"
            if homepage_url != final_url:
                logger.info("Политика не найдена на %s, пробуем главную: %s",
                            final_url, homepage_url)
                _, home_html, _ = fetch_page(session, homepage_url)
                if home_html:
                    policy_from_home = check_policy(session, homepage_url, home_html)
                    if policy_from_home.get("found") or policy_from_home.get("policy_url"):
                        policy = policy_from_home
        result.update({
            "policy_found":     policy["found"],
            "policy_url":       policy["policy_url"],
            "policy_status":    policy["policy_status"],
            "policy_text_len":  policy["policy_text_len"],
            "policy_is_pdf":    policy["policy_is_pdf"],
            "policy_in_footer": policy["policy_in_footer"],
            "sections_found":   policy["sections_found"],
            "sections_missing": policy["sections_missing"],
            "operator_found":   policy["operator_found"],
        })

    # ── Критерий 2 ────────────────────────────────────────────────────────────
    analytics = {
        "analytics_systems": [], "foreign_resources": [],
        "has_cookie_banner": False, "has_decline_button": False,
        "checked_by_default": False,
    }
    if criteria.get("check_analytics"):
        analytics = check_analytics(html)
        result.update({
            "analytics_systems":  analytics["analytics_systems"],
            "foreign_resources":  analytics["foreign_resources"],
            "has_cookie_banner":  analytics["has_cookie_banner"],
            "has_decline_button": analytics["has_decline_button"],
            "checked_by_default": analytics["checked_by_default"],
        })

    # ── Критерий 3 ────────────────────────────────────────────────────────────
    forms = {
        "pd_forms_count": 0, "pd_forms": [],
        "all_pd_fields": [], "any_warning": False,
    }
    if criteria.get("check_forms"):
        # forms_html_override — JS-rendered HTML (от Playwright) для поиска форм.
        # Используется для доп. страниц где формы генерируются JavaScript.
        _html_for_forms = forms_html_override if forms_html_override else html
        forms = check_forms(_html_for_forms)

        # Если Playwright дал список реально отмеченных чекбоксов (JS .checked),
        # корректируем checkbox_checked в каждой форме.
        # Это устраняет ложные срабатывания когда page.content() сериализует
        # атрибут checked="" для визуально не отмеченных чекбоксов.
        pw_checked_ids = set(pw.get("pw_checked_cb_ids", []))
        if use_pw and pw_checked_ids is not None:
            for f in forms["pd_forms"]:
                # Если список проверенных id пуст → ни один чекбокс не отмечен
                # Если список непустой → отмечен только если его id/name там есть
                # Получаем id/name чекбокса из pd_fields (тег сохраняет name=...)
                cb_names = set()
                for tag_str in f.get("pd_fields", []):
                    # Формат: "input[type=checkbox,name=agree,placeholder=]"
                    m = re.search(r"name=([^,\]]+)", tag_str)
                    if m:
                        cb_names.add(m.group(1))
                # checkbox_checked = True только если реально отмечен в DOM
                if f.get("checkbox_checked"):
                    if not (pw_checked_ids & cb_names) and pw_checked_ids != {"__unnamed__"}:
                        f["checkbox_checked"] = False

        result.update({
            "pd_forms_count":    forms["pd_forms_count"],
            "pd_forms":          forms["pd_forms"],
            "pd_fields":         forms["all_pd_fields"],
            "forms_have_warning": forms["any_warning"],
        })

    # ── Критерий 4 ────────────────────────────────────────────────────────────
    consent = {
        "consent_level": "не применимо", "missing_requisites": [],
        "has_checkbox": False, "found_requisites": [], "violations": [],
    }
    if criteria.get("check_consent") and forms["pd_forms_count"] > 0:
        consent = check_consent(html, forms["pd_forms"])
        result.update({
            "consent_level":      consent["consent_level"],
            "missing_requisites": consent["missing_requisites"],
            "consent_violations": consent["violations"],
        })

    # ── Оценка риска ──────────────────────────────────────────────────────────
    risk, violations = assess_risk(policy, analytics, forms, consent, pw)
    result["risk"]       = risk
    result["violations"] = violations

    result["check_time_sec"] = round(time.time() - start, 1)
    session.close()
    return result


# ═════════════════════════════════════════════════════════════════════════════
# Многостраничная проверка сайта
# ═════════════════════════════════════════════════════════════════════════════

# Сегменты URL с высокой вероятностью форм/трекеров (приоритет при краулинге)
_PRIORITY_SEGMENTS = re.compile(
    r"(kontakt|contact|zayavk|order|uslugi|services|о-нас|about|"
    r"portfolio|catalog|product|blog|news|otzyv|review|feedback|"
    r"uslugi|price|ceny|стоимость|записаться|оформить)",
    re.IGNORECASE,
)


def _is_single_page_site(candidate_urls: list) -> bool:
    """
    Определяет, является ли сайт одностраничником или лендингом.
    Признак: меньше 3 уникальных внутренних страниц (не считая главной).
    """
    return len(candidate_urls) < 3


def crawl_site_pages(
    session: requests.Session,
    base_url: str,
    start_html: str,
    max_pages: int = 5,
) -> list[dict]:
    """
    Обходит сайт и возвращает список страниц для проверки.

    Возвращает список словарей: [{"url": str, "html": str}, ...]
    Первым элементом всегда идёт стартовая страница (base_url).

    Алгоритм:
    1. Собирает все внутренние ссылки со стартовой страницы.
    2. Если уникальных страниц < 3 — одностраничник, возвращает только главную.
    3. Приоритизирует страницы с ключевыми сегментами (формы, контакты, услуги).
    4. Загружает до max_pages страниц, избегая дубликатов в т.ч. после редиректов.
    """
    parsed_base = urllib.parse.urlparse(base_url)
    base_netloc = parsed_base.netloc.lower().lstrip("www.")

    def _normalise(u: str) -> str:
        """Нормализует URL для сравнения: убирает query, fragment, trailing slash."""
        p = urllib.parse.urlparse(u)
        path = p.path.rstrip("/") or "/"
        return urllib.parse.urlunparse(p._replace(path=path, fragment="", query=""))

    base_url_norm = _normalise(base_url)
    pages = [{"url": base_url, "html": start_html}]
    visited = {base_url_norm}

    # Собираем ссылки со стартовой страницы
    soup = BeautifulSoup(start_html, "lxml")
    candidate_urls: list[tuple[int, str]] = []  # (priority, url)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if should_skip_crawl_url(href):
            continue
        full_url = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(full_url)
        clean_url = _normalise(full_url)
        link_netloc = parsed.netloc.lower().lstrip("www.")
        if link_netloc != base_netloc:
            continue
        if clean_url in visited:
            continue
        priority = 0 if _PRIORITY_SEGMENTS.search(clean_url) else 1
        candidate_urls.append((priority, clean_url))

    # ── Детектирование одностраничника / лендинга ─────────────────────────────
    if _is_single_page_site(candidate_urls):
        logger.info("Multipage: одностраничник/лендинг (%d ссылок) — только главная: %s",
                    len(candidate_urls), base_url)
        return pages

    # Сортируем: приоритетные первыми, затем по длине URL (короче = выше уровень)
    candidate_urls.sort(key=lambda x: (x[0], len(x[1])))

    # Загружаем страницы до лимита
    for _, url in candidate_urls:
        if len(pages) >= max_pages:
            break
        try:
            status, html, final_url = fetch_page(session, url)
            # Проверяем дубль после редиректа (например, /about → /)
            final_norm = _normalise(final_url)
            if final_norm in visited or should_skip_crawl_url(final_url):
                if should_skip_crawl_url(final_url):
                    logger.debug("Multipage: пропуск auth/register %s", final_url)
                else:
                    logger.debug("Multipage: пропуск дубля после редиректа %s → %s", url, final_url)
                continue
            visited.add(final_norm)
            if status == 200 and html and len(html.strip()) > 200:
                pages.append({"url": final_url, "html": html})
                logger.info("Multipage: добавлена страница %s", final_url)
        except Exception as e:
            logger.warning("Multipage: ошибка загрузки %s: %s", url, e)

    return pages


def _merge_results(base_result: dict, page_results: list[dict]) -> dict:
    """
    Сводит результаты нескольких страниц в единый отчёт по домену.

    Стратегия слияния:
    - Политика ПД: берём лучший найденный результат (где больше разделов).
    - Аналитика / иностранные ресурсы: объединяем уникальные находки.
    - Cookie-баннер: True если найден хоть на одной странице.
    - Формы: суммируем, дедуплицируем поля.
    - Согласие: берём наихудший уровень (чтобы не скрыть нарушения).
    - Playwright: данные со стартовой страницы (там проверяли баннер).
    - Риск и нарушения: пересчитываем по объединённым данным.
    - pages_checked: список проверенных URL.
    """
    merged = dict(base_result)  # копируем базовый результат
    merged["pages_checked"] = [r["url"] for r in page_results]
    merged["pages_count"] = len(page_results)

    # ── Критерий 1: берём лучшую политику ────────────────────────────────────
    best_policy_result = base_result
    best_sections_count = len(base_result.get("sections_found", []))

    for r in page_results[1:]:  # остальные страницы
        sc = len(r.get("sections_found", []))
        if r.get("policy_found") and sc > best_sections_count:
            best_sections_count = sc
            best_policy_result = r

    for key in ("policy_found", "policy_url", "policy_status",
                "policy_text_len", "policy_is_pdf", "policy_in_footer",
                "sections_found", "sections_missing", "operator_found"):
        merged[key] = best_policy_result.get(key, base_result.get(key))

    # ── Критерий 2: объединяем аналитику со всех страниц ─────────────────────
    all_analytics: set = set(base_result.get("analytics_systems", []))
    all_foreign: set   = set(base_result.get("foreign_resources", []))
    has_banner         = base_result.get("has_cookie_banner", False)
    has_decline        = base_result.get("has_decline_button", False)
    checked_default    = base_result.get("checked_by_default", False)

    for r in page_results[1:]:
        all_analytics.update(r.get("analytics_systems", []))
        all_foreign.update(r.get("foreign_resources", []))
        has_banner      = has_banner   or r.get("has_cookie_banner", False)
        has_decline     = has_decline  or r.get("has_decline_button", False)
        checked_default = checked_default or r.get("checked_by_default", False)

    merged["analytics_systems"]  = sorted(all_analytics)
    merged["foreign_resources"]  = sorted(all_foreign)
    merged["has_cookie_banner"]  = has_banner
    merged["has_decline_button"] = has_decline
    merged["checked_by_default"] = checked_default

    # ── Критерий 3: дедупликация форм ────────────────────────────────────────
    #
    # Правила:
    # А) Named-форма (есть form_id или form_name) — дедупликация по id+name+полям.
    #    Битрикс добавляет случайные токены в action (?rmT=..., sessid=...) —
    #    action ИГНОРИРУЕТСЯ для named-форм. Используем только id/name/поля.
    #
    # Б) Анонимная форма (нет id и name) — дедупликация по набору полей.
    #    На ОДНОЙ странице различаем по position (разные формы с теми же полями).
    #    На разных страницах — одинаковые поля = сквозная форма = один экземпляр.

    seen_global: set   = set()
    unique_forms: list = []
    forms_warn = False

    def _normalize_form_id(id_str: str) -> str:
        """
        Нормализует id формы Битрикса: убирает числовой префикс N_ / sN_.
        1_183653, 2_183653, 3_183653 → 183653 (одна форма блока).
        s1_RECOMMENDED → recommended.
        """
        if not id_str:
            return ""
        return re.sub(r'^[a-z]?\d+_', '', id_str.strip().lower()) or id_str.strip().lower()

    def _normalize_form_name(name_str: str) -> str:
        """
        Нормализует name: убирает числовые суффиксы.
        iblock_add_38, iblock_add_42 → iblock_add.
        """
        if not name_str:
            return ""
        return re.sub(r'[_\s]+\d+$', '', name_str.strip().lower()) or name_str.strip().lower()

    def _form_signature(form: dict) -> tuple:
        form_id   = _normalize_form_id(  (form.get("form_id")   or "").strip())
        form_name = _normalize_form_name((form.get("form_name") or "").strip())

        fields = set()
        for tag_str in form.get("pd_fields", []):
            for attr in ("name=", "placeholder="):
                m = re.search(rf"{attr}([^,\]]+)", tag_str)
                if m:
                    val = m.group(1).strip().lower()
                    if val:
                        fields.add(f"{attr}{val}")

        if form_id or form_name:
            return ("named", form_id, form_name, frozenset(fields))
        return ("anon", frozenset(fields))

    for r in page_results:
        page_url = r.get("url", "")
        seen_this_page: set = set()

        for form in r.get("pd_forms", []):
            sig      = _form_signature(form)
            is_named = bool((form.get("form_id") or "").strip()
                            or (form.get("form_name") or "").strip())

            if is_named:
                if sig in seen_global:
                    continue
            else:
                intra_key = (page_url, sig, form.get("position", -1))
                if intra_key in seen_this_page:
                    continue
                seen_this_page.add(intra_key)
                if sig in seen_global:
                    continue

            seen_global.add(sig)
            f_copy = dict(form)
            f_copy["source_page"] = page_url
            unique_forms.append(f_copy)

        forms_warn = forms_warn or r.get("forms_have_warning", False)

    all_fields = list(dict.fromkeys(
        field for f in unique_forms for field in f.get("pd_fields", [])
    ))
    merged["pd_forms_count"]    = len(unique_forms)
    merged["pd_forms"]          = unique_forms
    merged["pd_fields"]         = all_fields
    merged["forms_have_warning"] = forms_warn

    # ── Критерий 4: согласие пересчитывается по дедуплицированным формам ─────
    # Используем base_result html — Playwright-HTML стартовой страницы самый полный.
    # Передаём unique_forms, чтобы нарушения не дублировались от повторяющихся форм.
    if unique_forms:
        # html для пересчёта согласия — берём из base_result (там pw_html если был PW)
        _html_for_consent = base_result.get("_html_snapshot", "")
        consent_recalc = check_consent(_html_for_consent, unique_forms)
        merged["consent_level"]      = consent_recalc["consent_level"]
        merged["consent_violations"] = consent_recalc["violations"]
        merged["missing_requisites"] = consent_recalc["missing_requisites"]
    else:
        merged["consent_level"]      = "не применимо"
        merged["consent_violations"] = []
        merged["missing_requisites"] = []

    # ── Скриншоты: собираем со всех страниц ──────────────────────────────────
    screenshot_files = []
    for r in page_results:
        sf = r.get("screenshot_file", "")
        if sf:
            screenshot_files.append({"url": r.get("url", ""), "file": sf})
    merged["screenshot_files"] = screenshot_files
    # Обратная совместимость — первый скриншот как раньше
    merged["screenshot_file"] = screenshot_files[0]["file"] if screenshot_files else ""

    # ── Пересчитываем риск и нарушения ───────────────────────────────────────
    # Собираем все нарушения со всех страниц (дедуплицируем)
    all_violations = list(base_result.get("violations", []))
    for r in page_results[1:]:
        for v in r.get("violations", []):
            if v not in all_violations:
                all_violations.append(v)
    merged["violations"] = all_violations

    # Риск — берём наивысший
    RISK_SEVERITY = {"🔴 КРИТИЧЕСКИЙ": 4, "🔴 ВЫСОКИЙ": 3, "🟡 СРЕДНИЙ": 2, "🟢 НИЗКИЙ": 1}
    worst_risk = max(
        [r.get("risk", "🟢 НИЗКИЙ") for r in page_results],
        key=lambda r: RISK_SEVERITY.get(r, 0),
    )
    merged["risk"] = worst_risk

    # Суммарное время
    merged["check_time_sec"] = round(
        sum(r.get("check_time_sec", 0) for r in page_results), 1
    )

    return merged


def _pw_get_rendered_html(url: str) -> str:
    """
    Возвращает полностью отрендеренный (JS-executed) HTML страницы через Playwright.
    Используется для дополнительных страниц при многостраничной проверке —
    чтобы найти формы, которые генерируются JavaScript (Битрикс, React, Vue и т.п.).
    Возвращает пустую строку при ошибке.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return ""

    async def _do_render():
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=PW_HEADLESS)
                context = await browser.new_context(
                    locale=PW_LOCALE, timezone_id=PW_TIMEZONE
                )
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded",
                                    timeout=PW_TIMEOUT_MS)
                    # Ждём загрузки JS-компонентов
                    await page.wait_for_timeout(2000)
                    # Скроллим чтобы lazy-load компоненты отрисовались
                    await page.evaluate("""async () => {
                        let total = 0;
                        await new Promise(resolve => {
                            const step = () => {
                                window.scrollBy(0, 500);
                                total += 500;
                                if (total < document.body.scrollHeight) {
                                    setTimeout(step, 100);
                                } else { setTimeout(resolve, 300); }
                            };
                            step();
                        });
                        window.scrollTo(0, 0);
                    }""")
                    await page.wait_for_timeout(500)
                    html = await page.content()
                    await browser.close()
                    return html
                except Exception as e:
                    logger.warning("PW render %s: %s", url, e)
                    try:
                        html = await page.content()
                        await browser.close()
                        return html
                    except Exception:
                        await browser.close()
                        return ""
        except Exception as e:
            logger.warning("PW render launch %s: %s", url, e)
            return ""

    try:
        return asyncio.run(_do_render())
    except Exception as e:
        logger.warning("PW render run %s: %s", url, e)
        return ""


def _pw_screenshot_only(url: str, screenshots_dir: str) -> str:
    """
    Делает скриншот страницы через Playwright без перехвата трекеров.
    Используется для дополнительных страниц при многостраничной проверке.
    Возвращает имя файла скриншота или пустую строку при ошибке.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return ""

    async def _do_screenshot():
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=PW_HEADLESS)
                context = await browser.new_context(
                    locale=PW_LOCALE, timezone_id=PW_TIMEZONE
                )
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded",
                                    timeout=PW_TIMEOUT_MS)
                    await page.wait_for_timeout(1500)
                    # Прокручиваем для активации lazy-load перед скриншотом
                    await page.evaluate("""async () => {
                        await new Promise(resolve => {
                            let total = 0;
                            const step = () => {
                                window.scrollBy(0, 600);
                                total += 600;
                                if (total < document.body.scrollHeight) {
                                    setTimeout(step, 120);
                                } else {
                                    window.scrollTo(0, 0);
                                    setTimeout(resolve, 300);
                                }
                            };
                            step();
                        });
                    }""")
                except Exception:
                    pass
                domain = urllib.parse.urlparse(url).netloc.replace(".", "_")
                path_slug = urllib.parse.urlparse(url).path.strip("/").replace("/", "_")[:30]
                ts    = int(time.time())
                fname = f"{domain}_{path_slug}_{ts}.png" if path_slug else f"{domain}_{ts}.png"
                fpath = os.path.join(screenshots_dir, fname)
                try:
                    await page.screenshot(path=fpath, full_page=True)
                    await browser.close()
                    return fname
                except Exception as e:
                    logger.warning("Screenshot only %s: %s", url, e)
                    await browser.close()
                    return ""
        except Exception as e:
            logger.warning("PW screenshot_only launch %s: %s", url, e)
            return ""

    try:
        return asyncio.run(_do_screenshot())
    except Exception as e:
        logger.warning("Screenshot_only run %s: %s", url, e)
        return ""


def check_site_multipage(
    url: str,
    criteria: dict | None = None,
    max_pages: int = 4,
) -> dict:
    """
    Проверяет сайт на нескольких страницах и возвращает сводный результат.

    Шаги:
    1. Запускает check_site() для стартовой страницы (полная проверка).
    2. Краулит до max_pages-1 дополнительных страниц того же домена.
    3. Для каждой дополнительной страницы проверяет аналитику, формы и согласие
       (политику не перепроверяем — она найдена на шаге 1).
    4. Сводит результаты в единый отчёт через _merge_results().
    """
    if criteria is None:
        criteria = {k: True for k in
                    ("check_policy", "check_analytics",
                     "check_forms", "check_consent")}

    start = time.time()

    # ── Шаг 1: полная проверка стартовой страницы ─────────────────────────────
    base_result = check_site(url, criteria)
    base_result["url"] = base_result.get("url") or url

    if not base_result.get("accessible"):
        # Сайт недоступен — дальше нет смысла
        base_result["pages_checked"] = [base_result["url"]]
        base_result["pages_count"] = 1
        return base_result

    if max_pages <= 1:
        base_result["pages_checked"] = [base_result["url"]]
        base_result["pages_count"] = 1
        return base_result

    # ── Шаг 2: краул страниц ──────────────────────────────────────────────────
    # Загружаем HTML стартовой страницы заново для краулинга ссылок
    session = make_session()
    final_url = base_result["url"]
    _, start_html, _ = fetch_page(session, final_url)

    if not start_html:
        base_result["pages_checked"] = [final_url]
        base_result["pages_count"] = 1
        session.close()
        return base_result

    pages = crawl_site_pages(session, final_url, start_html, max_pages)
    logger.info("Multipage: найдено %d страниц для %s", len(pages), final_url)

    # ── Шаг 3: проверяем дополнительные страницы ─────────────────────────────
    # Для доп. страниц НЕ проверяем политику (она общая для домена)
    # и НЕ запускаем Playwright (долго и cookie-баннер нужен только при первом визите)
    extra_criteria = {
        "check_policy":   False,
        "check_analytics": criteria.get("check_analytics", True),
        "check_forms":     criteria.get("check_forms", True),
        "check_consent":   criteria.get("check_consent", True),
        "use_playwright":  False,
    }

    all_page_results = [base_result]
    use_pw_for_forms = PLAYWRIGHT_AVAILABLE  # рендерим JS на доп. страницах всегда

    for page in pages[1:]:  # пропускаем первую (уже проверена)
        page_url = page["url"]
        if should_skip_crawl_url(page_url):
            logger.info("Multipage: пропуск auth/register страницы %s", page_url)
            continue
        logger.info("Multipage: проверяем доп. страницу %s", page_url)
        try:
            # Если Playwright доступен — получаем JS-rendered HTML для поиска форм
            # (Битрикс, React, Vue и т.п. генерируют формы через JS)
            if use_pw_for_forms:
                rendered_html = _pw_get_rendered_html(page_url)
                if rendered_html:
                    logger.info("Multipage: PW-rendered HTML для %s (%d chars)",
                                page_url, len(rendered_html))
                    # Проверяем формы по rendered HTML, остальное — по статичному
                    page_result = check_site(page_url, extra_criteria,
                                             forms_html_override=rendered_html)
                else:
                    page_result = check_site(page_url, extra_criteria)
            else:
                page_result = check_site(page_url, extra_criteria)

            page_result["url"] = page_result.get("url") or page_url
            all_page_results.append(page_result)
        except Exception as e:
            logger.error("Multipage: ошибка проверки %s: %s", page_url, e)

    session.close()

    # ── Шаг 4: скриншоты дополнительных страниц через Playwright ─────────────
    # Если Playwright был запрошен — делаем лёгкие скриншоты доп. страниц
    # (без перехвата трекеров — только navigate + screenshot)
    if criteria.get("use_playwright") and PLAYWRIGHT_AVAILABLE and len(all_page_results) > 1:
        for pr in all_page_results[1:]:  # пропускаем стартовую (уже есть скриншот)
            page_url = pr.get("url", "")
            if not page_url or pr.get("screenshot_file"):
                continue
            try:
                sf = _pw_screenshot_only(page_url, SCREENSHOTS_DIR)
                if sf:
                    pr["screenshot_file"] = sf
                    logger.info("Multipage screenshot: %s → %s", page_url, sf)
            except Exception as e:
                logger.warning("Multipage screenshot failed %s: %s", page_url, e)

    # ── Шаг 5: сводный результат ──────────────────────────────────────────────
    merged = _merge_results(base_result, all_page_results)
    merged["check_time_sec"] = round(time.time() - start, 1)

    logger.info(
        "Multipage: итог для %s — %d страниц, риск %s, нарушений %d",
        final_url, len(all_page_results),
        merged.get("risk"), len(merged.get("violations", [])),
    )
    return merged

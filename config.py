# config.py — все настройки приложения
import os
import re
import sys
from pathlib import Path


def _is_frozen() -> bool:
    """True, если запущено из PyInstaller / Nuitka-бандла."""
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def get_resource_dir() -> str:
    """Каталог read-only ресурсов (код, templates, static, prompts)."""
    if getattr(sys, "_MEIPASS", None):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_data_dir() -> str:
    """
    Writable data: history, reports, logs, settings, Playwright browsers.
    - SITECHECKER_DATA_DIR — явный override
    - frozen / SITECHECKER_DESKTOP=1 → %APPDATA%\\Sitechecker (Windows)
    - иначе — каталог проекта (dev)
    """
    override = os.getenv("SITECHECKER_DATA_DIR", "").strip()
    if override:
        return os.path.abspath(override)

    desktop = os.getenv("SITECHECKER_DESKTOP", "").strip() in ("1", "true", "yes")
    if _is_frozen() or desktop:
        if sys.platform == "win32":
            base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
            return os.path.join(base, "Sitechecker")
        return str(Path.home() / ".sitechecker")

    return get_resource_dir()


RESOURCE_DIR = get_resource_dir()
DATA_DIR = get_data_dir()
BASE_DIR = DATA_DIR  # writable «корень» для логов/отчётов (совместимость)

# Загружаем .env: сначала data dir (десктоп), затем каталог проекта
try:
    from dotenv import load_dotenv
    load_dotenv(Path(DATA_DIR) / ".env")
    if RESOURCE_DIR != DATA_DIR:
        load_dotenv(Path(RESOURCE_DIR) / ".env")
except ImportError:
    pass

# ── Сервер ────────────────────────────────────────────────────────────────────
# Chrome/Chromium блокируют порт 6000 (ERR_UNSAFE_PORT, X11).
# Десктоп: 127.0.0.1; сервер: 0.0.0.0. Порт: PORT=8080 python app.py
_default_host = "127.0.0.1" if (
    _is_frozen() or os.getenv("SITECHECKER_DESKTOP", "").strip() in ("1", "true", "yes")
) else "0.0.0.0"
HOST = os.getenv("HOST", _default_host)
PORT = int(os.getenv("PORT", "6001"))

# ── Краулинг: страницы, которые не проверяем ─────────────────────────────────
# Bitrix и др.: /auth/?register=yes, /login/, /personal/ и т.п.
SKIP_CRAWL_URL_RE = re.compile(
    r"(?:"
    r"logout|log[-_]?in|sign[-_]?in|"
    r"register|registration|sign[-_]?up|signup|"
    r"/auth(?:/|\?|$)|"
    r"authorize|authorization|oauth|sso|"
    r"forgot[-_]?password|reset[-_]?password|recover[-_]?password|"
    r"cart|checkout|basket|payment|"
    r"lk/|personal/|account|profile|admin|wp-admin|"
    r"feed|sitemap|\.xml|\.pdf|"
    r"tel:|mailto:|javascript:|#"
    r")",
    re.IGNORECASE,
)
AUTH_PAGE_QUERY_RE = re.compile(
    r"[?&](?:register|login|signin|signup|auth_form|forgot_password|"
    r"change_password|reset_password)(?:=|&|$)",
    re.IGNORECASE,
)


def should_skip_crawl_url(url: str) -> bool:
    """Пропускать URL авторизации, регистрации, корзины и служебные страницы."""
    if not url:
        return True
    return bool(SKIP_CRAWL_URL_RE.search(url) or AUTH_PAGE_QUERY_RE.search(url))

# ── Ограничения ───────────────────────────────────────────────────────────────
MAX_URLS = 50
MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 МБ

# ── HTTP (requests) ───────────────────────────────────────────────────────────
REQUEST_TIMEOUT = 10
REQUEST_VERIFY_SSL = False

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/121.0",
]

# ── Playwright ────────────────────────────────────────────────────────────────
PW_TIMEOUT_MS        = 20_000   # таймаут навигации (мс)
PW_WAIT_AFTER_MS     = 2_000   # ожидание после загрузки страницы (мс)
PW_HEADLESS          = True    # False — показывать браузер (для отладки)
PW_LOCALE            = "ru-RU"
PW_TIMEZONE          = "Europe/Moscow"

# Селекторы cookie-баннеров (для поиска элемента на странице)
PW_BANNER_SELECTORS = [
    "[class*='cookie']", "[class*='Cookie']",
    "[id*='cookie']",    "[id*='Cookie']",
    "[class*='consent']","[id*='consent']",
    "[class*='gdpr']",   "[id*='gdpr']",
    "[class*='banner']", "[id*='banner']",
    "[class*='notice']", "[id*='notice']",
]

# Тексты кнопки «Принять» (регистронезависимо)
PW_ACCEPT_TEXTS = [
    "принять все", "принять", "принимаю", "согласен", "согласиться",
    "accept all", "accept cookies", "accept", "ok", "agree",
    "я согласен", "ок",
]

# Тексты кнопки «Отказаться»
PW_DECLINE_TEXTS = [
    "отказаться", "отклонить", "не принимать", "только необходимые",
    "decline", "reject", "refuse", "deny", "manage cookies",
    "настройки cookie", "cookie settings",
]

# ── Политика ПД: ключевые слова ───────────────────────────────────────────────
POLICY_LINK_KEYWORDS = [
    # Полные фразы — текст ссылки
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
    # Транслитерированные URL-паттерны (/politika-pd/, /pd/, /privacy/ и т.п.)
    "politika-pd",
    "politika_pd",
    "politika-konfidencialnosti",
    "politika-konfidentsialnosti",
    "privacy-policy",
    "personal-data",
    "personalnye-dannye",
    # Короткие URL-сегменты (только href, не текст — но логика проверяет оба)
    "/politika",
    "/pd/",
    "/pd-policy",
    "/policy",
    "/conf",
]

POLICY_TEXT_KEYWORDS = [
    "персональные данные", "оператор", "обработка",
    "субъект", "согласие", "152-фз", "роскомнадзор",
]

# ── 11 обязательных разделов Политики ПД (ст.14 152-ФЗ) ──────────────────────
# Для каждого раздела собраны все реальные формулировки из типовых политик.
# Правило: достаточно найти ЛЮБОЕ из ключевых слов в тексте политики.
POLICY_REQUIRED_SECTIONS = {
    "цели обработки": [
        "цели обработки", "цель обработки", "цели сбора",
        "цели использования", "цель использования",
        "для каких целей", "в каких целях",
    ],
    "категории данных": [
        "категории персональных данных", "состав персональных данных",
        "перечень персональных данных", "какие данные",
        "состав данных", "виды персональных данных",
        "обрабатываемые персональные данные",
        "перечень обрабатываемых", "категории обрабатываемых",
    ],
    "сроки хранения": [
        "срок хранения", "сроки хранения", "срок обработки",
        "срок использования", "период хранения", "хранение данных",
        "условия хранения", "хранение персональных данных",
        "не позднее", "в течение",
    ],
    "права субъекта": [
        "права субъекта", "право на доступ", "право субъекта",
        "право на исправление", "право на удаление",
        "права пользователя", "права физического лица",
        "права субъектов", "ваши права",
        "вправе потребовать", "вправе запросить",
        "право на получение", "право на уточнение",
    ],
    "способы обработки": [
        "способы обработки", "способ обработки",
        "порядок обработки", "методы обработки",
        "автоматизированная обработка", "без использования средств автоматизации",
        "смешанная обработка",
    ],
    "третьи лица": [
        "третьи лица", "передача третьим", "третьим лицам",
        "передача персональных данных", "раскрытие персональных данных",
        "предоставление данных",
    ],
    "трансграничная передача": [
        "трансграничная передача", "передача за рубеж",
        "иностранное государство", "за пределы российской федерации",
        "за пределы рф", "иностранным организациям",
        "трансграничная", "не осуществляется трансграничная",
    ],
    "уничтожение данных": [
        "уничтожение", "удаление персональных данных",
        "блокирование персональных данных", "прекращение обработки",
        "уничтожаются", "удаляются", "уничтожение данных",
        "порядок уничтожения",
    ],
    "наименование оператора": [
        "наименование оператора", "полное наименование",
        "место нахождения", "адрес оператора",
        "сведения об операторе", "информация об операторе",
        "оператор персональных данных",
        "контактные данные оператора",
        "реквизиты оператора", "об операторе",
        "оператором является", "оператор —", "оператор:",
        "является оператором",
    ],
    "правовые основания": [
        "основания обработки", "правовые основания",
        "законные интересы", "основание для обработки",
        "правовое основание", "на основании",
        "согласно законодательству",
    ],
    "отзыв согласия": [
        "отзыв согласия", "порядок отзыва", "право отозвать",
        "отозвать согласие", "отзыва своего согласия",
        "вы можете отозвать", "отказаться от обработки",
        "прекращение обработки по требованию",
    ],
}

# ── Аналитика ─────────────────────────────────────────────────────────────────
ANALYTICS_SIGNATURES = {
    "Яндекс.Метрика":   ["mc.yandex.ru", "ym(", "yandex_metrika"],
    # Не использовать голый "dataLayer": Tilda и др. CMS вставляют
    # window.dataLayer = window.dataLayer || [] без GA/GTM.
    "Google Analytics": [
        "google-analytics.com",
        "googletagmanager.com",
        "gtag/js",
        "google_tag_manager",
        "GTM-",
        "ga('create'",
        'ga("create"',
        "gtag('config'",
        'gtag("config"',
        "gtag('js'",
        'gtag("js"',
    ],
    "Meta Pixel":       ["fbq(", "connect.facebook.net"],
    "Hotjar":           ["hotjar.com", "hj("],
    "Roistat":          ["roistat.com"],
    "LiveInternet":     ["counter.yadro.ru", "top-fwz", "liveinternet.ru"],
    "SimilarWeb":       ["similarweb.com"],
}

# Российская аналитика: данные в РФ, НЕ трансграничная передача.
# Остальные из ANALYTICS_SIGNATURES (GA, Meta Pixel, Hotjar, SimilarWeb) —
# иностранные и считаются трансграничной передачей.
RUSSIAN_ANALYTICS = frozenset({
    "Яндекс.Метрика",
    "LiveInternet",
    "Roistat",
})

# ── Иностранные ресурсы — трансграничная передача (штраф до 500 000 руб.) ─────
FOREIGN_RESOURCES = {
    "Google Fonts":     ["fonts.googleapis.com", "fonts.gstatic.com"],
    "Google reCAPTCHA": ["google.com/recaptcha", "recaptcha/api.js"],
    "Google Maps":      ["maps.googleapis.com", "maps.google.com/maps"],
    "YouTube embed":    ["youtube.com/embed", "youtube-nocookie.com"],
    "Twitter/X":        ["platform.twitter.com", "syndication.twitter.com"],
    "jsDelivr CDN":     ["cdn.jsdelivr.net"],
    "Cloudflare CDN":   ["cdnjs.cloudflare.com"],
    "jQuery CDN":       ["code.jquery.com", "ajax.googleapis.com"],
    "Facebook SDK":     ["connect.facebook.net", "facebook.com/plugins"],
}

# ── Cookie-баннер ─────────────────────────────────────────────────────────────
COOKIE_BANNER_KEYWORDS = [
    "принять", "принимаю", "согласен",
    "accept cookies", "accept all", "куки", "cookies", "я соглашаюсь",
]

COOKIE_DECLINE_KEYWORDS = [
    "отказаться", "отклонить", "не принимать", "отказ от cookie",
    "decline", "reject", "refuse", "deny",
    "только необходимые", "только обязательные",
    "настройки cookie", "cookie settings", "manage cookies",
]

# ── Поля форм ─────────────────────────────────────────────────────────────────
PD_FIELD_KEYWORDS = [
    "phone", "tel", "телефон", "мобильный",
    "email", "e-mail", "почта",
    "name", "имя", "фамилия", "surname", "firstname", "lastname",
    "address", "адрес", "company", "компания", "контакт", "contact",
]

# ── Реквизиты согласия (152-ФЗ) ───────────────────────────────────────────────
CONSENT_REQUISITES = {
    "оператор":  ["оператор", "наименование"],
    "цели":      ["цель", "цели обработки"],
    "перечень":  ["перечень", "категории данных", "состав данных"],
    "срок":      ["срок", "период действия"],
    "отзыв":     ["отзыв", "отозвать", "отзыва согласия"],
}

# Чекбокс совмещён с рассылкой/офертой — нарушение
CONSENT_MIXED_KEYWORDS = [
    "рассылк", "newsletter", "новост", "акци", "оферт",
    "пользовательск", "условия использования", "terms", "marketing",
]

# Паттерны текстового согласия (без чекбокса) — «Нажимая «Отправить»...»
# Такой вид согласия не соответствует 152-ФЗ (нет явного чекбокса),
# но его наличие лучше чем полное отсутствие упоминания ПД
TEXT_CONSENT_PATTERNS = [
    r"нажима[яь][^.]{0,80}соглаша",
    r"нажима[яь][^.]{0,80}согласи",
    r"отправля[яь][^.]{0,80}соглаша",
    r"кликая[^.]{0,80}соглаша",
    r"подтвержда[яь][^.]{0,80}соглаша",
    r"нажимая[^.]{0,80}обработк",
    r"clicking[^.]{0,80}agree",
    r"by submitting[^.]{0,80}consent",
]

# Ключевые слова кнопок отправки (для поиска форм без тега <form>)
SUBMIT_BUTTON_TEXTS = [
    "отправить", "отправить заявку", "отправить сообщение",
    "заказать", "заказать звонок", "перезвоните",
    "записаться", "получить консультацию", "узнать стоимость",
    "связаться", "оставить заявку", "подать заявку",
    "submit", "send", "send message", "get a quote",
    "зарегистрироваться", "войти", "подписаться",
]

# ── Реквизиты оператора ───────────────────────────────────────────────────────
OPERATOR_KEYWORDS = [
    # Организационно-правовые формы (с пробелом и без — для «ООО«Ромашка»»)
    "ооо ", "ооо«", "ип ", "оао ", "зао ", "пао ", "ао ",
    "общество с ограниченной", "индивидуальный предприниматель",
    # Реквизиты
    "огрн", "инн", "кпп",
    # Адрес
    "юридический адрес", "юр. адрес", "юр.адрес",
    "место нахождения", "адрес регистрации", "зарегистрирован",
    "фактический адрес", "почтовый адрес",
    # Контакты оператора
    "электронная почта оператора", "телефон оператора",
    "контакты оператора", "связаться с оператором",
]

# ── AI / DeepSeek API ────────────────────────────────────────────────────────
# Ключ берётся из .env / admin_settings.json (get_ai_config).
# max_tokens — лимит длины *ответа* модели. При 8000 документ часто обрывался
# на «ПЛАН ДЕЙСТВИЙ» / «ИТОГ» (owner) или на середине ТЗ (dev/combined).
# Переопределение: AI_MAX_TOKENS=32768 в .env
#DEEPSEEK_API_KEY    = os.getenv("DEEPSEEK_API_KEY", "")
#DEEPSEEK_BASE_URL   = "https://api.deepseek.com"
#DEEPSEEK_MODEL      = "deepseek-chat"
DEEPSEEK_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", os.getenv("DEEPSEEK_MAX_TOKENS", "16384")))
DEEPSEEK_TEMPERATURE = 0.3   # Ниже = более строгий/юридический стиль

# ── AI API ──────────────────────────────────────────────────────────────
# Параметры читаются из admin_settings.json с фолбэком на env-переменные.
# admin_settings.json обновляется из админ-панели (вкладка «AI модель»).
import json as _json

# ── Пути (data writable / resources read-only) ───────────────────────────────
UPLOAD_DIR      = os.path.join(DATA_DIR, "uploads")
REPORT_DIR      = os.path.join(DATA_DIR, "reports")
REC_DIR         = os.path.join(DATA_DIR, "recommendations")
SCREENSHOTS_DIR = os.path.join(DATA_DIR, "screenshots")
LOG_FILE        = os.path.join(DATA_DIR, "errors.log")
HISTORY_DB_PATH = os.path.join(DATA_DIR, "history.db")
ADMIN_SETTINGS_FILE = os.path.join(DATA_DIR, "admin_settings.json")
PLAYWRIGHT_BROWSERS_DIR = os.path.join(DATA_DIR, "playwright-browsers")
REPORT_TTL      = 3600  # 1 час

# Фолбэк: settings рядом с проектом (dev), если в data dir ещё нет
_ADMIN_SETTINGS_FILE = ADMIN_SETTINGS_FILE
_DEV_ADMIN_SETTINGS = os.path.join(RESOURCE_DIR, "admin_settings.json")


def get_admin_settings_path() -> str:
    """Путь к admin_settings.json (data dir; иначе dev-копия в resource)."""
    if os.path.exists(ADMIN_SETTINGS_FILE):
        return ADMIN_SETTINGS_FILE
    if os.path.exists(_DEV_ADMIN_SETTINGS) and RESOURCE_DIR != DATA_DIR:
        return _DEV_ADMIN_SETTINGS
    return ADMIN_SETTINGS_FILE


def get_ai_config() -> dict:
    """Возвращает {api_key, base_url, model} — из admin_settings.json или env."""
    api_key = base_url = model = ""
    try:
        path = get_admin_settings_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as _f:
                _s = _json.load(_f)
            _ai = _s.get("ai_model", {})
            api_key  = _ai.get("api_key", "")
            base_url = _ai.get("base_url", "")
            model    = _ai.get("model", "")
    except Exception:
        pass
    return {
        "api_key":  api_key  or os.getenv("HUBRIS_API_KEY", ""),
        "base_url": base_url or os.getenv("HUBRIS_BASE_URL", ""),
        "model":    model    or os.getenv("HUBRIS_MODEL", ""),
    }

# Фолбэк-константы для обратной совместимости (не используются напрямую)
HUBRIS_API_KEY  = os.getenv("HUBRIS_API_KEY")
HUBRIS_BASE_URL = os.getenv("HUBRIS_BASE_URL")
HUBRIS_MODEL    = os.getenv("HUBRIS_MODEL")

for _d in (DATA_DIR, UPLOAD_DIR, REPORT_DIR, REC_DIR, SCREENSHOTS_DIR, PLAYWRIGHT_BROWSERS_DIR):
    os.makedirs(_d, exist_ok=True)

# Playwright: браузеры в data dir (скачиваются при первом использовании)
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", PLAYWRIGHT_BROWSERS_DIR)

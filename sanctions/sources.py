# sanctions/sources.py
# База санкционных, ограниченных и юридически рискованных ресурсов
# для российского правового поля.

from dataclasses import dataclass, field


@dataclass
class SanctionEntry:
    """Описание одного санкционного/ограниченного ресурса."""
    name:        str           # Отображаемое название
    category:    str           # Категория (см. CATEGORIES)
    severity:    str           # critical | high | medium | low
    patterns:    list[str]     # re-паттерны для поиска в HTML/заголовках
    domains:     list[str]     # точные домены для DNS/URL-проверки
    legal_basis: str           # правовое основание
    description: str           # краткое описание угрозы
    advice:      str           # что делать


CATEGORIES = {
    "meta":     "Meta (признана экстремистской)",
    "banned":   "Заблокированные в РФ",
    "payment":  "Ограниченные платёжные системы",
    "cdn":      "Иностранные CDN / инфраструктура",
    "ads":      "Иностранные рекламные сети",
    "widgets":  "Иностранные виджеты и чаты",
    "social":   "Иностранные соцсети (кнопки/виджеты)",
    "analytics":"Иностранная аналитика",
}

SEVERITY_LABEL = {
    "critical": ("🔴 КРИТИЧНО",  "Деятельность Meta запрещена в РФ"),
    "high":     ("🟠 ВЫСОКИЙ",   "Ресурс заблокирован / ограничен Роскомнадзором"),
    "medium":   ("🟡 СРЕДНИЙ",   "Передача данных иностранной организации"),
    "low":      ("🔵 ВНИМАНИЕ",  "Рекомендуется замена на отечественный аналог"),
}

DATABASE: list[SanctionEntry] = [

    # ── Meta (экстремизм) ─────────────────────────────────────────────────────
    SanctionEntry(
        name        = "Facebook",
        category    = "meta",
        severity    = "critical",
        patterns    = [
            r'facebook\.com|fb\.com|fbcdn\.net',
            r'connect\.facebook|graph\.facebook',
            r'facebook-jssdk|fb-root|FB\.init',
            r'data-href=["\']https://www\.facebook',
        ],
        domains     = ["facebook.com", "fb.com", "fbcdn.net", "connect.facebook.net"],
        legal_basis = "Решение Тверского суда г. Москвы от 21.03.2022; Meta Platforms признана экстремистской организацией",
        description = "На сайте обнаружены ресурсы Facebook (Meta). Организация Meta Platforms признана экстремистской и запрещена в РФ.",
        advice      = "Удалите все ссылки, кнопки «Поделиться», Like-виджеты и пиксели Facebook. Замените на ВКонтакте или Одноклассники.",
    ),
    SanctionEntry(
        name        = "Instagram",
        category    = "meta",
        severity    = "critical",
        patterns    = [
            r'instagram\.com|cdninstagram\.com',
            r'instagram-feed|instagram-widget|instafeed',
            r'//www\.instagram\.com/p/',
        ],
        domains     = ["instagram.com", "cdninstagram.com"],
        legal_basis = "Решение Тверского суда г. Москвы от 21.03.2022",
        description = "На сайте обнаружены ресурсы Instagram (Meta). Организация признана экстремистской и запрещена в РФ.",
        advice      = "Удалите встроенные посты Instagram, виджеты ленты, кнопки Follow. Используйте ВКонтакте или собственную галерею.",
    ),
    SanctionEntry(
        name        = "WhatsApp",
        category    = "meta",
        severity    = "critical",
        patterns    = [
            r'whatsapp\.com|wa\.me|api\.whatsapp',
            r'whatsapp-button|whatsapp-chat|whatsapp-widget',
        ],
        domains     = ["whatsapp.com", "wa.me", "web.whatsapp.com"],
        legal_basis = "Решение Тверского суда г. Москвы от 21.03.2022; Meta Platforms признана экстремистской организацией",
        description = "На сайте обнаружена ссылка или виджет WhatsApp (Meta). Использование сервисов Meta связано с правовым риском.",
        advice      = "Замените на Telegram, ВКонтакте Messenger или российский мессенджер. Для кнопки обратной связи используйте телефон или форму.",
    ),
    SanctionEntry(
        name        = "Threads",
        category    = "meta",
        severity    = "critical",
        patterns    = [r'threads\.net', r'threads-widget'],
        domains     = ["threads.net"],
        legal_basis = "Решение Тверского суда г. Москвы от 21.03.2022 (Meta Platforms)",
        description = "На сайте обнаружены ресурсы Threads — сети Meta (признана экстремистской).",
        advice      = "Удалите все ссылки и виджеты Threads.",
    ),
    SanctionEntry(
        name        = "Meta Pixel / Meta Ads",
        category    = "meta",
        severity    = "critical",
        patterns    = [
            r'connect\.facebook\.net/[a-z_]+/fbevents',
            r'fbq\(|_fbq\b|facebook-pixel',
            r'meta-pixel|fbpixel',
        ],
        domains     = ["connect.facebook.net"],
        legal_basis = "Решение Тверского суда г. Москвы от 21.03.2022; передача данных запрещённой организации",
        description = "На сайте установлен Meta Pixel (Facebook Pixel). Счётчик передаёт данные посетителей в Meta — запрещённую в РФ организацию.",
        advice      = "Немедленно удалите Meta Pixel. Используйте Яндекс.Метрику, VK Pixel или myTarget для ретаргетинга.",
    ),

    # ── Заблокированные в РФ ─────────────────────────────────────────────────
    SanctionEntry(
        name        = "LinkedIn",
        category    = "banned",
        severity    = "high",
        patterns    = [
            r'linkedin\.com|licdn\.com',
            r'linkedin-badge|linkedin-share|IN\.init',
        ],
        domains     = ["linkedin.com", "licdn.com", "platform.linkedin.com"],
        legal_basis = "Решение Московского городского суда от 04.08.2016; заблокирован Роскомнадзором",
        description = "LinkedIn заблокирован в России с 2016 года по решению суда за нарушение закона о локализации данных. Виджеты не загрузятся у российских пользователей.",
        advice      = "Удалите кнопки LinkedIn. Укажите профиль компании на hh.ru, SuperJob или используйте прямую ссылку с предупреждением.",
    ),
    SanctionEntry(
        name        = "TikTok",
        category    = "banned",
        severity    = "high",
        patterns    = [
            r'tiktok\.com|tiktokcdn\.com|tiktok-embed',
            r'TikTokEmbed|tiktok-widget',
        ],
        domains     = ["tiktok.com", "tiktokcdn.com"],
        legal_basis = "Рекомендация Роскомнадзора; ограниченный доступ для ряда провайдеров",
        description = "TikTok имеет нестабильный доступ в РФ и собирает данные для ByteDance (КНР). Встроенные видео могут не загружаться.",
        advice      = "Замените встроенные TikTok-видео на прямые файлы или ВКонтакте Видео.",
    ),
    SanctionEntry(
        name        = "Twitter / X",
        category    = "banned",
        severity    = "high",
        patterns    = [
            r'://twitter\.com|://t\.co|://twimg\.com|://x\.com',
            r'twitter\.com/[^\s"\'<>]+|x\.com/[^\s"\'<>]+',
            r't\.co/[A-Za-z0-9]+',
            r'twitter-share|tweet-button|twttr\b',
            r'platform\.twitter\.com',
        ],
        domains     = ["twitter.com", "x.com", "t.co", "platform.twitter.com", "twimg.com"],
        legal_basis = "Замедление трафика Роскомнадзором (с 2021); нестабильный доступ",
        description = "Twitter/X имеет нестабильный доступ в РФ. Виджеты и кнопки могут не загружаться, замедляя сайт.",
        advice      = "Замените виджеты Twitter на статичные ссылки. Для новостей используйте Telegram или ВКонтакте.",
    ),
    SanctionEntry(
        name        = "Pinterest",
        category    = "banned",
        severity    = "medium",
        patterns    = [r'pinterest\.com|pinimg\.com|pinterest-widget', r'PinUtils'],
        domains     = ["pinterest.com", "pinimg.com", "widgets.pinterest.com"],
        legal_basis = "Ограниченный доступ у части провайдеров в РФ",
        description = "Pinterest недоступен у части российских пользователей. Виджеты Pin It могут блокировать загрузку страницы.",
        advice      = "Уберите виджеты Pin It. Изображения размещайте на собственном сервере.",
    ),

    # ── Платёжные системы ─────────────────────────────────────────────────────
    SanctionEntry(
        name        = "Visa / Mastercard (внешние виджеты)",
        category    = "payment",
        severity    = "medium",
        patterns    = [
            r'visa\.com|mastercard\.com',
            r'secure\.visa|securecode\.mastercard',
            r'3dsecure\.visa|verified\.by\.visa',
        ],
        domains     = ["visa.com", "mastercard.com", "secure.visa.com"],
        legal_basis = "Visa и Mastercard приостановили работу в РФ с марта 2022",
        description = "Обнаружены внешние ресурсы Visa/Mastercard. С марта 2022 эти системы не обрабатывают транзакции в РФ. Внешние скрипты могут не загружаться.",
        advice      = "Уберите внешние виджеты Visa/Mastercard. Используйте Мир, СБП или российские платёжные шлюзы (ЮKassa, Тинькофф).",
    ),
    SanctionEntry(
        name        = "PayPal",
        category    = "payment",
        severity    = "high",
        patterns    = [r'paypal\.com|paypalobjects\.com', r'paypal-button|paypal-checkout'],
        domains     = ["paypal.com", "paypalobjects.com", "checkout.paypal.com"],
        legal_basis = "PayPal приостановил работу в РФ с марта 2022",
        description = "PayPal полностью остановил работу в России. Кнопки оплаты и виджеты не работают и создают негативный UX.",
        advice      = "Удалите кнопки PayPal. Подключите Mir Pay, ЮKassa или другой российский эквайринг.",
    ),
    SanctionEntry(
        name        = "Stripe",
        category    = "payment",
        severity    = "high",
        patterns    = [r'stripe\.com|js\.stripe\.com|stripe-js', r'Stripe\('],
        domains     = ["stripe.com", "js.stripe.com"],
        legal_basis = "Stripe прекратил работу с российскими бизнесами в 2022",
        description = "Stripe прекратил обслуживание российских компаний. Скрипты Stripe будут генерировать ошибки.",
        advice      = "Замените Stripe на ЮKassa, Тинькофф Кассу или другой российский платёжный шлюз.",
    ),

    # ── Иностранные CDN и инфраструктура ─────────────────────────────────────
    SanctionEntry(
        name        = "Google Fonts",
        category    = "cdn",
        severity    = "medium",
        patterns    = [
            r'fonts\.googleapis\.com|fonts\.gstatic\.com',
        ],
        domains     = ["fonts.googleapis.com", "fonts.gstatic.com"],
        legal_basis = "152-ФЗ: передача IP-адресов на серверы Google (США); Роскомнадзор фиксирует трансграничную передачу",
        description = "Google Fonts загружается с серверов Google (США). Это передача IP-адресов пользователей иностранной компании без явного согласия — нарушение 152-ФЗ.",
        advice      = "Скачайте шрифты и разместите на собственном сервере. Используйте font-display: swap для производительности.",
    ),
    SanctionEntry(
        name        = "YouTube Embed",
        category    = "cdn",
        severity    = "medium",
        patterns    = [
            r'youtube\.com/embed|youtu\.be|ytimg\.com',
            r'youtube-nocookie\.com',
        ],
        domains     = ["youtube.com", "youtu.be", "ytimg.com", "youtube-nocookie.com"],
        legal_basis = "152-ФЗ: передача IP-адресов на серверы Google при загрузке видео",
        description = "Встроенные видео YouTube передают IP-адреса пользователей на серверы Google (США) при загрузке страницы — даже до нажатия Play.",
        advice      = "Используйте lazy-load (загрузка iframe только по клику), или замените на RuTube / VK Видео.",
    ),
    SanctionEntry(
        name        = "Cloudflare CDN",
        category    = "cdn",
        severity    = "low",
        patterns    = [r'cdnjs\.cloudflare\.com', r'cloudflare\.com/ajax'],
        domains     = ["cdnjs.cloudflare.com"],
        legal_basis = "Передача технических данных через инфраструктуру США",
        description = "Скрипты загружаются через Cloudflare CDN (США). Технические данные запросов проходят через американские серверы.",
        advice      = "Скачайте JS-библиотеки и разместите локально. Это также ускорит загрузку.",
    ),
    SanctionEntry(
        name        = "jsDelivr / unpkg",
        category    = "cdn",
        severity    = "low",
        patterns    = [r'cdn\.jsdelivr\.net|unpkg\.com'],
        domains     = ["cdn.jsdelivr.net", "unpkg.com"],
        legal_basis = "Передача технических данных через зарубежную инфраструктуру",
        description = "Библиотеки загружаются через зарубежные CDN. Данные запросов уходят за рубеж.",
        advice      = "Разместите библиотеки на собственном сервере или используйте российские CDN.",
    ),

    # ── Рекламные сети ────────────────────────────────────────────────────────
    SanctionEntry(
        name        = "Google Ads / DoubleClick",
        category    = "ads",
        severity    = "medium",
        patterns    = [
            r'doubleclick\.net|googleadservices\.com',
            r'googlesyndication\.com|adservice\.google',
            r'gtag\(.*AW-|google_conversion',
        ],
        domains     = ["doubleclick.net", "googleadservices.com", "googlesyndication.com"],
        legal_basis = "152-ФЗ: передача данных о поведении пользователей в Google (США)",
        description = "Скрипты Google Ads/DoubleClick отслеживают конверсии и передают данные о пользователях в Google (США).",
        advice      = "Проверьте необходимость Google Ads. Для рекламы в РФ используйте Яндекс.Директ или VK Реклама.",
    ),
    SanctionEntry(
        name        = "Amazon Ads / Associates",
        category    = "ads",
        severity    = "low",
        patterns    = [r'amazon-adsystem\.com|assoc-amazon\.com', r'amazon\.com/gp/product'],
        domains     = ["amazon-adsystem.com", "assoc-amazon.com"],
        legal_basis = "Передача данных через инфраструктуру Amazon (США)",
        description = "Рекламные скрипты Amazon передают данные на серверы в США.",
        advice      = "Замените на партнёрские программы российских маркетплейсов (Wildberries, Ozon).",
    ),

    # ── Иностранные виджеты и чаты ────────────────────────────────────────────
    SanctionEntry(
        name        = "Intercom",
        category    = "widgets",
        severity    = "medium",
        patterns    = [r'intercom\.io|intercomcdn\.com', r'Intercom\(|intercomSettings'],
        domains     = ["intercom.io", "intercomcdn.com", "api-iam.intercom.io"],
        legal_basis = "152-ФЗ: передача персональных данных пользователей в компанию Intercom (США)",
        description = "Виджет Intercom передаёт данные посетителей (IP, email, история переписки) на серверы в США.",
        advice      = "Замените на российские чат-решения: Envybox, CallbackHunter, JivoSite (российские серверы).",
    ),
    SanctionEntry(
        name        = "Zendesk",
        category    = "widgets",
        severity    = "medium",
        patterns    = [r'zendesk\.com|zopim\.com|zdassets\.com', r'zE\(|zESettings'],
        domains     = ["zendesk.com", "zopim.com", "static.zdassets.com"],
        legal_basis = "152-ФЗ: передача данных в Zendesk Inc. (США)",
        description = "Виджет Zendesk Chat передаёт персональные данные пользователей в США.",
        advice      = "Рассмотрите замену на Битрикс24 CRM или российские helpdesk-решения.",
    ),
    SanctionEntry(
        name        = "HubSpot",
        category    = "widgets",
        severity    = "medium",
        patterns    = [r'hubspot\.com|hs-scripts\.com|hsforms\.com', r'_hsq\b|HubSpot'],
        domains     = ["hubspot.com", "hs-scripts.com", "hsforms.com"],
        legal_basis = "152-ФЗ: передача персональных данных в HubSpot Inc. (США)",
        description = "Скрипты HubSpot собирают и передают данные о поведении пользователей в США.",
        advice      = "Замените на AmoCRM, Битрикс24 или другую российскую CRM.",
    ),
    SanctionEntry(
        name        = "Drift",
        category    = "widgets",
        severity    = "medium",
        patterns    = [r'drift\.com|driftt\.com', r'drift\.load|driftt\.com'],
        domains     = ["drift.com", "driftt.com"],
        legal_basis = "152-ФЗ: передача персональных данных в Drift (США)",
        description = "Чат-виджет Drift передаёт данные посетителей на американские серверы.",
        advice      = "Замените на российский онлайн-чат.",
    ),

    # ── Соцсети (кнопки шаринга) ──────────────────────────────────────────────
    SanctionEntry(
        name        = "AddThis / ShareThis",
        category    = "social",
        severity    = "medium",
        patterns    = [r'addthis\.com|sharethis\.com', r'addthis_widget|sharethis\.js'],
        domains     = ["addthis.com", "sharethis.com"],
        legal_basis = "152-ФЗ: передача данных о поведении пользователей за рубеж",
        description = "Виджеты AddThis/ShareThis отслеживают поведение пользователей на всех сайтах сети и передают данные в США.",
        advice      = "Замените на статичные ссылки шаринга. Добавьте кнопки ВКонтакте и Telegram вместо иностранных виджетов.",
    ),
    SanctionEntry(
        name        = "Snapchat Pixel",
        category    = "social",
        severity    = "high",
        patterns    = [r'sc-static\.net|snapchat\.com/p/', r'snaptr\(|snap-pixel'],
        domains     = ["sc-static.net", "tr.snapchat.com"],
        legal_basis = "152-ФЗ; Snapchat заблокирован у части российских провайдеров",
        description = "Пиксель Snapchat передаёт данные о конверсиях в Snap Inc. (США). Сервис имеет ограниченный доступ в РФ.",
        advice      = "Удалите пиксель Snapchat. Используйте Яндекс.Метрику или VK Pixel для аналитики конверсий.",
    ),
]

# auth/sources.py
# База сервисов авторизации: разрешённые (российские) и запрещённые (иностранные)

from dataclasses import dataclass, field


@dataclass
class AuthEntry:
    """Описание сервиса авторизации."""
    id:           str = ""          # Уникальный идентификатор
    name:         str = ""          # Отображаемое название
    category:     str = ""          # auth_allowed | auth_foreign | rec_tech
    auth_type:    str = ""          # oauth | phone | biometric | password | captcha | social
    allowed:      bool = True       # True = разрешён, False = запрещён/risks
    risk:         str = "low"       # critical | high | medium | low
    patterns:     list[str] = field(default_factory=list)
    domains:      list[str] = field(default_factory=list)
    legal_basis:  str = ""
    description:  str = ""
    advice:       str = ""
    fine_citizens: int = 0
    fine_officials: int = 0
    fine_legal:   int = 0
    article:      str = ""
    absence_check: bool = False     # True = нарушение = отсутствие при наличии предпосылок


CATEGORIES = {
    "russian_auth":     "Российские сервисы аутентификации",
    "foreign_oauth":    "Иностранные OAuth (рискованные)",
    "rec_tech":         "Рекомендательные технологии",
    "phone":            "Телефонная аутентификация",
    "captcha":          "CAPTCHA и защита от ботов",
    "password":         "Политика паролей",
    "session":          "Управление сессиями",
    "consent":          "Согласия при регистрации",
    "mfa":              "Многофакторная аутентификация",
}

SEVERITY_LABEL = {
    "critical": ("🔴 КРИТИЧНО",  "Использование запрещённого сервиса"),
    "high":     ("🟠 ВЫСОКИЙ",   "Иностранный сервис без российской альтернативы"),
    "medium":   ("🟡 СРЕДНИЙ",   "Несоответствие требованиям к безопасности"),
    "low":      ("🔵 ВНИМАНИЕ",  "Рекомендуется улучшение"),
}

# ── Разрешённые российские сервисы ──────────────────────────────────────────

RUSSIAN_SERVICES = [
    AuthEntry(
        id          = "gosuslugi_esia",
        name        = "Госуслуги (ЕСИА)",
        category    = "auth_allowed",
        auth_type   = "oauth",
        allowed     = True,
        risk        = "low",
        patterns    = [
            r'esia\.gosuslugi\.ru|gosuslugi\.ru',
            r'account\.gosuslugi\.ru|esia-login',
            r'data\.mos\.ru.*esia|esia\.mos\.ru',
        ],
        domains     = ["esia.gosuslugi.ru", "gosuslugi.ru", "esia.mos.ru"],
        legal_basis = "Указ Президента РФ от 01.11.2024 №788; Постановление Правительства от 28.09.2024 №1335",
        description = "Единая система идентификации и аутентификации (ЕСИА) — государственный сервис подтверждения личности.",
        advice      = "Рекомендуется: интеграция с ЕСИА для подтверждения личности пользователей.",
    ),
    AuthEntry(
        id          = "ebs",
        name        = "Единая биометрическая система (ЕБС)",
        category    = "auth_allowed",
        auth_type   = "biometric",
        allowed     = True,
        risk        = "low",
        patterns    = [
            r'biometric\.ru|biometria\.ru',
            r'esia-biometric|gosuslugi.*biometric',
        ],
        domains     = ["biometric.ru", "biometria.ru"],
        legal_basis = "Федеральный закон от 19.07.2022 №254-ФЗ",
        description = "Единая биометрическая система для идентификации по биометрическим данным.",
        advice      = "Для высокорисковых сервисов рекомендуется интеграция с ЕБС.",
    ),
    AuthEntry(
        id          = "gosuslugi_auth",
        name        = "Платформа «Госуслуги.Авторизация»",
        category    = "auth_allowed",
        auth_type   = "oauth",
        allowed     = True,
        risk        = "low",
        patterns    = [
            r'account\.gosuslugi\.ru/nca',
            r'oauth2\.0.*gosuslugi',
            r'autorization\.gosuslugi',
        ],
        domains     = ["account.gosuslugi.ru"],
        legal_basis = "Приказ Минцифры от 18.08.2023 №471",
        description = "Новая платформа авторизации Госуслуг для коммерческих сервисов.",
        advice      = "Используйте «Госуслуги.Авторизация» для подтверждения возраста и личности.",
    ),
    AuthEntry(
        id          = "sber_id",
        name        = "Сбер ID",
        category    = "auth_allowed",
        auth_type   = "oauth",
        allowed     = True,
        risk        = "low",
        patterns    = [
            r'id\.sber\.ru|sberid',
            r'login\.sber\.ru|sber\.ru.*auth',
            r'api\.sber\.ru.*oauth',
        ],
        domains     = ["id.sber.ru", "login.sber.ru"],
        legal_basis = "Российский банк, лицензия ЦБ РФ",
        description = "Сервис аутентификации Сбербанка — российский OAuth-провайдер.",
        advice      = "Допустимая российская альтернатива иностранным OAuth.",
    ),
    AuthEntry(
        id          = "yandex_id",
        name        = "Яндекс ID",
        category    = "auth_allowed",
        auth_type   = "oauth",
        allowed     = True,
        risk        = "low",
        patterns    = [
            r'passport\.yandex\.ru|yandex\.ru.*oauth',
            r'login\.yandex\.ru|oauth\.yandex\.ru',
        ],
        domains     = ["passport.yandex.ru", "login.yandex.ru"],
        legal_basis = "Российская компания, локализация данных в РФ",
        description = "Сервис аутентификации Яндекса — российский OAuth-провайдер.",
        advice      = "Допустимая российская альтернатива иностранным OAuth.",
    ),
    AuthEntry(
        id          = "vk_id",
        name        = "ВКонтакте ID",
        category    = "auth_allowed",
        auth_type   = "social",
        allowed     = True,
        risk        = "low",
        patterns    = [
            r'vk\.com.*oauth|oauth\.vk\.com',
            r'login\.vk\.com|api\.vk\.com.*auth',
        ],
        domains     = ["oauth.vk.com", "login.vk.com"],
        legal_basis = "Российская компания, данные в РФ",
        description = "Сервис аутентификации ВКонтакте — российский OAuth-провайдер.",
        advice      = "Допустимая российская альтернатива иностранным соцсетям.",
    ),
]

# ── Запрещённые / рискованные иностранные сервисы ───────────────────────────

FOREIGN_SERVICES = [
    AuthEntry(
        id          = "google_oauth",
        name        = "Google OAuth",
        category    = "auth_foreign",
        auth_type   = "oauth",
        allowed     = False,
        risk        = "critical",
        patterns    = [
            r'accounts\.google\.com|googleapis\.com.*oauth',
            r'google.*sign.in|gsi.*client|google.*auth',
            r'apis\.google\.com.*auth|myaccount\.google',
        ],
        domains     = ["accounts.google.com", "apis.google.com"],
        legal_basis = "152-ФЗ: передача данных аутентификации на серверы Google (США)",
        description = "Google OAuth передаёт данные аутентификации на серверы за рубежом. Использование рискованно с точки зрения 152-ФЗ.",
        advice      = "Замените на Госуслуги, Сбер ID, Яндекс ID или ВКонтакте ID.",
        fine_citizens = 10000,
        fine_officials = 30000,
        fine_legal = 500000,
        article = "ст. 13.55 КоАП",
    ),
    AuthEntry(
        id          = "facebook_login",
        name        = "Facebook Login",
        category    = "auth_foreign",
        auth_type   = "oauth",
        allowed     = False,
        risk        = "critical",
        patterns    = [
            r'facebook\.com/login|connect\.facebook\.net',
            r'fb-login|facebook.*auth|login\.facebook',
        ],
        domains     = ["facebook.com", "connect.facebook.net"],
        legal_basis = "Meta признана экстремистской организацией (решение Тверского суда 21.03.2022)",
        description = "Facebook Login связан с Meta — запрещённой в РФ организацией. Использование незаконно.",
        advice      = "НЕМЕДЛЕННО удалите. Используйте российские OAuth-провайдеры.",
        fine_citizens = 10000,
        fine_officials = 30000,
        fine_legal = 500000,
        article = "ст. 13.55 КоАП",
    ),
    AuthEntry(
        id          = "apple_signin",
        name        = "Apple Sign In",
        category    = "auth_foreign",
        auth_type   = "oauth",
        allowed     = False,
        risk        = "critical",
        patterns    = [
            r'appleid\.apple\.com|apple.*sign.in',
            r'signin\.apple\.com|appleid\.apple\.ru',
        ],
        domains     = ["appleid.apple.com", "signin.apple.com"],
        legal_basis = "152-ФЗ: передача данных аутентификации на серверы Apple (США)",
        description = "Apple Sign In передаёт данные аутентификации на серверы Apple (США). Нет российской инфраструктуры.",
        advice      = "Замените на Госуслуги или другой российский сервис.",
        fine_citizens = 10000,
        fine_officials = 30000,
        fine_legal = 500000,
        article = "ст. 13.55 КоАП",
    ),
    AuthEntry(
        id          = "microsoft_account",
        name        = "Microsoft Account / Azure AD",
        category    = "auth_foreign",
        auth_type   = "oauth",
        allowed     = False,
        risk        = "critical",
        patterns    = [
            r'login\.microsoftonline\.com|microsoft\.com.*oauth',
            r'login\.live\.com|accounts\.microsoft\.com',
            r'azure.*ad|msal\.js|microsoft.*auth',
        ],
        domains     = ["login.microsoftonline.com", "login.live.com"],
        legal_basis = "152-ФЗ: передача данных аутентификации на серверы Microsoft (США)",
        description = "Microsoft Account / Azure AD передаёт данные аутентификации на серверы Microsoft (США).",
        advice      = "Замените на Госуслуги, Сбер ID или Яндекс ID.",
        fine_citizens = 10000,
        fine_officials = 30000,
        fine_legal = 500000,
        article = "ст. 13.55 КоАП",
    ),
    AuthEntry(
        id          = "github_oauth",
        name        = "GitHub OAuth",
        category    = "auth_foreign",
        auth_type   = "oauth",
        allowed     = False,
        risk        = "high",
        patterns    = [
            r'github\.com/login|github\.com.*oauth',
            r'api\.github\.com.*authorize',
        ],
        domains     = ["github.com"],
        legal_basis = "152-ФЗ: передача данных на серверы Microsoft (США)",
        description = "GitHub OAuth передаёт данные на серверы Microsoft (США). Использование рискованно для пользователей РФ.",
        advice      = "Для внутренних сервисов — допустимо. Для публичных — замените.",
        fine_citizens = 10000,
        fine_officials = 30000,
        fine_legal = 500000,
        article = "ст. 13.55 КоАП",
    ),
    AuthEntry(
        id          = "discord_oauth",
        name        = "Discord OAuth",
        category    = "auth_foreign",
        auth_type   = "oauth",
        allowed     = False,
        risk        = "high",
        patterns    = [
            r'discord\.com.*oauth|discord\.com.*authorize',
            r'api\.discord\.com.*oauth|discord.*auth',
        ],
        domains     = ["discord.com", "discordapp.com"],
        legal_basis = "152-ФЗ: передача данных аутентификации на серверы Discord (США)",
        description = "Discord OAuth передаёт данные аутентификации на серверы Discord (США). Нет российской инфраструктуры.",
        advice      = "Замените на Госуслуги или другой российский сервис.",
        fine_citizens = 10000,
        fine_officials = 30000,
        fine_legal = 500000,
        article = "ст. 13.55 КоАП",
    ),
    AuthEntry(
        id          = "telegram_login",
        name        = "Telegram Login",
        category    = "auth_foreign",
        auth_type   = "social",
        allowed     = False,
        risk        = "critical",
        patterns    = [
            r'telegram\.org.*login|oauth\.telegram\.org',
            r't\.me.*login|telegram.*auth\.bot',
        ],
        domains     = ["oauth.telegram.org", "t.me"],
        legal_basis = "152-ФЗ: данные передаются за рубеж; Telegram имеет нестабильный доступ в РФ",
        description = "Telegram Login передаёт данные аутентификации за рубеж. Сервис может быть заблокирован.",
        advice      = "Используйте SMS-верификацию через российских операторов или Госуслуги.",
        fine_citizens = 10000,
        fine_officials = 30000,
        fine_legal = 500000,
        article = "ст. 13.55 КоАП",
    ),
    AuthEntry(
        id          = "foreign_email_id",
        name        = "Иностранный email как идентификатор",
        category    = "auth_foreign",
        auth_type   = "email",
        allowed     = False,
        risk        = "high",
        patterns    = [
            r'@gmail\.com.*login|@yahoo\.com.*login|@hotmail\.com.*login',
            r'login.*@gmail|login.*@yahoo|login.*@hotmail',
            r'outlook\.com.*auth|mail\.ru.*foreign',
        ],
        domains     = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com"],
        legal_basis = "152-ФЗ: использование иностранных почтовых сервисов для идентификации",
        description = "Использование иностранного email (Gmail, Yahoo, Hotmail) как основного идентификатора для входа.",
        advice      = "Используйте российские почтовые сервисы или интегрируйте ЕСИА.",
        fine_citizens = 10000,
        fine_officials = 30000,
        fine_legal = 500000,
        article = "ст. 13.55 КоАП",
    ),
]

# ── Паттерны для проверки аспектов безопасности ──────────────────────────────

SECURITY_CHECKS = {
    "password_https": {
        "patterns": [r'action=["\']https?://'],
        "description": "Форма авторизации использует HTTPS",
        "severity": "high",
        "legal_basis": "Приказ ФСТЭК от 18.02.2013 №21; 152-ФЗ",
    },
    "password_http": {
        "patterns": [r'action=["\']http://[^s]'],
        "description": "Форма авторизации использует HTTP (небезопасно)",
        "severity": "critical",
        "legal_basis": "Приказ ФСТЭК от 18.02.2013 №21",
    },
    "autocomplete_off": {
        "patterns": [r'autocomplete=["\']off["\'].*password', r'type=["\']password["\'].*autocomplete=["\']off'],
        "description": "Отключено автозаполнение пароля (рекомендуется)",
        "severity": "low",
        "legal_basis": "Рекомендация ФСТЭК",
    },
    "captcha_present": {
        "patterns": [r'recaptcha|hcaptcha|captcha|smartcaptcha|yandex\.ru/captcha|cloudflare\.com/cf-captcha'],
        "description": "Обнаружена CAPTCHA защита от ботов",
        "severity": "low",
        "legal_basis": "Рекомендуется для предотвращения брутфорса",
    },
    "rate_limiting": {
        "patterns": [r'rate.?limit|throttl|brute.?force|lockout|max.?attempts'],
        "description": "Обнаружены механизмы ограничения попыток входа",
        "severity": "low",
        "legal_basis": "Приказ ФСТЭК от 18.02.2013 №21",
    },
    "mfa_totp": {
        "patterns": [r'totp|two.?factor|2fa|mfa|authenticator|otp'],
        "description": "Обнаружена многофакторная аутентификация (TOTP)",
        "severity": "low",
        "legal_basis": "Рекомендуется для защиты аккаунтов",
    },
    "session_cookie_secure": {
        "patterns": [r'secure.*cookie|cookie.*secure|httponly|samesite'],
        "description": "Session cookies с защитными флагами",
        "severity": "low",
        "legal_basis": "Приказ ФСТЭК от 18.02.2013 №21",
    },
    "password_field": {
        "patterns": [r'type=["\']password["\']'],
        "description": "Обнаружено поле ввода пароля",
        "severity": "info",
        "legal_basis": "",
    },
    "email_field": {
        "patterns": [r'type=["\']email["\']', r'name=["\'][^"\']*email[^"\']*["\']'],
        "description": "Обнаружено поле email при регистрации",
        "severity": "info",
        "legal_basis": "",
    },
    "phone_field": {
        "patterns": [r'type=["\']tel["\']', r'name=["\'][^"\']*phone[^"\']*["\']', r'placeholder=["\'][^"\']*(\+7|телефон|номер)[^"\']*["\']'],
        "description": "Обнаружено поле телефона",
        "severity": "info",
        "legal_basis": "",
    },
    "consent_checkbox": {
        "patterns": [r'type=["\']checkbox["\'].*соглас', r'соглас.*обработк.*персональн', r'consent.*checkbox'],
        "description": "Обнаружен чекбокс согласия на обработку ПД",
        "severity": "low",
        "legal_basis": "152-ФЗ: требуется явное согласие",
    },
    "age_verification": {
        "patterns": [r'возраст|age.?verify|18\+|подтвер.*возраст|birth.*date|дата.*рожд'],
        "description": "Обнаружена проверка возраста",
        "severity": "low",
        "legal_basis": "52-ФЗ: защита детей от вредной информации",
    },
    "data_minimization": {
        "patterns": [r'обязательн|required.*field|must.?fill'],
        "description": "Обнаружены обязательные поля (проверка минимизации данных)",
        "severity": "info",
        "legal_basis": "152-ФЗ: минимизация собираемых данных",
    },
}

# ── Рекомендательные технологии (ст. 10.2-2 149-ФЗ, ст. 13.56 КоАП) ────────

REC_TECH_SOURCES = [
    AuthEntry(
        id          = "rec_tech_detected",
        name        = "Обнаружены алгоритмы персонализации",
        category    = "rec_tech",
        allowed     = False,
        risk        = "high",
        patterns    = [
            r'рекомендуем\|рекомендации\|вам\s+может\s+понравиться',
            r'recommend\|similar\s+items\|персонализир',
            r'collaborative[\s_-]?filtering',
            r'вам\s+может\s+подойти\|похожие\s+товары\|вы\s+смотрели',
            r'personalized\|for\s+you\|recommended\s+for',
        ],
        domains     = [],
        legal_basis = "ст. 10.2-2 149-ФЗ; ст. 13.56 КоАП РФ; приказ РКН №149 от 06.10.2023",
        description = "На сайте обнаружены алгоритмы персонализации (рекомендательные технологии).",
        advice      = "Требуется уведомление пользователей и публикация документа о правилах применения.",
    ),
    AuthEntry(
        id          = "rec_tech_disclosure",
        name        = "Отсутствует уведомление о рекомендательных технологиях",
        category    = "rec_tech",
        allowed     = False,
        risk        = "high",
        patterns    = [
            r'рекомендательн[а-я]+\s+технолог',
            r'применяются\s+рекомендательные',
            r'recommendation[\s_-]?technolog',
        ],
        domains     = [],
        legal_basis = "ст. 10.2-2 149-ФЗ; ст. 13.56 КоАП РФ",
        description = "На сайте обнаружены рекомендательные технологии, но отсутствует уведомление пользователей об их применении.",
        advice      = "Добавьте на сайт уведомление о применении рекомендательных технологий (ст. 10.2-2 149-ФЗ).",
        absence_check = True,
    ),
    AuthEntry(
        id          = "rec_tech_rules_doc",
        name        = "Отсутствует документ о правилах рекомендательных технологий",
        category    = "rec_tech",
        allowed     = False,
        risk        = "high",
        patterns    = [
            r'правила\s+применения\s+рекомендательн',
            r'rules[\s_-]?of[\s_-]?recommendation',
            r'/rec[\s_-]?rules',
        ],
        domains     = [],
        legal_basis = "ст. 10.2-2 149-ФЗ; ст. 13.56 КоАП РФ; приказ РКН №149 от 06.10.2023",
        description = "На сайте обнаружены рекомендательные технологии, но отсутствует документ с правилами их применения.",
        advice      = "Опубликуйте документ с описанием правил применения рекомендательных технологий на сайте.",
        absence_check = True,
    ),
]

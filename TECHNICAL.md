# Техническое описание проекта

## Общая информация

**Название:** double_sitechecker (Проверка сайтов по 152-ФЗ + Санкционный аудит)

**Стек:** Python 3.10+ / Flask / BeautifulSoup / Playwright / OpenAI SDK / python-docx

**Среда:** Windows (порт 6001 для обхода блокировки Chrome), Gunicorn + Nginx для прода

**Цель:** Автоматизированный аудит сайтов на соответствие требованиям 152-ФЗ «О персональных данных» и проверка на наличие санкционных/заблокированных иностранных ресурсов

---

## Структура проекта

```
double_sitechecker/
├── app.py                    # Flask-ядро: маршруты 152-ФЗ модуля, фоновые задачи, SSE, админ-панель
├── app_sanctions.py          # Flask Blueprint: модуль санкционного аудита + AI-рекомендации
├── app_auth.py               # Flask Blueprint: модуль проверки аутентификации + AI-рекомендации
├── checker.py                # Логика проверки 152-ФЗ (requests + BeautifulSoup + Playwright), ~1900 строк
├── config.py                 # Конфигурация: ключевые слова, селекторы, лимиты, API-ключи из .env
├── deepseek.py               # AI-рекомендации 152-ФЗ (OpenAI SDK, SSE-стриминг, промпты)
├── rec_report.py             # Генерация .docx для 152-ФЗ (python-docx, markdown-рендеринг)
├── report.py                 # Генерация Excel/CSV отчётов (pandas + openpyxl)
├── requirements.txt          # Зависимости
├── .env                      # API-ключи (HUBRIS_API_KEY, BASE_URL, MODEL, ADMIN_PASSWORD)
├── admin_settings.json       # Настройки админ-панели (тема, логотип, лимиты)
├── errors.log                # Лог ошибок
├── example_sites.csv         # Пример входных данных
│
├── sanctions/                # Модуль санкционного аудита
│   ├── __init__.py
│   ├── checker.py            # Логика проверки санкций (requests + BeautifulSoup)
│   ├── sources.py            # База санкционных ресурсов (24 записи, 8 категорий)
│   ├── prompts.py            # AI-промпты для санкционных рекомендаций
│   └── rec_report.py         # Генерация .docx для санкций
│
├── auth/                     # Модуль проверки аутентификации
│   ├── __init__.py
│   ├── checker.py            # Логика проверки авторизации (requests + BeautifulSoup)
│   ├── sources.py            # База сервисов аутентификации (11 записей: 6 российских + 5 иностранных)
│   ├── prompts.py            # AI-промпты для рекомендаций по аутентификации
│   └── rec_report.py         # Генерация .docx для аутентификации
│
├── prompts/                  # Промпты и база знаний для AI (152-ФЗ)
│   ├── system_prompt.txt
│   └── knowledge/
│       ├── fines.txt
│       ├── 152fz_key_articles.txt
│       └── best_practices.txt
│
├── templates/
│   ├── index.html            # Интерфейс 152-ФЗ проверки (Bootstrap 5)
│   ├── sanctions.html        # Интерфейс санкционного аудита (кастомный CSS, тёмная/светлая тема)
│   ├── auth.html             # Интерфейс проверки аутентификации (кастомный CSS, тёмная/светлая тема)
│   ├── admin_login.html      # Вход в админ-панель
│   └── admin_panel.html      # Админ-панель настроек
│
├── static/
│   ├── app.js                # JS для 152-ФЗ: polling, таблица, SSE-стриминг
│   ├── app_v1..v5.js         # Исторические версии JS
│   ├── css/
│   ├── js/
│   ├── fonts/
│   ├── img/
│   └── uploaded_logo.png
│
├── uploads/                  # Загруженные файлы (CSV/XLSX с URL)
├── reports/                  # Сгенерированные Excel/CSV отчёты (TTL 1 час)
├── recommendations/          # Сгенерированные .docx файлы рекомендаций
└── screenshots/              # Скриншоты страниц (Playwright)
```

---

## Два независимых модуля

### Модуль 1: Проверка по 152-ФЗ (`app.py` + `checker.py`)

**Маршруты:**
- `GET /` — главная страница с формой ввода
- `POST /check` — запуск проверки (URL или файл)
- `GET /status/<job_id>` — polling прогресса
- `GET /download/<job_id>/<fmt>` — скачивание Excel/CSV
- `POST /recommend/<job_id>/<domain>` — запуск AI-рекомендаций
- `GET /recommend/stream/<rec_id>` — SSE-стриминг текста от AI
- `GET /recommend/download/<rec_id>` — скачивание .docx
- `GET/POST /admin/*` — админ-панель (настройки темы, логотипа, лимитов)

**Критерии проверки:**
1. Политика ПД — наличие ссылки, 11 разделов ст.14 152-ФЗ, реквизиты оператора, футер
2. Cookie/аналитика — баннер, кнопка отказа, предустановленные галочки, иностранные ресурсы
3. Формы ПД — обнаружение форм (в т.ч. JS-форм без `<form>`), дедупликация
4. Согласие — чекбокс, реквизиты по ст.9, нарушения
5. Playwright — трекеры до согласия, скрытые баннеры, проверка через JS `.checked`

**Особенности:**
- Многостраничная проверка (до 5 страниц с краулингом)
- CMS-детектирование (Bitrix, Tilda, WordPress и др.) с кастомными инструкциями для AI
- Оценка риска по шкалам КоАП (КРИТИЧЕСКИЙ ≥700K / ВЫСОКИЙ ≥220K / СРЕДНИЙ ≥100K / НИЗКИЙ <100K)
- AI-рекомендации: два типа документов (для владельца + ТЗ для разработчика)

**Фоновые задачи:** `threading.Thread` с pooling через `/status/<job_id>` каждые 1.5 сек

---

### Модуль 2: Санкционный аудит (`app_sanctions.py` + `sanctions/`)

**Blueprint:** `sanctions_bp`, URL-префикс `/sanctions`

**Маршруты:**
- `GET /sanctions/` — главная страница модуля
- `POST /sanctions/check` — запуск сканирования
- `GET /sanctions/status/<job_id>` — polling прогресса
- `GET /sanctions/export/<job_id>` — экспорт в Excel
- `GET /sanctions/database` — полная база ресурсов (JSON)
- `POST /sanctions/recommend/<job_id>/<domain>` — запуск AI-рекомендаций
- `GET /sanctions/recommend/stream/<rec_id>` — SSE-стриминг
- `GET /sanctions/recommend/download/<rec_id>` — скачивание .docx

**База ресурсов (`sources.py`):** 24 записи в 8 категориях:
- `meta` — Facebook, Instagram, WhatsApp, Threads, Meta Pixel (экстремизм)
- `banned` — LinkedIn, TikTok, Twitter/X, Pinterest (заблокированы в РФ)
- `payment` — Visa/Mastercard, PayPal, Stripe
- `cdn` — Google Fonts, YouTube, Cloudflare, jsDelivr
- `ads` — Google Ads, Amazon Ads
- `widgets` — Intercom, Zendesk, HubSpot, Drift
- `social` — AddThis/ShareThis, Snapchat Pixel

**Уровни риска:**
- `critical` — экстремизм (Meta)
- `high` — заблокированы в РФ
- `medium` — передача данных за рубеж
- `low` — рекомендуется замена
- `error` — сайт недоступен (требует перепроверки)
- `clean` — ресурсов не обнаружено

---

### Модуль 3: Проверка аутентификации (`app_auth.py` + `auth/`)

**Blueprint:** `auth_bp`, URL-префикс `/auth`

**Маршруты:**
- `GET /auth/` — главная страница модуля
- `POST /auth/check` — запуск проверки
- `GET /auth/status/<job_id>` — polling прогресса
- `GET /auth/export/<job_id>` — экспорт в Excel
- `POST /auth/recommend/<job_id>/<domain>` — AI-рекомендации
- `GET /auth/recommend/stream/<rec_id>` — SSE-стриминг
- `GET /auth/recommend/download/<rec_id>` — скачивание .docx

**Файл `auth/sources.py`:** база сервисов аутентификации
- **Разрешённые (6):** Госуслуги (ЕСИА), ЕБС, Госуслуги.Авторизация, Сбер ID, Яндекс ID, ВКонтакте ID
- **Запрещённые (5):** Google OAuth, Facebook Login, Apple Sign In, GitHub OAuth, Telegram Login

**Критерии проверки (`auth/checker.py`):**
1. Наличие иностранных OAuth (Google, Facebook, Apple, Telegram) — критично/высокий
2. Наличие российских сервисов (Госуслуги, Сбер, Яндекс, ВК) — ОК
3. Безопасность формы входа (HTTPS, CAPTCHA, rate limiting)
4. Согласия при регистрации (152-ФЗ)
5. Многофакторная аутентификация (TOTP)
6. Проверка возраста (52-ФЗ)

**Уровни риска:**
- `critical` — Facebook Login (Meta запрещена)
- `high` — иностранные OAuth (Google, Apple, Telegram)
- `medium` — проблемы безопасности
- `low` — рекомендации (CAPTCHA, MFA, согласия)
- `error` — сайт недоступен
- `clean` — проблем нет

---

## AI-рекомендации (общая архитектура)

**Провайдер:** Hubris API (OpenAI SDK-совместимый)
**Модель:** `qwen/qwen3.5-9b` (настраивается в `.env`)
**Лимит токенов:** 8000

**Файлы AI-модулей:**

| Модуль | 152-ФЗ | Санкции |
|--------|--------|---------|
| Промпты | `deepseek.py` | `sanctions/prompts.py` |
| .docx | `rec_report.py` | `sanctions/rec_report.py` |

**Типы документов:**
- `owner` — рекомендации для владельца (деловой язык)
- `dev` — ТЗ для разработчика (технический язык + код)
- `combined` — оба раздела (используется по умолчанию в санкциях)

**Поток данных:**
1. Клиент открывает SSE-соединение
2. Бэкенд стримит чанки от AI
3. После завершения стрима генерируется .docx
4. Отправляется `[DONE]` → кнопка «Скачать» появляется
5. Клиент скачивает файл через `window.open`

---

## Конфигурация (`.env`)

```
HUBRIS_API_KEY=sk-...         # API-ключ для AI-рекомендаций
HUBRIS_BASE_URL=https://api.hubris.pw/v1
HUBRIS_MODEL=qwen/qwen3.5-9b
```

**Порты:**
- Flask: 6001 (избегает блокировки Chrome на 6000)
- Gunicorn: `gunicorn -w 2 -b 127.0.0.1:5000 app:app`

---

## Админ-панель

**Пароль:** читается из `.env` → `ADMIN_PASSWORD` (по умолчанию `change-me-in-production`)

**Настройки:**
- Тема оформления (amd-dark, amd-light, minimal, green, intel)
- Кастомный CSS
- Видимость hero-секции, AI-блока, переключателя темы
- Максимум URL (по умолчанию 50)
- Максимум страниц на домен (по умолчанию 4)
- Брендинг: логотип, название, подзаголовок

---

## Зависимости (`requirements.txt`)

```
Flask==3.0.2
requests==2.31.0
beautifulsoup4==4.12.3
lxml==5.1.0
pandas==2.2.1
openpyxl==3.1.2
playwright>=1.40.0
openai>=1.0.0
python-dotenv>=1.0.0
python-docx>=1.1.0
```

---

## Ограничения

| Параметр | Значение |
|----------|----------|
| Максимум URL за запрос | 50 |
| Страниц на домен (152-ФЗ) | 4-5 (настраивается) |
| Страниц на домен (санкции) | 1-10 (настраивается) |
| Страниц на домен (аутентификация) | 1-5 (настраивается) |
| Таймаут requests | 10-12 сек |
| Таймаут Playwright | 20 сек |
| Макс. размер файла | 1 МБ |
| TTL отчётов | 1 час |

---

## Текущее состояние (июнь 2025)

### Завершено
- Модуль 152-ФЗ: полная проверка, многостраничность, Playwright, CMS-детекция, AI-рекомендации
- Модуль санкций: проверка по базе 24 ресурсов, краулинг, экспорт Excel
- AI-рекомендации для обоих модулей (SSE-стриминг, .docx генерация)
- Админ-панель с настройками темы и брендинга
- Исправлены: ложные срабатывания Twitter/X, кириллица в WD_ALIGN_PARAGRAPH, CSS.escape в JS

### Известные проблемы
- Playwright не установлен в dev-среде (Warning при импорте, не критично)
- Нет автоматических тестов
- Нет Docker-конфигурации
- `app.py` и `app_sanctions.py` имеют дублирование логики (парсинг URL, хранилище задач)

### Возможные улучшения
- Объединение дублирующегося кода парсинга URL в общем модуле
- Вынос пароля админки в переменные окружения
- Добавление unit-тестов для checker.py и sanctions/checker.py
- Docker-compose для деплоя
- Кэширование результатов AI-рекомендаций
- Rate limiting для API-запросов

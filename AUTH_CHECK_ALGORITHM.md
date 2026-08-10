# Алгоритм проверки и оценки уровня модуля авторизации

## Обзор

Модуль авторизации (`auth/`) проверяет сайты на соответствие требованиям
149-ФЗ «Об информации» и 152-ФЗ «О персональных данных»:

- **Авторизация через российские сервисы** (ст. 13.55 КоАП РФ)
- **Рекомендательные технологии** (ст. 10.2-2 149-ФЗ, ст. 13.56 КоАП РФ; приказ РКН №149 от 06.10.2023)
- **Безопасность форм авторизации** (Приказ ФСТЭК от 18.02.2013 №21)

## Источники данных

- `auth/sources.py` — база сервисов (`RUSSIAN_SERVICES`, `FOREIGN_SERVICES`, `REC_TECH_SOURCES`), `SECURITY_CHECKS`
- `auth/checker.py` — логика сканирования, агрегации, расчёта штрафов и риска
- `app_auth.py` — Flask-эндпоинты и фоновые задачи

---

## 1. Этапы проверки одного домена (`check_auth`)

### Шаг 1 — HTTP-запрос

URL нормализуется (`_normalize_url`): если протокол не указан, добавляется `https://`.
GET-запрос с User-Agent Chrome, Accept-Language `ru-RU`, таймаут 12 сек.

Если HTTP-статус не входит в `{200, 301, 302, 403}` или таймаут → `risk_level = "error"`.

### Шаг 2 — Краулинг страниц

С стартовой страницы собираются ссылки (`<a href>`), приоритизируются по ключевым словам:
`login`, `signin`, `register`, `signup`, `account`, `auth`, `войти`, `регистрация`.

Макс. `max_pages` страниц (по умолчанию 3, макс. 10) того же домена.

### Шаг 3 — Сканирование HTML (`_scan_html`)

Каждая загруженная страница проверяется по 4 категориям:

#### 3.1. Российские сервисы (`auth_allowed`)

| Сервис | Паттерны поиска |
|--------|-----------------|
| Госуслуги (ЕСИА) | `esia.gosuslugi.ru`, `account.gosuslugi.ru` |
| ЕБС (биометрия) | `biometric.ru`, `biometria.ru` |
| Госуслуги.Авторизация | `account.gosuslugi.ru/nca`, `oauth2.0.*gosuslugi` |
| Сбер ID | `id.sber.ru`, `sberid`, `login.sber.ru` |
| Яндекс ID | `passport.yandex.ru`, `login.yandex.ru` |
| ВКонтакте ID | `vk.com.*oauth`, `login.vk.com` |

**Серьёзность:** `low`. Эти записи **не влияют** на финальный риск сайта (см. шаг 6).

#### 3.2. Иностранные сервисы (`auth_foreign`)

| Сервис | Серьёзность | Штраф (юрлицо) | Статья |
|--------|-------------|---------------|--------|
| Google OAuth | `critical` | 500 000 ₽ | ст. 13.55 КоАП |
| Facebook Login (Meta) | `critical` | 500 000 ₽ | ст. 13.55 КоАП |
| Apple Sign In | `critical` | 500 000 ₽ | ст. 13.55 КоАП |
| Microsoft Account / Azure AD | `critical` | 500 000 ₽ | ст. 13.55 КоАП |
| Telegram Login | `critical` | 500 000 ₽ | ст. 13.55 КоАП |
| GitHub OAuth | `high` | 500 000 ₽ | ст. 13.55 КоАП |
| Discord OAuth | `high` | 500 000 ₽ | ст. 13.55 КоАП |
| Иностранный email как ID | `high` | 500 000 ₽ | ст. 13.55 КоАП |

Штрафы: граждане 10 000 ₽, должностные лица 30 000 ₽, юрлица 500 000 ₽ за сервис.

#### 3.3. Рекомендательные технологии (`rec_tech`)

| Запись | Что проверяется |
|--------|-----------------|
| `rec_tech_detected` | Наличие алгоритмов персонализации (рекомендации, «похожее», «вам может понравиться») |
| `rec_tech_disclosure` | Уведомление пользователей о применении рекомендательных технологий (**absence_check**) |
| `rec_tech_rules_doc` | Документ «Правила применения рекомендательных технологий» (**absence_check**) |

**absence_check** — логика «инвертирована»: запись является нарушением, когда она **отсутствует** при наличии рекомендательных алгоритмов. Если `rec_tech_detected` не найден — обе absence-проверки пропускаются.

#### 3.4. Проверки безопасности

| Ключ | Описание | Серьёзность |
|------|----------|-------------|
| `password_http` | Форма авторизации по HTTP | `critical` |
| `password_https` | Форма авторизации по HTTPS | `high` |
| `autocomplete_off` | Отключено автозаполнение пароля | `low` |
| `captcha_present` | Обнаружена CAPTCHA | `low` |
| `rate_limiting` | Ограничение попыток входа | `low` |
| `mfa_totp` | Многофакторная аутентификация | `low` |
| `session_cookie_secure` | Защитные флаги cookies | `low` |
| `consent_checkbox` | Чекбокс согласия на обработку ПД | `low` |
| `age_verification` | Проверка возраста | `low` |
| `password_field` | Поле ввода пароля | `info` |
| `email_field` | Поле email при регистрации | `info` |
| `phone_field` | Поле телефона | `info` |
| `data_minimization` | Обязательные поля | `info` |

#### Сниппеты

Для каждого совпадения regex-паттерна извлекается **сниппет** — HTML-контекст вокруг совпадения
(±150 символов, нормализованный пробелами). Не более 3 сниппетов на проверку.

---

## 2. Агрегация результатов

1. Хиты группируются по `check_key` (ID сервиса или ключ проверки)
2. Страницы из `found_on` объединяются, паттерны объединяются (set union), сниппеты объединяются (до 3)
3. Серьёзность хита не повышается — берётся значение с первой страницы

### Шаг 5 — Absence checks (рекомендательные технологии)

Если `rec_tech_detected == True` (алгоритмы найдены):
- `rec_tech_disclosure` отсутствует → добавляется нарушение (`high`)
- `rec_tech_rules_doc` отсутствует → добавляется нарушение (`high`)

Если `rec_tech_detected == False` — обе проверки пропускаются.

### Шаг 6 — allowed_auth_found

Собирается отдельный список найденных российских сервисов авторизации
(категория `auth_allowed`). Этот список не влияет на расчёт риска.

---

## 3. Определение уровня риска (`risk_level`)

**Ключевое правило:** на финальный риск влияют **только** записи категорий
`auth_foreign` и `rec_tech`. Записи `auth_allowed` (low) и `security` (info/low)
**не повышают** риск.

```
risk = max(severity) по хитам с category ∈ {auth_foreign, rec_tech}
```

| Условие | risk_level |
|---------|------------|
| Сайт недоступен (есть ошибка) | `error` |
| Есть хит `auth_foreign`/`rec_tech` с severity=`critical` | `critical` |
| Есть хит `auth_foreign`/`rec_tech` с severity=`high` | `high` |
| Есть хит `auth_foreign`/`rec_tech` с severity=`medium` | `medium` |
| Есть хит `auth_foreign`/`rec_tech` с severity=`low` | `low` |
| Нет нарушений | `clean` |

---

## 4. Расчёт штрафов

```
total_fine_citizens  = sum(fine_citizens  для hit с allowed=False)
total_fine_officials = sum(fine_officials для hit с allowed=False)
total_fine_legal     = sum(fine_legal     для hit с allowed=False)
```

Штрафы по ст. 13.55 КоАП: 10 000 / 30 000 / 500 000 ₽ за каждый обнаруженный иностранный сервис.

---

## 5. Структура результата (`AuthCheckResult`)

| Поле | Описание |
|------|----------|
| `domain`, `url`, `accessible`, `http_status`, `error` | Сетевая информация |
| `pages_checked` | Список проверенных страниц |
| `risk_level` | Итоговый уровень риска |
| `hits` | Список находок (отсортирован по серьёзности) |
| `critical_count`, `high_count`, `medium_count`, `low_count` | Количество по уровням |
| `has_login_form` | Найдена ли форма входа |
| `has_rec_tech` | Обнаружены ли рекомендательные алгоритмы |
| `has_rec_disclosure` | Есть ли уведомление о рекомендательных технологиях |
| `has_rec_rules_doc` | Есть ли документ с правилами |
| `allowed_auth_found` | Список найденных российских сервисов |
| `total_fine_citizens/officials/legal` | Суммарные штрафы |
| `check_time_sec` | Время проверки |

Каждый хит содержит: `name`, `category`, `severity`, `severity_label`, `allowed`,
`legal_basis`, `description`, `advice`, `found_on`, `matched_patterns`, `snippets`.

---

## 6. Контрольные точки

```
URL → [нормализация] → [HTTP-запрос]
  ├─ Ошибка → risk_level = "error"
  └─ 200/301/302/403 → [краулинг до 3 страниц]
       └─ [_scan_html для каждой страницы]
            ├─ RUSSIAN_SERVICES → hit(category=auth_allowed, severity=low)
            ├─ FOREIGN_SERVICES → hit(category=auth_foreign, severity=critical/high)
            ├─ REC_TECH_SOURCES → hit(category=rec_tech, severity=high)
            └─ SECURITY_CHECKS  → hit(category=security, severity=varies)
       → [агрегация: группировка по check_key, union паттернов/сниппетов]
       → [absence checks: rec_tech + (disclosure|rules_doc)]
       → [allowed_auth_found: список российских сервисов]
       → [risk_level: только auth_foreign + rec_tech]
       → [fine_calculation: только allowed=False]
```

---

## 7. Юридическая основа

- **149-ФЗ** «Об информации» — ст. 10.2-2 (рекомендательные технологии)
- **152-ФЗ** «О персональных данных» — обработка ПД при авторизации
- **ст. 13.55 КоАП** — штрафы за иностранные сервисы авторизации
- **ст. 13.56 КоАП** — штрафы за нарушение требований к рекомендательным технологиям
- **приказ РКН №149 от 06.10.2023** — правила применения рекомендательных технологий
- **52-ФЗ** — защита детей, проверка возраста
- **Приказ ФСТЭК от 18.02.2013 №21** — требования к безопасности форм
- **Указ Президента РФ от 01.11.2024 №788** — ЕСИА

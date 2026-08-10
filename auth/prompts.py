# auth/prompts.py — генерация AI-рекомендаций по результатам проверки авторизации

import logging
from datetime import datetime

from openai import OpenAI, APIError, APIConnectionError

from config import (
    get_ai_config,
    DEEPSEEK_MAX_TOKENS,
    DEEPSEEK_TEMPERATURE,
)

logger = logging.getLogger(__name__)


def _get_client() -> OpenAI:
    cfg = get_ai_config()
    if not cfg["api_key"]:
        raise ValueError(
            "API-ключ не задан. Настройте в админ-панели (AI модель) или в .env: HUBRIS_API_KEY=sk-..."
        )
    return OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])


SYSTEM_PROMPT = (
    "Ты — эксперт по российскому законодательству в сфере защиты персональных данных, "
    "информационной безопасности и требованиям Роскомнадзора и ФСТЭК. "
    "Пишешь на русском языке. Составляешь структурированные документы "
    "с рекомендациями по устранению нарушений в области аутентификации и авторизации."
)


def _build_audit_context(result: dict) -> str:
    """Формирует контекст аудита авторизации для AI-промпта."""
    today = datetime.now().strftime("%d.%m.%Y")

    domain = result.get("domain", "сайт")
    url = result.get("url", "")
    risk_level = result.get("risk_level", "clean")
    hits = result.get("hits") or []
    pages_checked = result.get("pages_checked") or []
    has_login_form = result.get("has_login_form", False)

    risk_labels = {
        "critical": "🔴 КРИТИЧЕСКИЙ",
        "high":     "🟠 ВЫСОКИЙ",
        "medium":   "🟡 СРЕДНИЙ",
        "low":      "🔵 ВНИМАНИЕ",
        "clean":    "✅ Чисто",
        "error":    "⚪ Ошибка доступа",
    }
    risk_label = risk_labels.get(risk_level, risk_level)

    # Группируем по категориям
    foreign_hits = [h for h in hits if not h.get("allowed", True) and h.get("severity") in ("critical", "high")]
    russian_hits = [h for h in hits if h.get("allowed", True) and h.get("severity") == "low"]
    security_hits = [h for h in hits if h.get("check_key", "").startswith(("password", "captcha", "mfa", "session", "consent", "age"))]

    hits_lines = []
    for h in hits:
        sev = h.get("severity", "info")
        sev_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(sev, "⚪")
        pages = ", ".join(h.get("found_on", []))
        allowed = "✅" if h.get("allowed", True) else "❌"
        hits_lines.append(
            f"- {sev_icon} {allowed} **{h['name']}**\n"
            f"  {h.get('description', '')}\n"
            f"  Правовое основание: {h.get('legal_basis', '—')}\n"
            f"  Рекомендация: {h.get('advice', '—')}\n"
            f"  Найдено на: {pages}"
        )

    foreign_section = ""
    if foreign_hits:
        foreign_section = f"""
### ИНОСТРАННЫЕ OAuth-СЕРВИСЫ (требуют замены):
{chr(10).join(f"- ❌ {h['name']} — {h.get('legal_basis', '')}" for h in foreign_hits)}
"""

    ok_lines = []
    if not foreign_hits:
        ok_lines.append("Иностранные OAuth-сервисы не обнаружены")
    if russian_hits:
        ok_lines.append(f"Обнаружены российские сервисы: {', '.join(h['name'] for h in russian_hits)}")
    if has_login_form:
        ok_lines.append("Найдена форма авторизации на сайте")

    nl = "\n"
    return f"""Сегодняшняя дата: **{today}**. Используй именно эту дату.

Проведён автоматический аудит регистрации/авторизации на сайте **{domain}** ({url}).
Страниц проверено: {len(pages_checked)}.
Форма авторизации: {'обнаружена' if has_login_form else 'не обнаружена'}.

## РЕЗУЛЬТАТЫ АУДИТА

**Оценка риска:** {risk_label}
**Найдено сервисов/проблем:** {len(hits)}

### НАРУШЕНИЯ И НАХОДКИ:
{nl.join(hits_lines) if hits_lines else "- Нарушений не выявлено"}
{foreign_section}
### ЧТО УЖЕ ВЫПОЛНЕНО ПРАВИЛЬНО:
{nl.join(f"- ✅ {s}" for s in ok_lines) if ok_lines else "- (нет подтверждённых соответствий)"}

---
⚠️ КРИТИЧЕСКИ ВАЖНО: Составляй документ СТРОГО на основе данных выше.
- ЗАПРЕЩЕНО упоминать нарушения которых нет в разделе НАРУШЕНИЯ.
- Запрещено придумывать сервисы которых нет в данных аудита."""


def _build_prompt_owner(result: dict) -> str:
    domain = result.get("domain", "сайт")
    ctx = _build_audit_context(result)

    return f"""Ты — эксперт по российскому законодательству в сфере аутентификации и защиты данных.

{ctx}

---
ПРАВИЛО: Работай ТОЛЬКО с данными выше. Запрещено придумывать нарушения которых нет в данных.
---

Составь подробный документ **«Рекомендации для владельца сайта {domain}»**.
Язык — деловой, понятный руководителю.

Структура:

# РЕКОМЕНДАЦИИ ПО АУТЕНТИФИКАЦИИ: {domain.upper()}
**Дата аудита:** [сегодняшняя дата]
**Статус:** [Требуется исправление / Соответствует требованиям]

## КРАТКОЕ РЕЗЮМЕ
3–5 предложений. Объясни владельцу: что проверялось, что нашли, какие риски.

## НАРУШЕНИЯ И РЕКОМЕНДАЦИИ
Для каждого нарушения — отдельный подраздел.

### [Название]
**Что это значит для бизнеса:**
Объясни простым языком.

**Последствия:**
- Правовое основание
- Риск проверки

**Что сделать:**
Пошаговая инструкция.

**Приоритет:** СРОЧНО / ВАЖНО / РЕКОМЕНДУЕТСЯ
**Срок:** конкретные сроки

## ЧТО УЖЕ ВЫПОЛНЕНО ПРАВИЛЬНО
Перечисли пункты ✅.

## ПЛАН ДЕЙСТВИЙ
Нумерованный чек-лист с исполнителями и сроками."""


def _build_prompt_dev(result: dict) -> str:
    domain = result.get("domain", "сайт")
    ctx = _build_audit_context(result)

    return f"""Ты — эксперт по веб-разработке и безопасности аутентификации.

{ctx}

---
ПРАВИЛО: Работай ТОЛЬКО с данными выше.
---

Составь **«Техническое задание разработчику»** для сайта {domain}.

Структура:

# ТЗ: АУТЕНТИФИКАЦИЯ — {domain.upper()}
**Дата:** [сегодняшняя дата]

## ЗАДАЧИ

### Задача [N]: [Название]
**Приоритет:** КРИТИЧНО / ВЫСОКИЙ / СРЕДНИЙ
**Трудоёмкость:** [часы]

**Описание:**
Что нарушено технически.

**Реализация:**
```html
<!-- Пример кода -->
```

**Альтернатива:**
Чем заменить (российские сервисы).

**Критерий приёмки:**
Как проверить выполнение."""


def _stream(result: dict, prompt: str):
    try:
        client = _get_client()
    except ValueError as e:
        yield f"data: [ERROR] {e}\n\n"
        return

    try:
        logger.info("Auth AI stream start for %s", result.get("domain"))
        stream = client.chat.completions.create(
            model=get_ai_config()["model"],
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=DEEPSEEK_MAX_TOKENS,
            temperature=DEEPSEEK_TEMPERATURE,
            stream=True,
        )
        full_len = 0
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                text = delta.content
                full_len += len(text)
                yield f"data: {text.replace(chr(10), chr(92) + 'n')}\n\n"

        logger.info("Auth AI stream done for %s (%d chars)", result.get("domain"), full_len)

    except APIConnectionError as e:
        logger.error("Auth AI connection error: %s", e)
        yield f"data: [ERROR] Ошибка соединения с AI API: {e}\n\n"
    except APIError as e:
        logger.error("Auth AI API error: %s", e)
        yield f"data: [ERROR] Ошибка AI API: {e}\n\n"
    except Exception as e:
        logger.error("Auth AI unexpected error: %s", e)
        yield f"data: [ERROR] Неожиданная ошибка: {e}\n\n"


def stream_auth_recommendations(result: dict, doc_type: str = "combined"):
    if doc_type == "owner":
        prompt = _build_prompt_owner(result)
    elif doc_type == "dev":
        prompt = _build_prompt_dev(result)
    else:
        prompt = _build_prompt_owner(result)
    yield from _stream(result, prompt)


def get_auth_recommendations_sync(result: dict, doc_type: str = "combined") -> str:
    if doc_type == "owner":
        prompt = _build_prompt_owner(result)
    elif doc_type == "dev":
        prompt = _build_prompt_dev(result)
    else:
        prompt = _build_prompt_owner(result)

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=get_ai_config()["model"],
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
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
        logger.error("Auth AI sync error: %s", e)
        raise

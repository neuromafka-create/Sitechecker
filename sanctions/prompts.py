# sanctions/prompts.py — генерация AI-рекомендаций по результатам санкционного аудита
#
# doc_type: "owner"    — рекомендации для владельца сайта (деловой язык)
#           "dev"      — ТЗ для разработчика (технический язык + код)
#           "combined" — оба раздела в одном документе

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
    "Ты — эксперт по российскому законодательству в сфере санкций, "
    "информационной безопасности и требований Роскомнадзора. "
    "Пишешь на русском языке. Составляешь структурированные документы "
    "с рекомендациями по устранению нарушений."
)


def _build_audit_context(result: dict) -> str:
    """Формирует контекст аудита санкционных ресурсов для AI-промпта."""
    today = datetime.now().strftime("%d.%m.%Y")

    domain = result.get("domain", "сайт")
    url = result.get("url", "")
    risk_level = result.get("risk_level", "clean")
    hits = result.get("hits") or []
    pages_checked = result.get("pages_checked") or []

    risk_labels = {
        "critical": "🔴 КРИТИЧЕСКИЙ",
        "high":     "🟠 ВЫСОКИЙ",
        "medium":   "🟡 СРЕДНИЙ",
        "low":      "🔵 ВНИМАНИЕ",
        "clean":    "✅ Чисто",
    }
    risk_label = risk_labels.get(risk_level, risk_level)

    # Группируем по категориям
    by_category: dict[str, list] = {}
    for h in hits:
        cat = h.get("category", "other")
        by_category.setdefault(cat, []).append(h)

    category_names = {
        "meta":      "Meta (экстремизм)",
        "banned":    "Заблокированные в РФ",
        "payment":   "Платёжные системы",
        "cdn":       "Иностранные CDN / инфраструктура",
        "ads":       "Иностранные рекламные сети",
        "widgets":   "Иностранные виджеты и чаты",
        "social":    "Иностранные соцсети",
        "analytics": "Иностранная аналитика",
    }

    hits_lines = []
    for h in hits:
        sev_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(h.get("severity", ""), "⚪")
        pages = ", ".join(h.get("found_on", []))
        hits_lines.append(
            f"- {sev_icon} **{h['name']}** ({category_names.get(h.get('category', ''), h.get('category', ''))})\n"
            f"  Правовое основание: {h.get('legal_basis', '—')}\n"
            f"  Описание: {h.get('description', '—')}\n"
            f"  Рекомендация: {h.get('advice', '—')}\n"
            f"  Найдено на: {pages}"
        )

    ok_lines = []
    if not hits:
        ok_lines.append("Санкционных ресурсов не обнаружено — сайт чист")

    nl = "\n"
    return f"""Сегодняшняя дата: **{today}**. Используй именно эту дату.

Проведён автоматический санкционный аудит сайта **{domain}** ({url}).
Страниц проверено: {len(pages_checked)}.

## РЕЗУЛЬТАТЫ АУДИТА

**Оценка риска:** {risk_label}
**Найдено ресурсов:** {len(hits)}

### НАРУШЕНИЯ (требуют исправления):
{nl.join(hits_lines) if hits_lines else "- Нарушений не выявлено"}

### ЧТО УЖЕ ВЫПОЛНЕНО ПРАВИЛЬНО:
{nl.join(f"- ✅ {s}" for s in ok_lines) if ok_lines else "- (нет подтверждённых соответствий)"}

---
⚠️ КРИТИЧЕСКИ ВАЖНО: Составляй документ СТРОГО на основе данных выше.
- ЗАПРЕЩЕНО упоминать нарушения которых нет в разделе НАРУШЕНИЯ.
- ЗАПРЕЩЕНО придумывать ресурсы которых нет в данных аудита.
- Раздел «ЧТО УЖЕ ВЫПОЛНЕНО ПРАВИЛЬНО» — это факты. Не превращай их в нарушения."""


def _build_prompt_owner(result: dict) -> str:
    """Рекомендации для владельца — деловой язык, без кода."""
    domain = result.get("domain", "сайт")
    ctx = _build_audit_context(result)

    return f"""Ты — эксперт по российскому законодательству в сфере санкций и информационной безопасности.

{ctx}

---
ПРАВИЛО: Работай ТОЛЬКО с данными выше. Каждый пункт — строго из строк нарушений.
Запрещено придумывать нарушения которых нет в данных аудита.
---

Составь подробный документ **«Рекомендации для владельца сайта {domain}»**.
Язык — деловой, понятный руководителю. Без технического кода.
Владелец должен понять: что нарушено, почему это проблема, что заказать у разработчика или юриста, какой правовой риск.

Структура:

# РЕКОМЕНДАЦИИ ДЛЯ ВЛАДЕЛЬЦА САЙТА {domain.upper()}
**Дата аудита:** [сегодняшняя дата]
**Статус:** [Требуется исправление / Соответствует требованиям]

## КРАТКОЕ РЕЗЮМЕ
3–5 предложений. Объясни владельцу: что проверялось, что нашли, какой суммарный правовой риск. Упомяни конкретный сайт и конкретные находки.

## НАРУШЕНИЯ И РЕКОМЕНДАЦИИ
Для каждого найденного ресурса — отдельный развёрнутый подраздел. Минимум 5–7 предложений на каждое нарушение.

### [Название ресурса]
**Что это значит для вашего бизнеса:**
Объясни простым языком что именно обнаружено и почему это проблема для владельца сайта (2–3 предложения).

**Какие последствия:**
- Правовое основание (решение суда, постановление, рекомендация РКН)
- Риск проверки Роскомнадзором
- Репутационные и технологические риски (блокировка, замедление, штрафы)

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
Сводная таблица: Нарушение | Правовое основание | Исполнитель | Срок | Статус"""


def _build_prompt_dev(result: dict) -> str:
    """ТЗ для разработчика — технический язык, с примерами кода."""
    domain = result.get("domain", "сайт")
    ctx = _build_audit_context(result)

    return f"""Ты — эксперт по веб-разработке и требованиям российского законодательства.

{ctx}

---
ПРАВИЛО: Работай ТОЛЬКО с данными выше. Задачи — только по нарушениям.
Запрещено придумывать задачи которых нет в данных.
---

Составь документ **«Техническое задание разработчику»** для устранения нарушений на сайте {domain}.

Структура:

# ТЕХНИЧЕСКОЕ ЗАДАНИЕ: {domain.upper()}
**Дата:** [сегодняшняя дата]
**Исполнитель:** разработчик сайта

## ЗАДАЧИ

Для каждого найденного ресурса блок:

### Задача [N]: Удалить/заменить [Название ресурса]
**Приоритет:** КРИТИЧНО / ВЫСОКИЙ / СРЕДНИЙ
**Трудоёмкость:** [оценка в часах]

**Описание:**
Что именно обнаружено технически и почему критично.

**Что изменить:**
Конкретные файлы, блоки, компоненты, настройки.

**Реализация:**
```html
<!-- Рабочий пример кода -->
```

**Альтернатива:**
Чем заменить удалённый ресурс (российские аналоги, локальные решения).

**Критерий приёмки:**
Как проверить что задача выполнена (конкретный инструмент / шаг).

---

Пиши конкретно. Все примеры кода — рабочие."""


def _build_prompt_combined(result: dict) -> str:
    """Оба раздела в одном промпте (обратная совместимость)."""
    ctx = _build_audit_context(result)

    return f"""Ты — эксперт по российскому законодательству в сфере санкций.

{ctx}

---

Составь подробный документ, строго следуя структуре ниже.

## РАЗДЕЛ 1. РЕКОМЕНДАЦИИ ДЛЯ ВЛАДЕЛЬЦА САЙТА
Деловой язык, без кода. Для каждого нарушения: что нарушено, что сделать, приоритет (СРОЧНО / ВАЖНО / РЕКОМЕНДУЕТСЯ). Сортируй по убыванию приоритета.

## РАЗДЕЛ 2. ТЕХНИЧЕСКОЕ ЗАДАНИЕ ДЛЯ РАЗРАБОТЧИКА
Технический язык. Для каждого нарушения: задача, что изменить, пример кода, альтернатива, критерий приёмки."""


def _stream(result: dict, prompt: str):
    """Внутренний SSE-генератор для любого промпта."""
    try:
        client = _get_client()
    except ValueError as e:
        yield f"data: [ERROR] {e}\n\n"
        return

    try:
        logger.info("Sanctions AI stream start for %s", result.get("domain"))
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

        logger.info("Sanctions AI stream done for %s (%d chars)", result.get("domain"), full_len)
        yield "data: [DONE]\n\n"

    except APIConnectionError as e:
        logger.error("Sanctions AI connection error: %s", e)
        yield f"data: [ERROR] Ошибка соединения с AI API: {e}\n\n"
    except APIError as e:
        logger.error("Sanctions AI API error: %s", e)
        yield f"data: [ERROR] Ошибка AI API: {e}\n\n"
    except Exception as e:
        logger.error("Sanctions AI unexpected error: %s", e)
        yield f"data: [ERROR] Неожиданная ошибка: {e}\n\n"


def stream_sanctions_recommendations(result: dict, doc_type: str = "combined"):
    """
    SSE-генератор рекомендаций по санкционному аудиту.
    doc_type: "owner" | "dev" | "combined"
    """
    if doc_type == "owner":
        prompt = _build_prompt_owner(result)
    elif doc_type == "dev":
        prompt = _build_prompt_dev(result)
    else:
        prompt = _build_prompt_combined(result)
    yield from _stream(result, prompt)


def get_sanctions_recommendations_sync(result: dict, doc_type: str = "combined") -> str:
    """
    Синхронный вариант для генерации .docx.
    doc_type: "owner" | "dev" | "combined"
    """
    if doc_type == "owner":
        prompt = _build_prompt_owner(result)
    elif doc_type == "dev":
        prompt = _build_prompt_dev(result)
    else:
        prompt = _build_prompt_combined(result)

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
        logger.error("Sanctions AI sync error: %s", e)
        raise

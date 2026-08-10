"""history.py — SQLite-хранилище истории проверок."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.db")
_lock = threading.Lock()

# Нормализация risk-строк из всех модулей → ключи сводки
_RISK_ALIASES = {
    "critical": "critical",
    "критический": "critical",
    "критично": "critical",
    "high": "high",
    "высокий": "high",
    "medium": "medium",
    "средний": "medium",
    "low": "low",
    "низкий": "low",
    "clean": "clean",
    "чисто": "clean",
    "error": "error",
    "ошибка": "error",
}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_history_db():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scans (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                module          TEXT    NOT NULL,
                job_id          TEXT    NOT NULL,
                created_at      TEXT    NOT NULL,
                domains         TEXT    NOT NULL,
                total_sites     INTEGER DEFAULT 0,
                risk_summary    TEXT,
                violations_count INTEGER DEFAULT 0,
                results_json    TEXT    NOT NULL,
                report_xlsx     TEXT,
                report_csv      TEXT,
                docs_json       TEXT    DEFAULT '[]'
            )
        """)
        # Миграция: docs_json (рекомендации / ТЗ)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(scans)").fetchall()}
        if "docs_json" not in cols:
            conn.execute("ALTER TABLE scans ADD COLUMN docs_json TEXT DEFAULT '[]'")
            logger.info("History migration: added docs_json column")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_module ON scans(module)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON scans(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_job ON scans(job_id)")
        # Разовый пересчёт risk_summary (раньше эмодзи-риски уходили в clean)
        _recompute_risk_summaries(conn)
    logger.info("History DB initialized at %s", DB_PATH)


def _recompute_risk_summaries(conn: sqlite3.Connection):
    """Исправляет risk_summary по results_json для старых записей."""
    try:
        rows = conn.execute("SELECT id, results_json FROM scans").fetchall()
        updated = 0
        for row in rows:
            try:
                results = json.loads(row["results_json"] or "[]")
            except json.JSONDecodeError:
                continue
            summary = _aggregate_risks(results)
            conn.execute(
                "UPDATE scans SET risk_summary = ? WHERE id = ?",
                (json.dumps(summary, ensure_ascii=False), row["id"]),
            )
            updated += 1
        if updated:
            logger.info("History: recomputed risk_summary for %d scans", updated)
    except Exception as e:
        logger.warning("History risk recompute skipped: %s", e)


def _normalize_risk(level) -> str:
    """Приводит risk / risk_level к ключу critical|high|medium|low|clean|error."""
    if not level:
        return "clean"
    if not isinstance(level, str):
        return "clean"
    # Убираем эмодзи и лишние символы, оставляем буквы/пробелы
    text = level.lower().strip()
    text = re.sub(r"[^a-zа-яё\s\-]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    # Точное совпадение слова
    for word in text.split():
        if word in _RISK_ALIASES:
            return _RISK_ALIASES[word]
    # Подстрока (на случай «риск высокий»)
    for alias, key in _RISK_ALIASES.items():
        if alias in text:
            return key
    return "clean"


def save_scan(module: str, job_id: str, results: list[dict],
              report_xlsx: str = None, report_csv: str = None) -> int:
    """Сохраняет результаты проверки в БД. Возвращает id записи."""
    domains = [r.get("domain", r.get("url", "")) for r in results]
    risk_summary = _aggregate_risks(results)
    violations_count = _count_violations(module, results)

    with _lock:
        with _get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO scans
                   (module, job_id, created_at, domains, total_sites,
                    risk_summary, violations_count, results_json,
                    report_xlsx, report_csv, docs_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    module,
                    job_id,
                    datetime.now().isoformat(),
                    json.dumps(domains, ensure_ascii=False),
                    len(results),
                    json.dumps(risk_summary, ensure_ascii=False),
                    violations_count,
                    json.dumps(results, ensure_ascii=False),
                    report_xlsx,
                    report_csv,
                    "[]",
                ),
            )
            scan_id = cur.lastrowid
    logger.info(
        "History saved: id=%s module=%s job=%s sites=%d risks=%s",
        scan_id, module, job_id, len(results), risk_summary,
    )
    return scan_id


def _aggregate_risks(results: list[dict]) -> dict:
    risks = {"critical": 0, "high": 0, "medium": 0, "low": 0, "clean": 0, "error": 0}
    for r in results:
        if r.get("error") and not r.get("accessible", True) and not r.get("risk") and not r.get("risk_level"):
            risks["error"] += 1
            continue
        level = r.get("risk_level", r.get("risk", "clean"))
        key = _normalize_risk(level)
        risks[key] = risks.get(key, 0) + 1
    return risks


def _count_violations(module: str, results: list[dict]) -> int:
    count = 0
    for r in results:
        if module == "main":
            v = r.get("violations") or []
            count += len(v) if isinstance(v, list) else 0
        elif module == "sanctions":
            count += int(r.get("total_count") or 0)
        elif module == "auth":
            count += sum(
                1 for h in (r.get("hits") or [])
                if h.get("category") in ("auth_foreign", "rec_tech")
            )
    return count


def _row_to_list_item(row: sqlite3.Row | dict) -> dict:
    d = dict(row)
    # Парсим JSON-поля для API
    for key in ("domains", "risk_summary"):
        val = d.get(key)
        if isinstance(val, str):
            try:
                d[key] = json.loads(val)
            except json.JSONDecodeError:
                d[key] = [] if key == "domains" else {}
    docs = d.get("docs_json") or "[]"
    if isinstance(docs, str):
        try:
            d["docs"] = json.loads(docs)
        except json.JSONDecodeError:
            d["docs"] = []
    else:
        d["docs"] = docs or []
    d.pop("docs_json", None)
    # Не отдаём полный results_json в списке
    d.pop("results_json", None)
    # Флаги наличия файлов (для UI)
    d["has_xlsx"] = bool(d.get("report_xlsx") and os.path.exists(d["report_xlsx"]))
    d["has_csv"] = bool(d.get("report_csv") and os.path.exists(d["report_csv"]))
    # Отчёты всегда можно пересобрать из results — помечаем regenerable
    d["can_download_report"] = True
    d["docs_count"] = len(d.get("docs") or [])
    return d


def get_history(module: str, limit: int = 50) -> list[dict]:
    """Список проверок модуля (без results_json)."""
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT id, module, job_id, created_at, domains, total_sites,
                      risk_summary, violations_count, report_xlsx, report_csv,
                      docs_json
               FROM scans
               WHERE module = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (module, limit),
        ).fetchall()
    return [_row_to_list_item(r) for r in rows]


def get_scan_detail(scan_id: int) -> dict | None:
    """Полные данные одной проверки + results."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scans WHERE id = ?", (scan_id,)
        ).fetchone()
    if not row:
        return None
    d = _row_to_list_item(row)
    raw = dict(row).get("results_json", "[]")
    try:
        d["results"] = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except json.JSONDecodeError:
        d["results"] = []
    return d


def get_scan_by_job_id(job_id: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scans WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    if not row:
        return None
    return get_scan_detail(row["id"])


def delete_scan(scan_id: int) -> bool:
    """Удаляет запись истории и связанные файлы отчётов/docx (если больше нигде не используются)."""
    with _lock:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM scans WHERE id = ?", (scan_id,)
            ).fetchone()
            if not row:
                return False
            d = dict(row)
            conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))

    # Файлы отчётов
    for key in ("report_xlsx", "report_csv"):
        path = d.get(key)
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError as e:
                logger.warning("Could not remove %s: %s", path, e)

    # Docx рекомендации
    try:
        docs = json.loads(d.get("docs_json") or "[]")
    except json.JSONDecodeError:
        docs = []
    for doc in docs:
        path = doc.get("path")
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError as e:
                logger.warning("Could not remove doc %s: %s", path, e)

    logger.info("History deleted: id=%s module=%s job=%s", scan_id, d.get("module"), d.get("job_id"))
    return True


def attach_doc(job_id: str, domain: str, doc_type: str, path: str,
               label: str | None = None) -> bool:
    """
    Привязывает сгенерированный .docx (рекомендации / ТЗ) к записи истории по job_id.
    """
    if not job_id or not path:
        return False
    entry = {
        "domain": domain or "",
        "doc_type": doc_type or "combined",
        "path": path,
        "label": label or _doc_label(doc_type),
        "created_at": datetime.now().isoformat(),
        "filename": os.path.basename(path),
    }
    with _lock:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT id, docs_json FROM scans WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            if not row:
                logger.warning("attach_doc: no scan for job_id=%s", job_id)
                return False
            try:
                docs = json.loads(row["docs_json"] or "[]")
            except json.JSONDecodeError:
                docs = []
            # Заменяем документ того же domain+doc_type, если уже есть
            docs = [
                x for x in docs
                if not (x.get("domain") == entry["domain"]
                        and x.get("doc_type") == entry["doc_type"])
            ]
            docs.append(entry)
            conn.execute(
                "UPDATE scans SET docs_json = ? WHERE id = ?",
                (json.dumps(docs, ensure_ascii=False), row["id"]),
            )
    logger.info(
        "History doc attached: job=%s domain=%s type=%s path=%s",
        job_id, domain, doc_type, path,
    )
    return True


def _doc_label(doc_type: str) -> str:
    return {
        "owner": "Рекомендации владельцу",
        "dev": "ТЗ разработчику",
        "combined": "Рекомендации",
    }.get(doc_type or "combined", "Документ")


def get_doc(scan_id: int, doc_index: int) -> dict | None:
    """Возвращает метаданные документа по индексу в docs."""
    detail = get_scan_detail(scan_id)
    if not detail:
        return None
    docs = detail.get("docs") or []
    if doc_index < 0 or doc_index >= len(docs):
        return None
    return docs[doc_index]


def list_job_report_paths() -> set[str]:
    """Пути отчётов, на которые ссылается история (чтобы cleanup их не трогал)."""
    paths: set[str] = set()
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT report_xlsx, report_csv FROM scans"
            ).fetchall()
        for r in rows:
            if r["report_xlsx"]:
                paths.add(os.path.normpath(r["report_xlsx"]))
            if r["report_csv"]:
                paths.add(os.path.normpath(r["report_csv"]))
    except Exception as e:
        logger.warning("list_job_report_paths: %s", e)
    return paths

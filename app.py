# app.py — Flask-приложение: маршруты, фоновые задачи, API
import functools
import io
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime

import pandas as pd
from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, send_file, session, stream_with_context)

from checker import check_site, check_site_multipage

# Независимая проверка Playwright прямо в app.py
# Не полагаемся только на checker.py — на Windows импорт может упасть
# с другим типом исключения чем ImportError.
def _check_playwright_available() -> bool:
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        return True
    except Exception:
        return False

PLAYWRIGHT_AVAILABLE = _check_playwright_available()
from config import (HOST, LOG_FILE, MAX_FILE_SIZE, MAX_URLS, PORT,
                    REC_DIR, REPORT_DIR, REPORT_TTL, RESOURCE_DIR, DATA_DIR,
                    SCREENSHOTS_DIR, UPLOAD_DIR, ADMIN_SETTINGS_FILE,
                    get_ai_config, get_admin_settings_path)
from report import generate_csv, generate_excel
from deepseek import stream_recommendations, get_recommendations_sync
from rec_report import generate_recommendations_docx
from app_sanctions import sanctions_bp
from app_auth import auth_bp
from history import (
    init_history_db, save_scan, get_history, get_scan_detail,
    delete_scan, attach_doc, get_doc, list_job_report_paths,
)

# ── Логирование ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(
    __name__,
    template_folder=os.path.join(RESOURCE_DIR, "templates"),
    static_folder=os.path.join(RESOURCE_DIR, "static"),
)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
app.register_blueprint(sanctions_bp)
app.register_blueprint(auth_bp)
init_history_db()

def _get_admin_theme_params() -> dict:
    """Извлекает параметры темы из admin settings для передачи в шаблоны."""
    s = _admin_settings
    theme_map = {
        "amd-dark":  "dark",
        "amd-light": "light",
        "minimal":   "dark",
        "green":     "dark",
        "intel":     "dark",
    }
    theme_key = s.get("theme", "amd-dark")
    return {
        "data_theme":     theme_map.get(theme_key, "dark"),
        "custom_css":     s.get("custom_css", ""),
        "theme_switcher": s.get("theme_switcher", True),
    }

# Глобальное хранилище задач (в памяти)
# Хранилище сгенерированных рекомендаций
recs: dict = {}        # {rec_id: {domain, text, done, error}}
recs_lock = threading.Lock()
# Формат: {job_id: {"status": str, "progress": int, "total": int,
#                    "current": str, "results": list, "created": float}}
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────────────────────────────────────

def _validate_url(url: str) -> str | None:
    """Нормализует URL. Возвращает None если URL невалидный."""
    url = url.strip().rstrip("/")
    if not url:
        return None
    if not re.match(r"https?://", url, re.IGNORECASE):
        url = "https://" + url
    if not re.match(r"https?://[a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}", url):
        return None
    return url


def _normalize_to_root(url: str) -> str:
    """
    Приводит URL к корню домена: https://example.com/page/sub → https://example.com
    Используется для дедупликации — два URL одного домена считаются одним сайтом.
    Краулер сам обойдёт нужные страницы от корня.
    """
    from urllib.parse import urlparse
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _parse_urls_from_text(text: str) -> list[str]:
    """Парсит URL из textarea (по одному на строку).
    Дедупликация по домену: если два URL ведут на один домен,
    оставляется только первый (приводится к корню сайта).
    """
    lines = text.strip().splitlines()
    urls = []
    seen_domains = set()
    for line in lines:
        url = _validate_url(line)
        if not url:
            continue
        root = _normalize_to_root(url)
        if root not in seen_domains:
            seen_domains.add(root)
            urls.append(root)   # всегда проверяем от корня
    return urls


def _parse_urls_from_file(file_storage) -> list[str]:
    """
    Парсит URL из загруженного файла (CSV, TXT, XLSX).
    Берёт первый столбец / первую колонку.
    Дедупликация по домену — см. _normalize_to_root.
    """
    filename = file_storage.filename.lower()
    urls: list[str] = []
    seen_domains: set[str] = set()

    try:
        if filename.endswith(".xlsx"):
            df = pd.read_excel(file_storage, header=None)
            raw = df.iloc[:, 0].dropna().astype(str).tolist()
        else:  # .csv или .txt
            content = file_storage.read().decode("utf-8", errors="ignore")
            # Пробуем как CSV с разделителями ; или ,
            if ";" in content or "," in content:
                df = pd.read_csv(io.StringIO(content), header=None,
                                 sep=r"[;,]", engine="python")
                raw = df.iloc[:, 0].dropna().astype(str).tolist()
            else:
                raw = content.strip().splitlines()
    except Exception as e:
        logger.error("Error parsing file: %s", e)
        return []

    for line in raw:
        url = _validate_url(line.strip())
        if not url:
            continue
        root = _normalize_to_root(url)
        if root not in seen_domains:
            seen_domains.add(root)
            urls.append(root)   # всегда проверяем от корня

    return urls


def _cleanup_old_jobs():
    """Удаляет задачи и файлы старше REPORT_TTL секунд.
    Файлы, на которые ссылается история проверок, не трогаем.
    """
    now = time.time()
    protected = list_job_report_paths()
    with jobs_lock:
        to_delete = [jid for jid, j in jobs.items()
                     if now - j.get("created", 0) > REPORT_TTL]
        for jid in to_delete:
            del jobs[jid]
            for ext in ("xlsx", "csv"):
                path = os.path.join(REPORT_DIR, f"{jid}.{ext}")
                if os.path.exists(path) and os.path.normpath(path) not in protected:
                    try:
                        os.remove(path)
                    except OSError:
                        pass


def _run_checks(job_id: str, urls: list[str], criteria: dict):
    """
    Фоновый поток: проверяет сайты по очереди, обновляет jobs[job_id].
    """
    with jobs_lock:
        jobs[job_id]["status"] = "running"

    results = []
    for i, url in enumerate(urls):
        with jobs_lock:
            jobs[job_id]["current"] = url
            jobs[job_id]["progress"] = i

        logger.info("[%s] Checking %d/%d: %s", job_id, i + 1, len(urls), url)

        try:
            result = check_site_multipage(url, criteria, max_pages=int(_admin_settings.get("max_pages", 4)))
        except Exception as e:
            err_safe = str(e).encode("ascii", "replace").decode("ascii")[:500]
            try:
                logger.error("[%s] Unhandled error for %s: %s", job_id, url, err_safe)
            except Exception:
                pass
            result = {
                "domain": url, "url": url, "accessible": False,
                "http_status": 0, "error": err_safe,
                "policy_found": False, "policy_url": "", "policy_status": 0,
                "policy_text_len": 0, "analytics_systems": [],
                "has_cookie_banner": False, "pd_forms_count": 0,
                "pd_fields": [], "forms_have_warning": False,
                "consent_level": "не проверено", "missing_requisites": [],
                "risk": "🟡 СРЕДНИЙ", "check_time_sec": 0,
                "pages_checked": [url], "pages_count": 0,
            }

        results.append(result)
        with jobs_lock:
            jobs[job_id]["results"] = results

    # Генерация отчётов — всегда помечаем done, даже при сбое
    xlsx_path = csv_path = None
    try:
        try:
            xlsx_bytes = generate_excel(results)
            xlsx_path = os.path.join(REPORT_DIR, f"{job_id}.xlsx")
            with open(xlsx_path, "wb") as f:
                f.write(xlsx_bytes)

            csv_bytes = generate_csv(results)
            csv_path = os.path.join(REPORT_DIR, f"{job_id}.csv")
            with open(csv_path, "wb") as f:
                f.write(csv_bytes)
        except Exception as e:
            logger.error("[%s] Report generation error: %s", job_id,
                         str(e).encode("ascii", "replace").decode("ascii"))

        try:
            save_scan("main", job_id, results, xlsx_path, csv_path)
        except Exception as e:
            logger.error("[%s] History save error: %s", job_id,
                         str(e).encode("ascii", "replace").decode("ascii"))
    finally:
        with jobs_lock:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["progress"] = len(urls)
            jobs[job_id]["current"] = ""
            jobs[job_id]["results"] = results

        try:
            _cleanup_old_jobs()
        except Exception:
            pass
        logger.info("[%s] Finished. Total: %d sites", job_id, len(urls))


# ─────────────────────────────────────────────────────────────────────────────
# Административная панель
# ─────────────────────────────────────────────────────────────────────────────

# Legacy fallback, если пароль ещё не задан через UI (только .env)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me-in-production")
# ADMIN_SETTINGS_FILE — из config (data dir)
LOGO_FILE = os.path.join(DATA_DIR, "uploaded_logo")


def _load_admin_settings() -> dict:
    defaults = {
        "theme":          "amd-dark",
        "custom_css":     "",
        "theme_switcher": True,
        "show_hero":      True,
        "show_ai":        True,
        "max_urls":       50,
        "max_pages":      4,
        "admin_password_hash": "",  # пусто = первый запуск, нужно задать пароль
        "branding": {
            "logo_name":    "152-ФЗ ПРОВЕРКА",
            "logo_sub":     "Audit & Compliance",
            "page_title":   "Проверка сайтов по 152-ФЗ",
            "hero_eyebrow": "Роскомнадзор · АС МПДн · 152-ФЗ",
            "hero_title":   "Автоматизированный аудит сайтов на соответствие требованиям закона",
            "hero_sub":     "Проверяем политику ПД, cookie-баннер, согласия, трекеры и иностранные ресурсы.",
        }
    }
    path = get_admin_settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
                defaults.update(saved)
        except Exception:
            pass
    return defaults


def _save_admin_settings(settings: dict):
    try:
        os.makedirs(os.path.dirname(ADMIN_SETTINGS_FILE) or ".", exist_ok=True)
        with open(ADMIN_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Admin settings save error: %s", e)


_admin_settings: dict = _load_admin_settings()
app.config["_admin_settings"] = _admin_settings


def _admin_password_configured() -> bool:
    """Пароль уже задан в UI (хеш в admin_settings)."""
    return bool((_admin_settings.get("admin_password_hash") or "").strip())


def _set_admin_password(plain: str) -> None:
    from werkzeug.security import generate_password_hash
    _admin_settings["admin_password_hash"] = generate_password_hash(plain)
    _save_admin_settings(_admin_settings)


def _verify_admin_password(plain: str) -> bool:
    """Проверка пароля: хеш из settings, иначе legacy ADMIN_PASSWORD из .env."""
    h = (_admin_settings.get("admin_password_hash") or "").strip()
    if h:
        from werkzeug.security import check_password_hash
        try:
            return check_password_hash(h, plain)
        except Exception:
            return False
    # Совместимость: старые установки с ADMIN_PASSWORD в .env / дефолтом
    return plain == ADMIN_PASSWORD and bool(plain)


def admin_required(f):
    """Декоратор — проверяет авторизацию в сессии."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────────────────────
# Маршруты
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    s = _admin_settings
    branding = s.get("branding", {})
    custom_css = s.get("custom_css", "")

    theme_map = {
        "amd-dark":  ("dark",  "amd"),
        "amd-light": ("light", "amd"),
        "minimal":   ("dark",  "minimal"),
        "green":     ("dark",  "green"),
        "intel":     ("dark",  "intel"),
    }
    theme_key = s.get("theme", "amd-dark")
    data_theme, css_flavor = theme_map.get(theme_key, ("dark", "amd"))

    return render_template(
        "index.html",
        playwright_available = PLAYWRIGHT_AVAILABLE,
        custom_css     = custom_css,
        data_theme     = data_theme,
        css_flavor     = css_flavor,
        show_hero      = s.get("show_hero",      True),
        show_ai        = s.get("show_ai",        True),
        theme_switcher = s.get("theme_switcher", True),
        logo_name      = branding.get("logo_name",    "152-ФЗ ПРОВЕРКА"),
        logo_sub       = branding.get("logo_sub",     "Audit & Compliance"),
        page_title     = branding.get("page_title",   "Проверка сайтов по 152-ФЗ"),
        hero_eyebrow   = branding.get("hero_eyebrow", "Роскомнадзор · АС МПДн · 152-ФЗ"),
        hero_title     = branding.get("hero_title",   "Автоматизированный аудит сайтов на соответствие требованиям закона"),
        hero_sub       = branding.get("hero_sub",     "Проверяем политику ПД, cookie-баннер, согласия, трекеры и иностранные ресурсы."),
        logo_image_url = _get_logo_url(),
        max_urls       = s.get("max_urls",  50),
        max_pages      = s.get("max_pages",  4),
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """
    Первый запуск (нет admin_password_hash) → форма установки пароля.
    Иначе → обычный вход.
    Legacy: если хеша нет, принимается ADMIN_PASSWORD из .env
    (в т.ч. change-me-in-production) — после входа лучше задать новый пароль.
    """
    error = None
    needs_setup = not _admin_password_configured()
    # На десктопе без хеша — только setup (не путать с «неверный пароль»)
    force_setup = needs_setup and (
        os.environ.get("SITECHECKER_DESKTOP", "").strip() in ("1", "true", "yes")
        or getattr(sys, "frozen", False)
    )

    if request.method == "POST":
        action = request.form.get("action", "login")
        if action == "setup" or (force_setup and needs_setup):
            pw = request.form.get("password", "")
            pw2 = request.form.get("password2", "")
            if len(pw) < 6:
                error = "Пароль слишком короткий (минимум 6 символов)."
            elif pw != pw2:
                error = "Пароли не совпадают."
            else:
                try:
                    _set_admin_password(pw)
                    session["admin_logged_in"] = True
                    logger.info("Admin password set (first-run setup)")
                    return redirect("/admin")
                except Exception as e:
                    error = f"Не удалось сохранить пароль: {e}"
            needs_setup = True
        else:
            pw = request.form.get("password", "")
            if _verify_admin_password(pw):
                # Если входили по legacy .env — предложим сохранить как UI-пароль
                if not _admin_password_configured() and pw:
                    try:
                        _set_admin_password(pw)
                    except Exception:
                        pass
                session["admin_logged_in"] = True
                return redirect("/admin")
            error = "Неверный пароль. Попробуйте ещё раз."
            if needs_setup and not force_setup:
                error += (
                    " Если вы ещё не задавали пароль в этом приложении — "
                    "используйте значение ADMIN_PASSWORD из .env "
                    "или дефолт change-me-in-production, либо задайте новый ниже."
                )

    return render_template(
        "admin_login.html",
        error=error,
        needs_setup=needs_setup or force_setup,
        force_setup=force_setup,
    )


@app.route("/admin/change-password", methods=["POST"])
@admin_required
def admin_change_password():
    """Смена пароля из панели (JSON)."""
    data = request.get_json(silent=True) or {}
    current = data.get("current", "")
    new_pw = data.get("new_password", "")
    new2 = data.get("new_password2", "")
    if not _verify_admin_password(current) and _admin_password_configured():
        return jsonify({"error": "Текущий пароль неверен"}), 400
    if len(new_pw) < 6:
        return jsonify({"error": "Новый пароль: минимум 6 символов"}), 400
    if new_pw != new2:
        return jsonify({"error": "Пароли не совпадают"}), 400
    _set_admin_password(new_pw)
    return jsonify({"ok": True})


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect("/admin/login")


@app.route("/admin")
@admin_required
def admin_panel():
    s = _admin_settings
    branding = s.get("branding", {})
    settings_for_js = {
        "theme_switcher": s.get("theme_switcher", True),
        "show_hero":      s.get("show_hero",      True),
        "show_ai":        s.get("show_ai",        True),
        "max_urls":       s.get("max_urls",       50),
        "max_pages":      s.get("max_pages",      4),
        "ai_model":       s.get("ai_model",       {}),
    }
    return render_template(
        "admin_panel.html",
        current_theme = s.get("theme", "amd-dark"),
        custom_css    = s.get("custom_css", ""),
        logo_name     = branding.get("logo_name",    "152-ФЗ ПРОВЕРКА"),
        logo_sub      = branding.get("logo_sub",     "Audit & Compliance"),
        page_title    = branding.get("page_title",   "Проверка сайтов по 152-ФЗ"),
        hero_eyebrow  = branding.get("hero_eyebrow", "Роскомнадзор · АС МПДн · 152-ФЗ"),
        hero_title    = branding.get("hero_title",   "Автоматизированный аудит сайтов"),
        hero_sub      = branding.get("hero_sub",     ""),
        max_urls      = s.get("max_urls", 50),
        max_pages     = s.get("max_pages", 4),
        logo_image_url = _get_logo_url(),
        settings_json = settings_for_js,
    )


@app.route("/admin/save", methods=["POST"])
@admin_required
def admin_save():
    data = request.get_json(silent=True) or {}
    setting = data.get("setting")
    value   = data.get("value")

    if not setting:
        return jsonify({"ok": False, "error": "no setting key"}), 400

    if setting == "branding" and isinstance(value, dict):
        _admin_settings.setdefault("branding", {}).update(value)
    elif setting == "ai_model" and isinstance(value, dict):
        _admin_settings.setdefault("ai_model", {}).update(value)
    elif setting == "all_settings" and isinstance(value, dict):
        for k, v in value.items():
            if k == "admin_password_hash":
                continue  # пароль только через setup / change-password
            # Числовые настройки всегда int
            if k in ("max_urls", "max_pages"):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    pass
            _admin_settings[k] = v
    else:
        if setting == "admin_password_hash":
            return jsonify({"ok": False, "error": "use change-password"}), 400
        # Одиночное сохранение числовых настроек
        if setting in ("max_urls", "max_pages"):
            try:
                value = int(value)
            except (TypeError, ValueError):
                pass
        _admin_settings[setting] = value

    _save_admin_settings(_admin_settings)

    if setting in ("max_urls", "max_pages", "all_settings"):
        new_max = _admin_settings.get("max_urls")
        if new_max:
            try:
                import config as _cfg
                _cfg.MAX_URLS = int(new_max)
            except Exception:
                pass

    logger.info("Admin saved: %s", setting)
    return jsonify({"ok": True})


@app.route("/history")
@admin_required
def history_page():
    return render_template("history.html")


@app.route("/api/history/<module>")
def api_history(module: str):
    if module not in ("main", "sanctions", "auth"):
        return jsonify({"error": "Unknown module"}), 400
    limit = int(request.args.get("limit", 50))
    rows = get_history(module, limit=limit)
    return jsonify(rows)


@app.route("/api/history/detail/<int:scan_id>")
def api_history_detail(scan_id: int):
    detail = get_scan_detail(scan_id)
    if not detail:
        return jsonify({"error": "Not found"}), 404
    return jsonify(detail)


@app.route("/api/history/<int:scan_id>", methods=["DELETE"])
def api_history_delete(scan_id: int):
    """Удаляет одну запись истории и связанные файлы."""
    if not delete_scan(scan_id):
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True, "id": scan_id})


@app.route("/api/history/<int:scan_id>/download/<fmt>")
def api_history_download_report(scan_id: int, fmt: str):
    """Скачать xlsx/csv отчёт из истории (файл или пересборка из results)."""
    if fmt not in ("xlsx", "csv"):
        return jsonify({"error": "Неверный формат"}), 400

    detail = get_scan_detail(scan_id)
    if not detail:
        return jsonify({"error": "Not found"}), 404

    results = detail.get("results") or []
    module = detail.get("module", "main")
    path_key = "report_xlsx" if fmt == "xlsx" else "report_csv"
    stored = detail.get(path_key) or ""

    if stored and os.path.isfile(stored):
        path = stored
    else:
        # Пересобираем из сохранённых результатов
        try:
            if module == "main":
                data = generate_excel(results) if fmt == "xlsx" else generate_csv(results)
            else:
                # sanctions / auth — плоский Excel/CSV
                data = _history_export_table(results, module, fmt)
            path = os.path.join(REPORT_DIR, f"history_{scan_id}.{fmt}")
            mode = "wb"
            with open(path, mode) as f:
                f.write(data if isinstance(data, (bytes, bytearray)) else data.encode("utf-8"))
        except Exception as e:
            logger.error("History report rebuild failed id=%s: %s", scan_id, e)
            return jsonify({"error": f"Не удалось сформировать отчёт: {e}"}), 500

    mime = ("application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet" if fmt == "xlsx" else "text/csv")
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    mod_slug = {"main": "152fz", "sanctions": "sanctions", "auth": "auth"}.get(module, module)
    filename = f"{mod_slug}_history_{scan_id}_{ts}.{fmt}"
    return send_file(path, mimetype=mime, as_attachment=True, download_name=filename)


@app.route("/api/history/<int:scan_id>/doc/<int:doc_index>")
def api_history_download_doc(scan_id: int, doc_index: int):
    """Скачать рекомендацию / ТЗ, привязанные к проверке."""
    doc = get_doc(scan_id, doc_index)
    if not doc:
        return jsonify({"error": "Документ не найден"}), 404
    path = doc.get("path") or ""
    if not path or not os.path.isfile(path):
        return jsonify({"error": "Файл документа отсутствует на диске"}), 404

    safe_domain = re.sub(r"[^\w\-]", "_", doc.get("domain") or "site")
    suffix = {"owner": "owner", "dev": "dev"}.get(doc.get("doc_type"), "rec")
    filename = f"{suffix}_{safe_domain}.docx"
    return send_file(
        path,
        mimetype=("application/vnd.openxmlformats-officedocument"
                  ".wordprocessingml.document"),
        as_attachment=True,
        download_name=filename,
    )


def _history_export_table(results: list, module: str, fmt: str) -> bytes:
    """Плоский экспорт для sanctions/auth из сохранённых results."""
    rows = []
    for r in results:
        if module == "sanctions":
            rows.append({
                "Домен": r.get("domain", ""),
                "URL": r.get("url", ""),
                "Риск": r.get("risk_level", ""),
                "Всего находок": r.get("total_count", 0),
                "Critical": r.get("critical_count", 0),
                "High": r.get("high_count", 0),
                "Medium": r.get("medium_count", 0),
                "Low": r.get("low_count", 0),
                "Ошибка": r.get("error") or "",
            })
        else:  # auth
            hits = r.get("hits") or []
            foreign = [h.get("name", "") for h in hits
                       if h.get("category") in ("auth_foreign", "rec_tech")]
            rows.append({
                "Домен": r.get("domain", ""),
                "URL": r.get("url", ""),
                "Риск": r.get("risk_level", ""),
                "Форма входа": "да" if r.get("has_login_form") else "нет",
                "Находки": ", ".join(foreign) or "—",
                "Ошибка": r.get("error") or "",
            })
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    if fmt == "csv":
        buf.write(df.to_csv(index=False).encode("utf-8-sig"))
    else:
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Отчёт")
    return buf.getvalue()


@app.route("/static/css/custom.css")
def serve_custom_css():
    css = _admin_settings.get("custom_css", "")
    return app.response_class(css, mimetype="text/css")


def _get_logo_url() -> str | None:
    """Возвращает URL логотипа если файл существует, иначе None."""
    for ext in ("png", "jpg", "jpeg", "svg", "webp"):
        path = f"{LOGO_FILE}.{ext}"
        if os.path.exists(path):
            return f"/static/logo.{ext}"
    return None


@app.route("/static/logo.<ext>")
def serve_logo(ext: str):
    """Отдаёт загруженный логотип."""
    if ext not in ("png", "jpg", "jpeg", "svg", "webp"):
        return "", 404
    path = f"{LOGO_FILE}.{ext}"
    if not os.path.exists(path):
        return "", 404
    mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "svg": "image/svg+xml", "webp": "image/webp"}
    return send_file(path, mimetype=mime_map[ext])


@app.route("/admin/upload-logo", methods=["POST"])
@admin_required
def admin_upload_logo():
    """Принимает и сохраняет файл логотипа."""
    file = request.files.get("logo")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "Файл не передан"}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "svg", "webp"):
        return jsonify({"ok": False, "error": "Недопустимый формат"}), 400

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > 2 * 1024 * 1024:
        return jsonify({"ok": False, "error": "Файл больше 2 МБ"}), 400

    # Удаляем старый логотип всех форматов
    for old_ext in ("png", "jpg", "jpeg", "svg", "webp"):
        old_path = f"{LOGO_FILE}.{old_ext}"
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    new_path = f"{LOGO_FILE}.{ext}"
    file.save(new_path)
    logger.info("Logo uploaded: %s", new_path)
    return jsonify({"ok": True, "url": f"/static/logo.{ext}"})


@app.route("/admin/remove-logo", methods=["POST"])
@admin_required
def admin_remove_logo():
    """Удаляет загруженный логотип."""
    removed = False
    for ext in ("png", "jpg", "jpeg", "svg", "webp"):
        path = f"{LOGO_FILE}.{ext}"
        if os.path.exists(path):
            try:
                os.remove(path)
                removed = True
            except OSError as e:
                return jsonify({"ok": False, "error": str(e)}), 500
    logger.info("Logo removed: %s", removed)
    return jsonify({"ok": True})


@app.route("/screenshots/<filename>")
def serve_screenshot(filename: str):
    """Отдаёт скриншот по имени файла."""
    safe_name = os.path.basename(filename)
    path = os.path.join(SCREENSHOTS_DIR, safe_name)
    if not os.path.exists(path):
        return jsonify({"error": "Скриншот не найден"}), 404
    return send_file(path, mimetype="image/png")


@app.route("/check", methods=["POST"])
def start_check():
    """Принимает URL / файл, запускает фоновую проверку."""
    urls: list[str] = []
    if "file" in request.files and request.files["file"].filename:
        f = request.files["file"]
        urls = _parse_urls_from_file(f)
    elif request.form.get("urls"):
        urls = _parse_urls_from_text(request.form["urls"])
    else:
        return jsonify({"error": "Нет данных для проверки"}), 400

    if not urls:
        return jsonify({"error": "Не найдено валидных URL"}), 400

    max_urls = int(_admin_settings.get("max_urls", MAX_URLS))
    if len(urls) > max_urls:
        urls = urls[:max_urls]

    criteria = {
        "check_policy":    "check_policy"    in request.form,
        "check_analytics": "check_analytics" in request.form,
        "check_forms":     "check_forms"     in request.form,
        "check_consent":   "check_consent"   in request.form,
        "use_playwright":  "use_playwright"  in request.form and PLAYWRIGHT_AVAILABLE,
    }
    base = {k: v for k, v in criteria.items() if k != "use_playwright"}
    if not any(base.values()):
        for k in base:
            criteria[k] = True

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status":   "pending",
            "progress": 0,
            "total":    len(urls),
            "current":  "",
            "results":  [],
            "created":  time.time(),
        }

    thread = threading.Thread(
        target=_run_checks,
        args=(job_id, urls, criteria),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "total": len(urls)})


@app.route("/status/<job_id>")
def job_status(job_id: str):
    """Возвращает текущий статус задачи."""
    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        return jsonify({"error": "Задача не найдена"}), 404

    return jsonify({
        "status":   job["status"],
        "progress": job["progress"],
        "total":    job["total"],
        "current":  job["current"],
        "results":  job["results"],
    })


@app.route("/download/<job_id>/<fmt>")
def download_report(job_id: str, fmt: str):
    """Отдаёт готовый файл отчёта (xlsx или csv)."""
    if fmt not in ("xlsx", "csv"):
        return jsonify({"error": "Неверный формат"}), 400

    path = os.path.join(REPORT_DIR, f"{job_id}.{fmt}")
    if not os.path.exists(path):
        return jsonify({"error": "Файл не найден или ещё не готов"}), 404

    mime = ("application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet" if fmt == "xlsx" else "text/csv")
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"pd_check_{ts}.{fmt}"

    return send_file(path, mimetype=mime, as_attachment=True,
                     download_name=filename)


# ─────────────────────────────────────────────────────────────────────────────
# Маршруты: рекомендации через DeepSeek
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/recommend/<job_id>/<path:domain>", methods=["POST"])
def start_recommendation(job_id: str, domain: str):
    """
    Запускает генерацию рекомендаций для конкретного сайта из задачи.
    Параметр doc_type (POST body или query): "owner" | "dev" | "combined"
    Возвращает rec_id для polling/streaming.
    """
    if not get_ai_config()["api_key"]:
        return jsonify({"error":
            "API-ключ не задан. Настройте в админ-панели (AI модель) или в .env"}), 400

    doc_type = (request.get_json(silent=True) or {}).get("doc_type", "combined")
    if doc_type not in ("owner", "dev", "combined"):
        doc_type = "combined"

    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Задача не найдена"}), 404

    results = job.get("results", [])
    site_result = next(
        (r for r in results if r.get("domain") == domain or r.get("url", "").find(domain) >= 0),
        None
    )
    if not site_result:
        return jsonify({"error": f"Домен {domain} не найден в результатах"}), 404

    rec_id = str(uuid.uuid4())
    with recs_lock:
        recs[rec_id] = {
            "domain":   domain,
            "job_id":   job_id,
            "doc_type": doc_type,
            "text":     "",
            "done":     False,
            "error":    "",
            "result":   site_result,
            "created":  time.time(),
        }

    return jsonify({"rec_id": rec_id, "domain": domain, "doc_type": doc_type})


@app.route("/recommend/stream/<rec_id>")
def stream_recommendation(rec_id: str):
    """
    SSE-эндпоинт: стримит текст рекомендаций от DeepSeek.
    Браузер подключается через EventSource.
    """
    with recs_lock:
        rec = recs.get(rec_id)
    if not rec:
        def _err():
            yield "data: [ERROR] Сессия рекомендаций не найдена\n\n"
        return Response(stream_with_context(_err()),
                        mimetype="text/event-stream")

    site_result = rec["result"]
    doc_type    = rec.get("doc_type", "combined")

    def _generate():
        full_text = []
        try:
            for chunk in stream_recommendations(site_result, doc_type):
                if chunk.startswith("data: ") and not chunk.startswith("data: ["):
                    raw = chunk[6:].rstrip("\n").replace("\\n", "\n")
                    full_text.append(raw)
                yield chunk

            complete_text = "".join(full_text)
            with recs_lock:
                recs[rec_id]["text"] = complete_text
                recs[rec_id]["done"] = True

            try:
                docx_bytes = generate_recommendations_docx(site_result, complete_text)
                safe_domain = re.sub(r"[^\w\-]", "_", rec["domain"])
                suffix = {"owner": "owner", "dev": "dev"}.get(doc_type, "rec")
                docx_path = os.path.join(REC_DIR, f"{rec_id}_{safe_domain}_{suffix}.docx")
                with open(docx_path, "wb") as f:
                    f.write(docx_bytes)
                with recs_lock:
                    recs[rec_id]["docx_path"] = docx_path
                # Привязка к истории проверок (по job_id)
                try:
                    attach_doc(
                        rec.get("job_id", ""),
                        rec.get("domain", ""),
                        doc_type,
                        docx_path,
                    )
                except Exception as ae:
                    logger.warning("History attach_doc failed: %s", ae)
                logger.info("Recommendations docx saved: %s", docx_path)
            except Exception as e:
                logger.error("Docx generation error: %s", e)

        except Exception as e:
            logger.error("Stream error rec_id=%s: %s", rec_id, e)
            with recs_lock:
                recs[rec_id]["error"] = str(e)
                recs[rec_id]["done"]  = True
            yield f"data: [ERROR] {e}\n\n"

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/recommend/download/<rec_id>")
def download_recommendation(rec_id: str):
    """Скачать .docx с рекомендациями."""
    with recs_lock:
        rec = recs.get(rec_id)
    if not rec:
        return jsonify({"error": "Не найдено"}), 404

    docx_path = rec.get("docx_path", "")
    if not docx_path or not os.path.exists(docx_path):
        return jsonify({"error": "Документ ещё не готов"}), 404

    safe_domain = re.sub(r"[^\w\-]", "_", rec["domain"])
    ts = datetime.now().strftime("%Y%m%d")
    doc_type = rec.get("doc_type", "combined")
    suffix = {"owner": "owner", "dev": "dev"}.get(doc_type, "recommendations")
    filename = f"{suffix}_{safe_domain}_{ts}.docx"

    return send_file(
        docx_path,
        mimetype=("application/vnd.openxmlformats-officedocument"
                  ".wordprocessingml.document"),
        as_attachment=True,
        download_name=filename,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Запуск
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)

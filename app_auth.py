# app_auth.py
"""
Flask Blueprint для модуля проверки регистрации/авторизации.
Монтируется в основной app.py через:
    from app_auth import auth_bp
    app.register_blueprint(auth_bp)
"""
from __future__ import annotations

import io
import logging
import os
import re
import threading
import time
import uuid

import pandas as pd
from flask import (Blueprint, Response, current_app, jsonify, render_template,
                   request, send_file, stream_with_context)

from auth.checker import check_auth
from auth.sources import CATEGORIES, RUSSIAN_SERVICES, FOREIGN_SERVICES, SEVERITY_LABEL
from history import save_scan

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

_MAX_FILE_SIZE = int(os.environ.get("AUTH_MAX_FILE_SIZE", 1 * 1024 * 1024))
_MAX_URLS = int(os.environ.get("AUTH_MAX_URLS", 50))
_ALLOWED_FILE_EXT = (".csv", ".txt", ".xlsx")

_jobs:      dict[str, dict] = {}
_jobs_lock: threading.Lock  = threading.Lock()
_JOB_TTL = 3600

_recs:      dict[str, dict] = {}
_recs_lock: threading.Lock  = threading.Lock()


def _cleanup_jobs():
    now = time.time()
    with _jobs_lock:
        stale = [jid for jid, j in _jobs.items()
                 if now - j.get("created", 0) > _JOB_TTL]


def _get_theme_params() -> dict:
    s = current_app.config.get("_admin_settings", {})
    theme_map = {"amd-dark": "dark", "amd-light": "light",
                 "minimal": "dark", "green": "dark", "intel": "dark"}
    return {
        "data_theme":     theme_map.get(s.get("theme", "amd-dark"), "dark"),
        "custom_css":     s.get("custom_css", ""),
        "theme_switcher": s.get("theme_switcher", True),
    }


def _cleanup_jobs():
    now = time.time()
    with _jobs_lock:
        stale = [jid for jid, j in _jobs.items()
                 if now - j.get("created", 0) > _JOB_TTL]
        for jid in stale:
            del _jobs[jid]


def _validate_urls(text: str) -> list[str]:
    import re as _re
    seen, urls = set(), []
    for line in text.strip().splitlines():
        line = line.strip().rstrip("/")
        if not line or line.startswith(("#", "//")):
            continue
        if not _re.match(r"https?://", line, _re.IGNORECASE):
            line = "https://" + line
        if not _re.match(r"https?://[a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}", line):
            continue
        from urllib.parse import urlparse
        root = f"{urlparse(line).scheme}://{urlparse(line).netloc}"
        if root not in seen:
            seen.add(root)
            urls.append(root)
    return urls


def _parse_urls_from_file(file_storage) -> list[str]:
    filename = (file_storage.filename or "").lower()
    if not filename.endswith(_ALLOWED_FILE_EXT):
        raise ValueError("Поддерживаются только .csv, .txt, .xlsx")
    file_storage.seek(0, 2)
    size = file_storage.tell()
    file_storage.seek(0)
    if size > _MAX_FILE_SIZE:
        raise ValueError(f"Файл слишком большой (макс. {_MAX_FILE_SIZE // 1024 // 1024} МБ)")
    try:
        if filename.endswith(".xlsx"):
            df = pd.read_excel(file_storage, header=None)
            raw = df.iloc[:, 0].dropna().astype(str).tolist()
        else:
            content = file_storage.read().decode("utf-8", errors="ignore")
            if ";" in content or "," in content:
                df = pd.read_csv(io.StringIO(content), header=None,
                                 sep=r"[;,]", engine="python")
                raw = df.iloc[:, 0].dropna().astype(str).tolist()
            else:
                raw = content.strip().splitlines()
    except Exception as e:
        raise ValueError(f"Ошибка чтения файла: {e}") from e
    return _validate_urls("\n".join(raw))


def _run_scan(job_id: str, urls: list[str], max_pages: int):
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"
    results = []
    for i, url in enumerate(urls):
        with _jobs_lock:
            _jobs[job_id]["current"] = url
            _jobs[job_id]["progress"] = i
        logger.info("[auth job %s] %d/%d: %s", job_id, i + 1, len(urls), url)
        try:
            result = check_auth(url, max_pages=max_pages)
            results.append(result.to_dict())
        except Exception as e:
            logger.error("[auth job %s] Error %s: %s", job_id, url, e)
            results.append({
                "domain": url, "url": url,
                "accessible": False, "error": str(e),
                "risk_level": "error", "total_count": 0,
                "critical_count": 0, "high_count": 0,
                "medium_count": 0, "low_count": 0,
                "hits": [], "pages_checked": [], "check_time_sec": 0,
                "has_login_form": False,
            })
        with _jobs_lock:
            _jobs[job_id]["results"] = list(results)
    with _jobs_lock:
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["progress"] = len(urls)
        _jobs[job_id]["current"] = ""
    try:
        save_scan("auth", job_id, results)
    except Exception as e:
        logger.error("[auth job %s] History save error: %s", job_id, e)
    _cleanup_jobs()
    logger.info("[auth job %s] Done. %d sites.", job_id, len(results))


# ── Маршруты ─────────────────────────────────────────────────────────────

@auth_bp.route("/")
def index():
    stats = {
        "total_entries": len(RUSSIAN_SERVICES) + len(FOREIGN_SERVICES),
        "russian": len(RUSSIAN_SERVICES),
        "foreign": len(FOREIGN_SERVICES),
        "categories": CATEGORIES,
    }
    return render_template("auth.html", db_stats=stats,
                           categories=CATEGORIES, severity_labels=SEVERITY_LABEL,
                           max_urls=_MAX_URLS,
                           **_get_theme_params())


@auth_bp.route("/check", methods=["POST"])
def start_check():
    urls: list[str] = []
    if "file" in request.files and request.files["file"].filename:
        try:
            urls = _parse_urls_from_file(request.files["file"])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    elif request.form.get("urls"):
        urls = _validate_urls(request.form["urls"])
    else:
        return jsonify({"error": "Нет данных для проверки"}), 400
    if not urls:
        return jsonify({"error": "Не найдено валидных URL"}), 400
    max_pages = int(request.form.get("max_pages", 3))
    max_pages = max(1, min(max_pages, 10))
    if len(urls) > _MAX_URLS:
        urls = urls[:_MAX_URLS]
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "pending", "progress": 0, "total": len(urls),
            "current": "", "results": [], "created": time.time(),
        }
    thread = threading.Thread(target=_run_scan, args=(job_id, urls, max_pages), daemon=True)
    thread.start()
    return jsonify({"job_id": job_id, "total": len(urls)})


@auth_bp.route("/status/<job_id>")
def job_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Задача не найдена"}), 404
    return jsonify({
        "status": job["status"], "progress": job["progress"],
        "total": job["total"], "current": job["current"],
        "results": job["results"],
    })


@auth_bp.route("/export/<job_id>")
def export_xlsx(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job or not job.get("results"):
        return jsonify({"error": "Нет данных"}), 404
    results = job["results"]
    rows = []
    sev_order = ["critical", "high", "medium", "low", "info"]
    for r in results:
        fine_legal = r.get("total_fine_legal", 0)
        if not r.get("hits"):
            rows.append({
                "Домен": r["domain"], "Риск": "✅ Чисто",
                "Сервис": "—", "Тип": "—", "Уровень": "—",
                "Разрешён": "—", "Правовое основание": "—",
                "Страниц проверено": len(r.get("pages_checked", [])),
                "Время, сек": r.get("check_time_sec", 0),
                "Штраф юрлица": 0,
            })
        else:
            for h in sorted(r["hits"],
                            key=lambda x: sev_order.index(x["severity"])
                            if x["severity"] in sev_order else 99):
                rows.append({
                    "Домен": r["domain"],
                    "Риск": {
                        "critical": "🔴 КРИТИЧНО", "high": "🟠 ВЫСОКИЙ",
                        "medium": "🟡 СРЕДНИЙ", "low": "🔵 ВНИМАНИЕ",
                        "error": "⚪ ОШИБКА",
                    }.get(r["risk_level"], r["risk_level"]),
                    "Сервис": h["name"],
                    "Тип": CATEGORIES.get(h.get("category", ""), h.get("category", "")),
                    "Уровень": h.get("severity_label", h["severity"]),
                    "Разрешён": "✅ Да" if h.get("allowed", True) else "❌ Нет",
                    "Правовое основание": h.get("legal_basis", "—"),
                    "Найдено на страницах": ", ".join(h.get("found_on", [])),
                    "Страниц проверено": len(r.get("pages_checked", [])),
                    "Время, сек": r.get("check_time_sec", 0),
                    "Штраф юрлица": fine_legal,
                })
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Авторизация")
        ws = writer.sheets["Авторизация"]
        for col in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)
    buf.seek(0)
    from datetime import datetime
    fname = f"auth_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name=fname,
    )


@auth_bp.route("/database")
def view_database():
    """JSON со всеми сервисами аутентификации."""
    all_services = RUSSIAN_SERVICES + FOREIGN_SERVICES
    return jsonify([
        {
            "name":        e.name,
            "auth_type":   e.auth_type,
            "allowed":     e.allowed,
            "domains":     e.domains,
            "legal_basis": e.legal_basis,
            "description": e.description,
            "advice":      e.advice,
        }
        for e in all_services
    ])


# ── AI-рекомендации ─────────────────────────────────────────────────────

@auth_bp.route("/recommend/<job_id>/<path:domain>", methods=["POST"])
def start_recommendation(job_id: str, domain: str):
    from config import get_ai_config
    if not get_ai_config()["api_key"]:
        return jsonify({"error": "API-ключ не задан. Настройте в админ-панели (AI модель)"}), 400
    doc_type = (request.get_json(silent=True) or {}).get("doc_type", "combined")
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Задача не найдена"}), 404
    site_result = next(
        (r for r in job.get("results", [])
         if r.get("domain") == domain or r.get("url", "").find(domain) >= 0),
        None
    )
    if not site_result:
        return jsonify({"error": f"Домен {domain} не найден"}), 404
    rec_id = str(uuid.uuid4())
    with _recs_lock:
        _recs[rec_id] = {
            "domain": domain, "job_id": job_id, "doc_type": doc_type,
            "text": "", "done": False, "error": "",
            "result": site_result, "created": time.time(),
        }
    return jsonify({"rec_id": rec_id, "domain": domain, "doc_type": doc_type})


@auth_bp.route("/recommend/stream/<rec_id>")
def stream_recommendation(rec_id: str):
    with _recs_lock:
        rec = _recs.get(rec_id)
    if not rec:
        def _err():
            yield "data: [ERROR] Сессия не найдена\n\n"
        return Response(stream_with_context(_err()), mimetype="text/event-stream")

    site_result = rec["result"]
    doc_type = rec.get("doc_type", "combined")

    def _generate():
        full_text = []
        try:
            from auth.prompts import stream_auth_recommendations
            for chunk in stream_auth_recommendations(site_result, doc_type):
                if chunk.strip() == "data: [DONE]":
                    continue
                if chunk.startswith("data: ") and not chunk.startswith("data: ["):
                    raw = chunk[6:].rstrip("\n").replace("\\n", "\n")
                    full_text.append(raw)
                yield chunk

            complete_text = "".join(full_text)
            with _recs_lock:
                _recs[rec_id]["text"] = complete_text
                _recs[rec_id]["done"] = True

            try:
                from auth.rec_report import generate_auth_recommendations_docx
                docx_bytes = generate_auth_recommendations_docx(site_result, complete_text)
                safe_domain = re.sub(r"[^\w\-]", "_", rec["domain"])
                from config import REC_DIR
                os.makedirs(REC_DIR, exist_ok=True)
                docx_path = os.path.join(REC_DIR, f"auth_{rec_id}_{safe_domain}.docx")
                with open(docx_path, "wb") as f:
                    f.write(docx_bytes)
                with _recs_lock:
                    _recs[rec_id]["docx_path"] = docx_path
                try:
                    from history import attach_doc
                    attach_doc(rec.get("job_id", ""), rec.get("domain", ""),
                               doc_type, docx_path)
                except Exception as ae:
                    logger.warning("Auth attach_doc failed: %s", ae)
                logger.info("Auth recommendations docx saved: %s", docx_path)
            except Exception as e:
                logger.error("Auth docx generation error: %s", e)

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error("Auth stream error rec_id=%s: %s", rec_id, e)
            with _recs_lock:
                _recs[rec_id]["error"] = str(e)
                _recs[rec_id]["done"] = True
            yield f"data: [ERROR] {e}\n\n"

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@auth_bp.route("/recommend/download/<rec_id>")
def download_recommendation(rec_id: str):
    with _recs_lock:
        rec = _recs.get(rec_id)
    if not rec:
        return jsonify({"error": "Не найдено"}), 404
    docx_path = rec.get("docx_path", "")
    if not docx_path or not os.path.exists(docx_path):
        return jsonify({"error": "Документ ещё не готов"}), 404
    safe_domain = re.sub(r"[^\w\-]", "_", rec["domain"])
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d")
    filename = f"auth_{safe_domain}_{ts}.docx"
    return send_file(
        docx_path,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True, download_name=filename,
    )

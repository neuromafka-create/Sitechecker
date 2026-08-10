# auth/checker.py
"""
Проверка регистрации/авторизации на соответствие требованиям
российского законодательства.

Критерии:
1. Использование российских сервисов аутентификации (Госуслуги, ЕБС, Сбер, Яндекс, ВК)
2. Отсутствие иностранных OAuth (Google, Facebook, Apple, Telegram)
3. Безопасность формы входа (HTTPS, CAPTCHA, rate limiting)
4. Согласия при регистрации (152-ФЗ)
5. Многофакторная аутентификация
6. Проверка возраста (52-ФЗ)
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

from config import should_skip_crawl_url
from auth.sources import (
    CATEGORIES, RUSSIAN_SERVICES, FOREIGN_SERVICES, REC_TECH_SOURCES,
    SECURITY_CHECKS, SEVERITY_LABEL, AuthEntry,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
}
TIMEOUT = 12
MAX_PAGES = 3


@dataclass
class AuthHit:
    """Найденный сервис аутентификации или проблема."""
    entry:       AuthEntry | None  # None для security checks
    check_key:   str               # ключ из SECURITY_CHECKS или имя сервиса
    name:        str
    category:    str               # auth_allowed | auth_foreign | rec_tech | security
    allowed:     bool
    severity:    str               # critical/high/medium/low/info
    found_on:    list[str]
    matched_patterns: list[str]
    description: str
    legal_basis: str
    advice:      str
    snippets:    list[str] = field(default_factory=list)  # HTML-контекст совпадений


@dataclass
class AuthCheckResult:
    """Результат проверки одного домена."""
    domain:       str
    url:          str
    accessible:   bool              = False
    http_status:  int               = 0
    error:        str               = ""
    pages_checked: list[str]        = field(default_factory=list)
    hits:         list[AuthHit]     = field(default_factory=list)
    has_login_form: bool            = False
    check_time_sec: float           = 0.0
    has_rec_tech:  bool             = False
    has_rec_disclosure: bool        = False
    has_rec_rules_doc: bool         = False
    allowed_auth_found: list[str]   = field(default_factory=list)
    total_fine_citizens: int        = 0
    total_fine_officials: int       = 0
    total_fine_legal: int           = 0

    @property
    def critical_count(self) -> int:
        return sum(1 for h in self.hits if h.severity == "critical")

    @property
    def high_count(self) -> int:
        return sum(1 for h in self.hits if h.severity == "high")

    @property
    def medium_count(self) -> int:
        return sum(1 for h in self.hits if h.severity == "medium")

    @property
    def low_count(self) -> int:
        return sum(1 for h in self.hits if h.severity == "low")

    @property
    def total_count(self) -> int:
        return len(self.hits)

    @property
    def risk_level(self) -> str:
        if not self.accessible and self.error:
            return "error"
        violation_severities = [
            h.severity for h in self.hits
            if h.category in ("auth_foreign", "rec_tech")
        ]
        if "critical" in violation_severities:
            return "critical"
        if "high" in violation_severities:
            return "high"
        if "medium" in violation_severities:
            return "medium"
        if "low" in violation_severities:
            return "low"
        return "clean"

    def to_dict(self) -> dict:
        return {
            "domain":         self.domain,
            "url":            self.url,
            "accessible":     self.accessible,
            "http_status":    self.http_status,
            "error":          self.error,
            "pages_checked":  self.pages_checked,
            "risk_level":     self.risk_level,
            "critical_count": self.critical_count,
            "high_count":     self.high_count,
            "medium_count":   self.medium_count,
            "low_count":      self.low_count,
            "total_count":    self.total_count,
            "has_login_form": self.has_login_form,
            "has_rec_tech":   self.has_rec_tech,
            "has_rec_disclosure": self.has_rec_disclosure,
            "has_rec_rules_doc":  self.has_rec_rules_doc,
            "allowed_auth_found": self.allowed_auth_found,
            "check_time_sec": self.check_time_sec,
            "total_fine_citizens": self.total_fine_citizens,
            "total_fine_officials": self.total_fine_officials,
            "total_fine_legal": self.total_fine_legal,
            "hits": [
                {
                    "name":        h.name,
                    "category":    h.category,
                    "severity":    h.severity,
                    "severity_label": SEVERITY_LABEL.get(h.severity, ("", ""))[0],
                    "allowed":     h.allowed,
                    "legal_basis": h.legal_basis,
                    "description": h.description,
                    "advice":      h.advice,
                    "found_on":    h.found_on,
                    "matched_patterns": h.matched_patterns,
                    "snippets":    h.snippets,
                }
                for h in sorted(
                    self.hits,
                    key=lambda h: ["critical","high","medium","low","info"].index(h.severity)
                    if h.severity in ["critical","high","medium","low","info"] else 99
                )
            ],
        }


def _get_category(hit: AuthHit) -> str:
    """Определяет категорию хита."""
    if hit.entry:
        return hit.entry.category or hit.entry.auth_type
    key = hit.check_key or ""
    if "password" in key:
        return "password"
    if "captcha" in key:
        return "captcha"
    if "mfa" in key:
        return "mfa"
    if "session" in key or "cookie" in key:
        return "session"
    if "consent" in key or "age" in key:
        return "consent"
    return "security"


SNIPPET_CONTEXT = 150  # символов по каждую сторону от совпадения


def _extract_snippets(html: str, pattern: str, max_snippets: int = 3) -> list[str]:
    """Извлекает сниппеты HTML вокруг совпадений regex-паттерна."""
    snippets = []
    try:
        for m in re.finditer(pattern, html, re.IGNORECASE | re.DOTALL):
            start = max(0, m.start() - SNIPPET_CONTEXT)
            end = min(len(html), m.end() + SNIPPET_CONTEXT)
            snippet = html[start:end]
            snippet = re.sub(r'\s+', ' ', snippet).strip()
            if snippet and snippet not in snippets:
                snippets.append(snippet)
            if len(snippets) >= max_snippets:
                break
    except re.error:
        pass
    return snippets


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url:
        return ""
    if not re.match(r"https?://", url, re.IGNORECASE):
        url = "https://" + url
    return url


def _extract_domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return url


def _canonical_page_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    path = p.path.rstrip("/") or "/"
    netloc = p.netloc.lower().removeprefix("www.")
    scheme = (p.scheme or "https").lower()
    return urllib.parse.urlunparse((scheme, netloc, path, "", "", ""))


def _crawl_pages(session: requests.Session, start_url: str,
                 start_html: str, max_pages: int = MAX_PAGES) -> list[tuple[str, str]]:
    base_domain = _extract_domain(start_url)
    start_canon = _canonical_page_url(start_url)
    pages: list[tuple[str, str]] = [(start_canon, start_html)]
    visited = {start_canon}

    if max_pages <= 1:
        return pages

    try:
        soup = BeautifulSoup(start_html, "lxml")
        links: list[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("tel:", "mailto:", "javascript:", "#")):
                continue
            full = urllib.parse.urljoin(start_url, href).split("#")[0]
            if should_skip_crawl_url(full):
                continue
            canon = _canonical_page_url(full)
            if _extract_domain(canon) != base_domain:
                continue
            if canon in visited:
                continue
            links.append(canon)

        priority = ["login", "signin", "register", "signup", "account", "auth", "войти", "регистрация"]
        links.sort(key=lambda u: 0 if any(p in u.lower() for p in priority) else 1)

        for link in links[:max_pages - 1]:
            try:
                r = session.get(link, timeout=TIMEOUT, headers=HEADERS,
                                verify=False, allow_redirects=True)
                if should_skip_crawl_url(r.url):
                    continue
                if r.status_code == 200 and "text/html" in r.headers.get("Content-Type", ""):
                    canon = _canonical_page_url(r.url)
                    if canon in visited:
                        continue
                    visited.add(canon)
                    pages.append((canon, r.text))
            except Exception as e:
                logger.debug("Crawl skip %s: %s", link, e)
    except Exception as e:
        logger.warning("Crawl error %s: %s", start_url, e)

    return pages


def _scan_html(html: str, url: str) -> tuple[list[AuthHit], bool]:
    """Сканирует HTML на наличие сервисов авторизации и проблем безопасности."""
    hits: list[AuthHit] = []
    html_lower = html.lower()
    soup = BeautifulSoup(html, "lxml")

    has_login_form = False
    for form in soup.find_all("form"):
        form_text = form.get_text(" ", strip=True).lower()
        form_html = str(form).lower()
        if any(kw in form_text for kw in ["войти", "login", "sign in", "авторизац"]):
            has_login_form = True
        if any(kw in form_html for kw in ["type=\"password\"", "type='password'"]):
            has_login_form = True

    for entry in RUSSIAN_SERVICES:
        matched = []
        all_snippets = []
        for pattern in entry.patterns:
            if re.search(pattern, html_lower, re.IGNORECASE):
                matched.append(pattern)
                all_snippets.extend(_extract_snippets(html, pattern))
        if matched:
            hits.append(AuthHit(
                entry=entry, check_key=entry.id, name=entry.name,
                category="auth_allowed", allowed=True, severity="low",
                found_on=[url], matched_patterns=matched,
                description=entry.description, legal_basis=entry.legal_basis,
                advice=entry.advice, snippets=all_snippets[:3],
            ))

    for entry in FOREIGN_SERVICES:
        matched = []
        all_snippets = []
        for pattern in entry.patterns:
            if re.search(pattern, html_lower, re.IGNORECASE):
                matched.append(pattern)
                all_snippets.extend(_extract_snippets(html, pattern))
        if matched:
            hits.append(AuthHit(
                entry=entry, check_key=entry.id, name=entry.name,
                category="auth_foreign", allowed=False,
                severity=entry.risk,
                found_on=[url], matched_patterns=matched,
                description=entry.description, legal_basis=entry.legal_basis,
                advice=entry.advice, snippets=all_snippets[:3],
            ))

    for entry in REC_TECH_SOURCES:
        matched = []
        all_snippets = []
        for pattern in entry.patterns:
            if re.search(pattern, html_lower, re.IGNORECASE):
                matched.append(pattern)
                all_snippets.extend(_extract_snippets(html, pattern))
        if matched:
            hits.append(AuthHit(
                entry=entry, check_key=entry.id, name=entry.name,
                category="rec_tech", allowed=False,
                severity=entry.risk,
                found_on=[url], matched_patterns=matched,
                description=entry.description, legal_basis=entry.legal_basis,
                advice=entry.advice, snippets=all_snippets[:3],
            ))

    for key, check in SECURITY_CHECKS.items():
        matched = []
        all_snippets = []
        for pattern in check["patterns"]:
            if re.search(pattern, html_lower, re.IGNORECASE):
                matched.append(pattern)
                all_snippets.extend(_extract_snippets(html, pattern))
        if matched:
            hits.append(AuthHit(
                entry=None, check_key=key, name=check["description"],
                category="security", allowed=True, severity=check["severity"],
                found_on=[url], matched_patterns=matched,
                description=check["description"],
                legal_basis=check.get("legal_basis", ""),
                advice="", snippets=all_snippets[:3],
            ))

    return hits, has_login_form


def check_auth(url: str, max_pages: int = MAX_PAGES) -> AuthCheckResult:
    """Проверяет сайт на соответствие требованиям к регистрации/авторизации."""
    import warnings
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    start = time.time()
    url = _normalize_url(url)
    if not url:
        return AuthCheckResult(domain="", url="", error="Невалидный URL")

    domain = _extract_domain(url)
    result = AuthCheckResult(domain=domain, url=url)

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        resp = session.get(url, timeout=TIMEOUT, verify=False, allow_redirects=True)
        result.http_status = resp.status_code
        result.url = resp.url

        if resp.status_code not in (200, 301, 302, 403):
            result.error = f"HTTP {resp.status_code}"
            result.check_time_sec = round(time.time() - start, 1)
            return result

        result.accessible = True
        start_html = resp.text

    except requests.exceptions.Timeout:
        result.error = "Таймаут подключения"
        result.check_time_sec = round(time.time() - start, 1)
        return result
    except requests.exceptions.ConnectionError as e:
        result.error = f"Ошибка соединения: {e}"
        result.check_time_sec = round(time.time() - start, 1)
        return result
    except Exception as e:
        result.error = str(e)
        result.check_time_sec = round(time.time() - start, 1)
        return result

    pages = _crawl_pages(session, result.url, start_html, max_pages)
    result.pages_checked = list(dict.fromkeys(p[0] for p in pages))
    logger.info("[auth] %s: проверяем %d страниц", domain, len(result.pages_checked))

    # Агрегируем находки
    hits_agg: dict[str, dict] = {}
    has_login = False

    for page_url, page_html in pages:
        page_canon = _canonical_page_url(page_url)
        page_hits, page_has_login = _scan_html(page_html, page_canon)
        has_login = has_login or page_has_login

        for hit in page_hits:
            key = hit.check_key
            if key not in hits_agg:
                hits_agg[key] = {
                    "entry":      hit.entry,
                    "check_key":  hit.check_key,
                    "name":       hit.name,
                    "category":   hit.category,
                    "allowed":    hit.allowed,
                    "severity":   hit.severity,
                    "found_on":   [],
                    "matched_patterns": set(),
                    "snippets":   [],
                    "description": hit.description,
                    "legal_basis": hit.legal_basis,
                    "advice":     hit.advice,
                }
            if page_canon not in hits_agg[key]["found_on"]:
                hits_agg[key]["found_on"].append(page_canon)
            hits_agg[key]["matched_patterns"].update(hit.matched_patterns)
            for s in hit.snippets:
                if s not in hits_agg[key]["snippets"] and len(hits_agg[key]["snippets"]) < 3:
                    hits_agg[key]["snippets"].append(s)

    for key, data in hits_agg.items():
        result.hits.append(AuthHit(
            entry=data["entry"],
            check_key=data["check_key"],
            name=data["name"],
            category=data["category"],
            allowed=data["allowed"],
            severity=data["severity"],
            found_on=data["found_on"],
            matched_patterns=list(data["matched_patterns"]),
            description=data["description"],
            legal_basis=data["legal_basis"],
            advice=data["advice"],
            snippets=data["snippets"],
        ))

    result.has_login_form = has_login

    # Определяем флаги рекомендательных технологий
    rec_tech_ids = {e.id for e in REC_TECH_SOURCES}
    found_rec_tech_ids = {h.check_key for h in result.hits if h.category == "rec_tech"}
    result.has_rec_tech = "rec_tech_detected" in found_rec_tech_ids
    result.has_rec_disclosure = "rec_tech_disclosure" in found_rec_tech_ids
    result.has_rec_rules_doc = "rec_tech_rules_doc" in found_rec_tech_ids

    # Шаг 5 — absence checks: если rec_tech обнаружен, но нет disclosure/rules_doc
    if result.has_rec_tech:
        if not result.has_rec_disclosure:
            entry_disc = next((e for e in REC_TECH_SOURCES if e.id == "rec_tech_disclosure"), None)
            if entry_disc:
                result.hits.append(AuthHit(
                    entry=entry_disc, check_key="rec_tech_disclosure",
                    name=entry_disc.name, category="rec_tech", allowed=False,
                    severity="high", found_on=[], matched_patterns=[],
                    description=entry_disc.description, legal_basis=entry_disc.legal_basis,
                    advice=entry_disc.advice,
                ))
        if not result.has_rec_rules_doc:
            entry_rules = next((e for e in REC_TECH_SOURCES if e.id == "rec_tech_rules_doc"), None)
            if entry_rules:
                result.hits.append(AuthHit(
                    entry=entry_rules, check_key="rec_tech_rules_doc",
                    name=entry_rules.name, category="rec_tech", allowed=False,
                    severity="high", found_on=[], matched_patterns=[],
                    description=entry_rules.description, legal_basis=entry_rules.legal_basis,
                    advice=entry_rules.advice,
                ))

    # Собираем список найденных разрешённых методов авторизации
    result.allowed_auth_found = list({
        h.name for h in result.hits
        if h.category == "auth_allowed" and h.allowed
    })

    # Рассчитываем суммарные штрафы
    total_fine_citizens = 0
    total_fine_officials = 0
    total_fine_legal = 0
    
    for hit in result.hits:
        if hit.entry and not hit.allowed:
            total_fine_citizens += hit.entry.fine_citizens
            total_fine_officials += hit.entry.fine_officials
            total_fine_legal += hit.entry.fine_legal
    
    result.total_fine_citizens = total_fine_citizens
    result.total_fine_officials = total_fine_officials
    result.total_fine_legal = total_fine_legal
    
    session.close()
    result.check_time_sec = round(time.time() - start, 1)
    logger.info("[auth] %s: найдено %d сервисов за %.1f сек, штраф юрлиц: %d руб.",
                domain, result.total_count, result.check_time_sec, total_fine_legal)
    return result

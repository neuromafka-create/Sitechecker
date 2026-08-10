# sanctions/checker.py
"""
Проверка сайтов на наличие санкционных, заблокированных
и юридически рискованных иностранных ресурсов.
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
from sanctions.sources import DATABASE, SEVERITY_LABEL, SanctionEntry

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
MAX_PAGES = 3   # Проверяем главную + несколько дополнительных страниц


@dataclass
class SanctionHit:
    """Одно найденное совпадение на странице."""
    entry:       SanctionEntry
    found_on:    list[str]          # список URL страниц где найдено
    matched_patterns: list[str]     # какие паттерны сработали


@dataclass
class ScanResult:
    """Полный результат проверки одного домена."""
    domain:       str
    url:          str
    accessible:   bool              = False
    http_status:  int               = 0
    error:        str               = ""
    pages_checked: list[str]        = field(default_factory=list)
    hits:         list[SanctionHit] = field(default_factory=list)
    check_time_sec: float           = 0.0

    @property
    def critical_count(self) -> int:
        return sum(1 for h in self.hits if h.entry.severity == "critical")

    @property
    def high_count(self) -> int:
        return sum(1 for h in self.hits if h.entry.severity == "high")

    @property
    def medium_count(self) -> int:
        return sum(1 for h in self.hits if h.entry.severity == "medium")

    @property
    def low_count(self) -> int:
        return sum(1 for h in self.hits if h.entry.severity == "low")

    @property
    def total_count(self) -> int:
        return len(self.hits)

    @property
    def risk_level(self) -> str:
        if not self.accessible and self.error:
            return "error"
        if self.critical_count > 0:
            return "critical"
        if self.high_count > 0:
            return "high"
        if self.medium_count > 0:
            return "medium"
        if self.low_count > 0:
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
            "check_time_sec": self.check_time_sec,
            "hits": [
                {
                    "name":        h.entry.name,
                    "category":    h.entry.category,
                    "severity":    h.entry.severity,
                    "severity_label": SEVERITY_LABEL[h.entry.severity][0],
                    "legal_basis": h.entry.legal_basis,
                    "description": h.entry.description,
                    "advice":      h.entry.advice,
                    "found_on":    h.found_on,
                    "matched_patterns": h.matched_patterns,
                }
                for h in sorted(
                    self.hits,
                    key=lambda h: ["critical","high","medium","low"].index(h.entry.severity)
                )
            ],
        }


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
    """Единый вид URL страницы для сравнения и вывода (без query/fragment)."""
    p = urllib.parse.urlparse(url)
    path = p.path.rstrip("/") or "/"
    netloc = p.netloc.lower().removeprefix("www.")
    scheme = (p.scheme or "https").lower()
    return urllib.parse.urlunparse((scheme, netloc, path, "", "", ""))


def _crawl_pages(session: requests.Session, start_url: str,
                 start_html: str, max_pages: int = MAX_PAGES) -> list[tuple[str, str]]:
    """
    Краулит сайт начиная с start_url, возвращает [(url, html), ...].
    Придерживается одного домена.
    """
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

        # Приоритетные страницы
        priority = ["contact", "contacts", "about", "services", "product", "shop"]
        links.sort(key=lambda u: 0 if any(p in u.lower() for p in priority) else 1)

        for link in links[:max_pages - 1]:
            try:
                r = session.get(link, timeout=TIMEOUT, headers=HEADERS,
                                verify=False, allow_redirects=True)
                if should_skip_crawl_url(r.url):
                    logger.debug("Crawl skip auth/register %s", r.url)
                    continue
                if r.status_code == 200 and "text/html" in r.headers.get("Content-Type", ""):
                    canon = _canonical_page_url(r.url)
                    if canon in visited:
                        logger.debug("Crawl skip duplicate %s", canon)
                        continue
                    visited.add(canon)
                    pages.append((canon, r.text))
            except Exception as e:
                logger.debug("Crawl skip %s: %s", link, e)
    except Exception as e:
        logger.warning("Crawl error %s: %s", start_url, e)

    return pages


# Ложные срабатывания: Tilda/конструкторы кладут в JS шаблон соцссылок
# проверки вида item.indexOf('facebook.com') / item.indexOf("wa.me") —
# домены перечислены «на будущее», реальных ссылок на сайте нет.
_INDEXOF_FP_RE = re.compile(
    r"""indexof\s*\(\s*['"][^'"]{0,80}$""",
    re.IGNORECASE,
)
# Весь блок t_social_add_item / data-social-links шаблона Tilda
_TILDA_SOCIAL_SCRIPT_MARKERS = (
    "t_social_add_item",
    "data-social-links",
    "t-sociallinks__item_",
    "socialwrapper.insertadjacenthtml",
)


def _is_template_false_positive(html_lower: str, start: int, end: int) -> bool:
    """
    True, если совпадение — строка-шаблон, а не реальный ресурс.
    Типично: item.indexOf('facebook.com') !== -1 в JS Tilda.
    """
    # Окно слева от match
    left = html_lower[max(0, start - 60):start]
    if _INDEXOF_FP_RE.search(left):
        return True
    # indexOf( 'domain' ) — match внутри кавычек после indexOf
    window = html_lower[max(0, start - 50):min(len(html_lower), end + 15)]
    if re.search(
        r"indexof\s*\(\s*['\"][^'\"]{0,60}" + re.escape(html_lower[start:end][:40]),
        window,
        re.IGNORECASE,
    ):
        return True
    # var facebook = '...' template builders that only compare URLs
    if re.search(r"""(?:else\s+)?if\s*\(\s*item\.indexof""", left, re.I):
        return True
    return False


def _strip_constructor_social_templates(html: str) -> str:
    """
    Убирает из HTML встроенные JS-шаблоны соцкнопок конструкторов (Tilda и аналоги),
    где перечислены все возможные сети, но подключены только выбранные.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return html

    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        if not text:
            continue
        low = text.lower()
        # Скрипт-генератор иконок: indexOf по facebook/whatsapp + insertAdjacentHTML
        if "indexof" in low and any(m in low for m in _TILDA_SOCIAL_SCRIPT_MARKERS):
            script.decompose()
            continue
        if low.count("indexof") >= 5 and any(
            d in low for d in (
                "facebook.com", "whatsapp", "linkedin.com",
                "tiktok.com", "pinterest.com", "wa.me",
            )
        ):
            # Универсальный fallback: плотный набор indexOf по соцсетям
            script.decompose()
            continue

    return str(soup)


def _scan_html(html: str, url: str) -> dict[str, list[str]]:
    """
    Сканирует HTML на паттерны санкционных ресурсов.
    Возвращает {entry_name: [matched_pattern, ...]}.

    Отсекает ложные срабатывания на JS-шаблонах конструкторов (Tilda),
    где домены соцсетей встречаются только в item.indexOf('...').
    """
    hits: dict[str, list[str]] = {}
    cleaned = _strip_constructor_social_templates(html)
    html_lower = cleaned.lower()

    for entry in DATABASE:
        matched = []
        for pattern in entry.patterns:
            try:
                found_real = False
                for m in re.finditer(pattern, html_lower, re.IGNORECASE):
                    if _is_template_false_positive(html_lower, m.start(), m.end()):
                        continue
                    found_real = True
                    break
                if found_real:
                    matched.append(pattern)
            except re.error:
                pass
        if matched:
            hits[entry.name] = matched

    return hits


def check_sanctions(url: str, max_pages: int = MAX_PAGES) -> ScanResult:
    """
    Проверяет сайт на наличие санкционных ресурсов.
    Обходит до max_pages страниц.
    """
    import warnings
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    start = time.time()
    url = _normalize_url(url)
    if not url:
        return ScanResult(domain="", url="", error="Невалидный URL")

    domain = _extract_domain(url)
    result = ScanResult(domain=domain, url=url)

    session = requests.Session()
    session.headers.update(HEADERS)

    # Загружаем главную страницу
    try:
        resp = session.get(url, timeout=TIMEOUT, verify=False, allow_redirects=True)
        result.http_status = resp.status_code
        result.url = resp.url  # после редиректов

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

    # Краулим страницы
    pages = _crawl_pages(session, result.url, start_html, max_pages)
    result.pages_checked = list(dict.fromkeys(p[0] for p in pages))
    logger.info("[sanctions] %s: проверяем %d страниц", domain, len(result.pages_checked))

    # Агрегируем находки по всем страницам
    # hits_agg: {entry_name: {found_on: [], matched_patterns: set()}}
    hits_agg: dict[str, dict] = {}

    for page_url, page_html in pages:
        page_canon = _canonical_page_url(page_url)
        page_hits = _scan_html(page_html, page_canon)
        for entry_name, patterns in page_hits.items():
            if entry_name not in hits_agg:
                hits_agg[entry_name] = {
                    "found_on":        [],
                    "found_on_keys":   set(),
                    "matched_patterns": set(),
                }
            if page_canon not in hits_agg[entry_name]["found_on_keys"]:
                hits_agg[entry_name]["found_on_keys"].add(page_canon)
                hits_agg[entry_name]["found_on"].append(page_canon)
            hits_agg[entry_name]["matched_patterns"].update(patterns)

    # Строим SanctionHit объекты
    entry_map = {e.name: e for e in DATABASE}
    for entry_name, data in hits_agg.items():
        if entry_name in entry_map:
            result.hits.append(SanctionHit(
                entry=entry_map[entry_name],
                found_on=data["found_on"],
                matched_patterns=list(data["matched_patterns"]),
            ))

    session.close()
    result.check_time_sec = round(time.time() - start, 1)
    logger.info("[sanctions] %s: найдено %d ресурсов за %.1f сек",
                domain, result.total_count, result.check_time_sec)
    return result

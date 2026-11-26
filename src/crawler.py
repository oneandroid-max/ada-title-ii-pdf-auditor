"""Same-domain website crawler and public PDF discovery module (stdlib-only).

Discovers direct .pdf links as well as municipal CMS document endpoints
(/DocumentCenter, /AgendaCenter, /ArchiveCenter, /ShowDocument) across
same-domain pages, with Content-Type and %PDF- magic-byte verification.
"""

from __future__ import annotations

import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; SecondForeman-PDFUA-Auditor/1.0; "
    "+https://secondforeman.com/pdf-remediation/)"
)
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB safety cap per PDF
MAX_HTML_BYTES = 5 * 1024 * 1024  # 5 MB safety cap per HTML page

_SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff",
    ".mp4", ".mp3", ".wav", ".avi", ".mov", ".webm", ".ogg",
    ".zip", ".tar", ".gz", ".rar", ".7z", ".exe", ".dmg", ".msi",
    ".css", ".js", ".mjs", ".woff", ".woff2", ".ttf", ".eot", ".map",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".xml", ".json",
}

_PDF_ENDPOINT_RE = re.compile(
    r"(/documentcenter/view/|/agendacenter/viewfile/|/archivecenter/viewfile/|"
    r"/showdocument(\.aspx)?|/viewfile/|/downloadfile|/getfile|/pdf/)",
    re.IGNORECASE,
)

_PRIORITY_LINK_RE = re.compile(
    r"(documentcenter|agendacenter|archivecenter|documents?|agendas?|minutes|"
    r"accessibility|ada|forms?|notices?|budgets?|ordinances?|packets?|reports?|"
    r"public-hearing|samples?|pdf)",
    re.IGNORECASE,
)

_CONTENT_DISPOSITION_FILENAME_RE = re.compile(
    r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)"?',
    re.IGNORECASE,
)


class _LinkExtractor(HTMLParser):
    """Extract href/src/data URLs and anchor text from HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "").strip() for k, v in attrs}
        tag_l = tag.lower()
        if tag_l == "a" and attr_map.get("href"):
            self._current_href = attr_map["href"]
            self._current_text = []
        elif tag_l in {"iframe", "embed"} and attr_map.get("src"):
            self.links.append((attr_map["src"], tag_l))
        elif tag_l == "object" and attr_map.get("data"):
            self.links.append((attr_map["data"], "object"))

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current_href is not None:
            text = re.sub(r"\s+", " ", "".join(self._current_text)).strip()
            self.links.append((self._current_href, text))
            self._current_href = None
            self._current_text = []


@dataclass
class FetchedResource:
    url: str
    final_url: str
    status: int
    content_type: str
    filename: str
    data: bytes
    is_pdf: bool
    is_html: bool


def _normalize_url(raw_url: str, base_url: str = "") -> str | None:
    if not raw_url:
        return None
    raw_url = raw_url.strip()
    if raw_url.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    joined = urllib.parse.urljoin(base_url, raw_url) if base_url else raw_url
    parsed = urllib.parse.urlsplit(joined)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    # Strip fragment only; keep query string because CMS document IDs live in query params
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _same_domain(url: str, allowed_hosts: set[str]) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    host_l = host.lower().removeprefix("www.")
    return host_l in allowed_hosts


def _looks_like_pdf_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    path_l = parsed.path.lower()
    if path_l.endswith(".pdf"):
        return True
    if _PDF_ENDPOINT_RE.search(path_l):
        return True
    if "pdf" in parsed.query.lower() and ("download" in path_l or "view" in path_l or "file" in path_l):
        return True
    return False


def _has_skippable_extension(url: str) -> bool:
    path_l = urllib.parse.urlsplit(url).path.lower()
    for ext in _SKIP_EXTENSIONS:
        if path_l.endswith(ext):
            return True
    return False


def _extract_filename(url: str, content_disposition: str | None) -> str:
    if content_disposition:
        m = _CONTENT_DISPOSITION_FILENAME_RE.search(content_disposition)
        if m:
            candidate = urllib.parse.unquote(m.group(1).strip())
            if candidate:
                return candidate
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    name = path.rstrip("/").split("/")[-1]
    return name or "document.pdf"


def fetch_url(url: str, timeout_secs: int = 15) -> FetchedResource | None:
    """Fetch a URL and classify whether it is a PDF or HTML page."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/pdf,text/html,application/xhtml+xml,*/*;q=0.8",
        },
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout_secs, context=ctx) as resp:
            status = getattr(resp, "status", 200) or 200
            final_url = resp.geturl() or url
            content_type = (resp.headers.get("Content-Type") or "").lower()
            content_disp = resp.headers.get("Content-Disposition")
            # Peek first bytes to accurately detect PDF vs HTML
            head = resp.read(16)
            is_pdf = head.lstrip().startswith(b"%PDF-") or "application/pdf" in content_type
            is_html = (not is_pdf) and (
                "text/html" in content_type
                or "application/xhtml" in content_type
                or b"<html" in head.lower()
                or b"<!doctype" in head.lower()
            )
            limit = MAX_PDF_BYTES if is_pdf else MAX_HTML_BYTES
            rest = resp.read(max(0, limit - len(head)))
            data = head + rest
            # Secondary %PDF- check within first 1024 bytes (some PDFs have leading whitespace/garbage)
            if not is_pdf and b"%PDF-" in data[:1024]:
                is_pdf = True
                is_html = False
            filename = _extract_filename(final_url, content_disp)
            return FetchedResource(
                url=url,
                final_url=final_url,
                status=status,
                content_type=content_type,
                filename=filename,
                data=data,
                is_pdf=is_pdf,
                is_html=is_html,
            )
    except urllib.error.HTTPError as exc:
        logger.warning("HTTP %s fetching %s", exc.code, url)
        return None
    except Exception as exc:
        logger.warning("Error fetching %s: %s", url, exc)
        return None


def crawl_and_discover_pdfs(
    start_urls: list[str],
    *,
    max_pages: int = 25,
    max_pdfs: int = 50,
    timeout_secs: int = 15,
    on_page_crawled: Callable[[str], None] | None = None,
    on_pdf_discovered: Callable[[FetchedResource, str], None] | None = None,
) -> dict[str, int]:
    """Crawl start_urls (same-domain pages + direct PDFs) and invoke callback per discovered PDF."""
    normalized_starts: list[str] = []
    allowed_hosts: set[str] = set()
    for raw in start_urls:
        norm = _normalize_url(raw)
        if norm:
            normalized_starts.append(norm)
            host = (urllib.parse.urlsplit(norm).hostname or "").lower().removeprefix("www.")
            if host:
                allowed_hosts.add(host)

    visited_pages: set[str] = set()
    queued_pages: set[str] = set()
    seen_pdfs: set[str] = set()
    page_queue: deque[tuple[str, str]] = deque()
    pdf_queue: deque[tuple[str, str]] = deque()

    for url in normalized_starts:
        if _looks_like_pdf_url(url):
            pdf_queue.append((url, url))
        else:
            page_queue.append((url, url))
            queued_pages.add(url)

    pages_crawled = 0
    pdfs_audited = 0

    def _process_pdf_candidate(candidate_url: str, source_page: str) -> None:
        nonlocal pdfs_audited, pages_crawled
        if pdfs_audited >= max_pdfs or candidate_url in seen_pdfs:
            return
        seen_pdfs.add(candidate_url)
        res = fetch_url(candidate_url, timeout_secs=timeout_secs)
        if res is None:
            return
        if res.is_pdf:
            seen_pdfs.add(res.final_url)
            pdfs_audited += 1
            if on_pdf_discovered:
                on_pdf_discovered(res, source_page)
        elif res.is_html and pages_crawled < max_pages and res.final_url not in visited_pages:
            # Some /DocumentCenter/ links are HTML directory pages listing PDFs
            _ingest_html_page(res, source_page)

    def _ingest_html_page(res: FetchedResource, source_page: str) -> None:
        nonlocal pages_crawled
        if res.final_url in visited_pages or pages_crawled >= max_pages:
            return
        visited_pages.add(res.url)
        visited_pages.add(res.final_url)
        pages_crawled += 1
        if on_page_crawled:
            on_page_crawled(res.final_url)

        parser = _LinkExtractor()
        try:
            parser.feed(res.data.decode("utf-8", errors="replace"))
        except Exception:
            return

        priority_pages: list[tuple[str, str]] = []
        regular_pages: list[tuple[str, str]] = []

        for href, anchor_text in parser.links:
            resolved = _normalize_url(href, res.final_url)
            if not resolved or _has_skippable_extension(resolved):
                continue
            if _looks_like_pdf_url(resolved):
                if resolved not in seen_pdfs:
                    pdf_queue.append((resolved, res.final_url))
            elif (
                _same_domain(resolved, allowed_hosts)
                and resolved not in visited_pages
                and resolved not in queued_pages
            ):
                queued_pages.add(resolved)
                if _PRIORITY_LINK_RE.search(resolved) or _PRIORITY_LINK_RE.search(anchor_text):
                    priority_pages.append((resolved, res.final_url))
                else:
                    regular_pages.append((resolved, res.final_url))

        # Put high-value document/agenda/ADA links ahead of regular links
        for item in reversed(priority_pages):
            page_queue.appendleft(item)
        for item in regular_pages:
            page_queue.append(item)

    while (pdf_queue or page_queue) and pdfs_audited < max_pdfs:
        # Drain discovered PDF candidates first so we find PDFs quickly
        while pdf_queue and pdfs_audited < max_pdfs:
            pdf_url, source_page = pdf_queue.popleft()
            _process_pdf_candidate(pdf_url, source_page)

        if pdfs_audited >= max_pdfs or not page_queue or pages_crawled >= max_pages:
            break

        page_url, source_page = page_queue.popleft()
        if page_url in visited_pages:
            continue
        visited_pages.add(page_url)

        res = fetch_url(page_url, timeout_secs=timeout_secs)
        if res is None:
            continue

        if res.is_pdf:
            if res.final_url not in seen_pdfs and pdfs_audited < max_pdfs:
                seen_pdfs.add(page_url)
                seen_pdfs.add(res.final_url)
                pdfs_audited += 1
                if on_pdf_discovered:
                    on_pdf_discovered(res, source_page)
        elif res.is_html:
            # Remove from visited briefly so _ingest_html_page records it cleanly
            visited_pages.discard(page_url)
            _ingest_html_page(res, source_page)

    return {
        "pagesCrawled": pages_crawled,
        "pdfsAudited": pdfs_audited,
    }

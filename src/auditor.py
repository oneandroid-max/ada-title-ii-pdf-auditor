"""ISO 14289-1 (PDF/UA-1) & WCAG 2.1 AA PDF accessibility auditor powered by pypdf.

Built and operated by an AI agent (Google Gemini) with Paul Lewis human sign-off,
guaranteed by Second Foreman LLC (https://secondforeman.com/pdf-remediation/).
"""

from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

REMEDIATION_URL = "https://secondforeman.com/pdf-remediation/"

# Published Second Foreman LLC unit rates (Fixed-Fee Remediation Order Schedule)
RATE_STANDARD_TEXT_PAGE = 1.50
RATE_SCANNED_OCR_PAGE = 3.00
RATE_COMPLEX_TABLE_PAGE = 4.50

_FILENAME_TITLE_RE = re.compile(
    r"^(microsoft\s+word\s*-|untitled|document\d*|slide\s*\d*|powerpoint\s+presentation|"
    r"adobe\s+indesign|word\s+document)|(\.(pdf|docx?|xlsx?|pptx?|pub|indd|txt|rtf))$",
    re.IGNORECASE,
)


def _deref(obj: Any) -> Any:
    """Safely dereference pypdf IndirectObject instances."""
    seen: set[int] = set()
    while isinstance(obj, IndirectObject):
        key = (obj.idnum, obj.generation)
        if hash(key) in seen:
            return None
        seen.add(hash(key))
        try:
            obj = obj.get_object()
        except Exception:
            return None
    return obj


def _to_str(val: Any) -> str | None:
    val = _deref(val)
    if val is None:
        return None
    if isinstance(val, bytes):
        if val.startswith(b"\xfe\xff"):
            text = val[2:].decode("utf-16-be", errors="replace")
        elif val.startswith(b"\xff\xfe"):
            text = val[2:].decode("utf-16-le", errors="replace")
        else:
            text = val.decode("utf-8", errors="replace")
    else:
        text = str(val)
    cleaned = re.sub(r"\s+", " ", text.replace("\x00", "")).strip()
    return cleaned or None


def _is_truthy_pdf_bool(val: Any) -> bool:
    val = _deref(val)
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() == "true"


def _extract_xmp_info(root: DictionaryObject) -> tuple[bool, str | None]:
    """Return (pdf_ua_xmp, xmp_title) from /Metadata XMP stream."""
    meta_ref = root.get("/Metadata")
    meta = _deref(meta_ref)
    if meta is None or not hasattr(meta, "get_data"):
        return False, None

    try:
        raw = meta.get_data()
    except Exception:
        return False, None

    xmp_text = raw.decode("utf-8", errors="replace")
    # ISO 14289-1 §5 requires http://www.aiim.org/pdfua/ns/id/ and pdfuaid:part=1
    has_pdfua_ns = "http://www.aiim.org/pdfua/ns/id/" in xmp_text.lower()
    has_pdfua_part = bool(
        re.search(r"pdfuaid:part\s*(?:=\s*['\"]1['\"]|>\s*1\s*<)", xmp_text, re.IGNORECASE)
    )
    pdf_ua_xmp = has_pdfua_ns and has_pdfua_part
    if not pdf_ua_xmp and has_pdfua_ns and "pdfuaid" in xmp_text.lower():
        # Handle RDF default-namespace or alternate serialization carrying pdfuaid namespace
        pdf_ua_xmp = bool(re.search(r"<[^>]*part[^>]*>\s*1\s*<", xmp_text, re.IGNORECASE))

    xmp_title: str | None = None
    try:
        start = xmp_text.find("<x:xmpmeta")
        end = xmp_text.rfind("</x:xmpmeta>")
        xml_slice = xmp_text[start : end + len("</x:xmpmeta>")] if start != -1 and end != -1 else xmp_text
        tree = ET.fromstring(xml_slice)
        for elem in tree.iter():
            if elem.tag.endswith("}title") or elem.tag == "title":
                # Collect text inside rdf:Alt/rdf:li
                parts = [t.strip() for t in elem.itertext() if t and t.strip()]
                if parts:
                    xmp_title = re.sub(r"\s+", " ", " ".join(parts)).strip()
                    break
    except Exception:
        m = re.search(r"<dc:title[^>]*>.*?</dc:title>", xmp_text, re.IGNORECASE | re.DOTALL)
        if m:
            stripped = re.sub(r"<[^>]+>", " ", m.group(0))
            cleaned = re.sub(r"\s+", " ", stripped).strip()
            if cleaned:
                xmp_title = cleaned

    return pdf_ua_xmp, xmp_title


def _extract_role_map(struct_tree_root: DictionaryObject) -> dict[str, str]:
    role_map_raw = _deref(struct_tree_root.get("/RoleMap"))
    mapping: dict[str, str] = {}
    if isinstance(role_map_raw, DictionaryObject):
        for k, v in role_map_raw.items():
            v_obj = _deref(v)
            if v_obj is not None:
                mapping[str(k)] = str(v_obj)
    return mapping


def _resolve_role(tag: str, role_map: dict[str, str]) -> str:
    seen: set[str] = set()
    cur = tag
    while cur in role_map and cur not in seen:
        seen.add(cur)
        cur = role_map[cur]
    return cur


def _walk_struct_tree(struct_tree_root: DictionaryObject) -> dict[str, int]:
    """Traverse /StructTreeRoot to audit Figure alt text, Tables, and TH headers."""
    role_map = _extract_role_map(struct_tree_root)
    counts = {
        "figure_count": 0,
        "missing_alt_figures": 0,
        "table_count": 0,
        "table_header_count": 0,
        "heading_count": 0,
    }
    visited: set[int] = set()

    def _visit(node: Any) -> None:
        raw = _deref(node)
        if raw is None:
            return
        if isinstance(raw, (list, ArrayObject)):
            for child in raw:
                _visit(child)
            return
        if not isinstance(raw, DictionaryObject):
            return
        node_id = id(raw)
        if node_id in visited:
            return
        visited.add(node_id)

        s_val = _deref(raw.get("/S"))
        if s_val is not None:
            role = _resolve_role(str(s_val), role_map)
            if role == "/Figure":
                counts["figure_count"] += 1
                alt = _to_str(raw.get("/Alt"))
                actual = _to_str(raw.get("/ActualText"))
                if not alt and not actual:
                    counts["missing_alt_figures"] += 1
            elif role == "/Table":
                counts["table_count"] += 1
            elif role == "/TH":
                counts["table_header_count"] += 1
            elif role in {"/H", "/H1", "/H2", "/H3", "/H4", "/H5", "/H6"}:
                counts["heading_count"] += 1

        k = raw.get("/K")
        if k is not None:
            _visit(k)

    _visit(struct_tree_root)
    return counts


def _count_page_images_and_scanned(reader: PdfReader) -> tuple[int, int]:
    """Return (scanned_image_pages, meaningful_untagged_images)."""
    scanned_pages = 0
    meaningful_images = 0

    for page in reader.pages:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""

        # Inspect XObject images (including nested Form XObjects one level deep)
        smask_ids: set[int] = set()
        page_images: list[DictionaryObject] = []

        def _collect_xobjects(resources: Any, depth: int = 0) -> None:
            if depth > 2:
                return
            res = _deref(resources)
            if not isinstance(res, DictionaryObject):
                return
            xobjs = _deref(res.get("/XObject"))
            if not isinstance(xobjs, DictionaryObject):
                return
            for _, xobj_ref in xobjs.items():
                xobj = _deref(xobj_ref)
                if not isinstance(xobj, DictionaryObject):
                    continue
                subtype = str(_deref(xobj.get("/Subtype")) or "")
                if subtype == "/Image":
                    smask = _deref(xobj.get("/SMask"))
                    if isinstance(smask, DictionaryObject):
                        smask_ids.add(id(smask))
                    page_images.append(xobj)
                elif subtype == "/Form":
                    _collect_xobjects(xobj.get("/Resources"), depth + 1)

        _collect_xobjects(page.get("/Resources"))

        non_mask_images = 0
        large_images = 0
        for img in page_images:
            if id(img) in smask_ids:
                continue
            if _is_truthy_pdf_bool(img.get("/ImageMask")):
                continue
            try:
                w = int(_deref(img.get("/Width")) or 0)
                h = int(_deref(img.get("/Height")) or 0)
            except Exception:
                w, h = 0, 0
            if w >= 32 and h >= 32:
                non_mask_images += 1
            if w >= 200 and h >= 200:
                large_images += 1

        meaningful_images += non_mask_images
        # A page with large raster image(s) and <25 extracted characters is a scanned image page
        if len(text) < 25 and (large_images > 0 or non_mask_images > 0):
            scanned_pages += 1

    return scanned_pages, meaningful_images


def _audit_form_fields(root: DictionaryObject, reader: PdfReader) -> tuple[int, int]:
    """Return (form_field_count, missing_tooltips) across AcroForm fields & page Widget annotations."""
    seen_ids: set[int] = set()
    field_count = 0
    missing_tooltips = 0

    def _walk_field(field_ref: Any, inherited_ft: str | None = None, inherited_tu: str | None = None) -> None:
        nonlocal field_count, missing_tooltips
        field = _deref(field_ref)
        if not isinstance(field, DictionaryObject):
            return
        fid = id(field)
        if fid in seen_ids:
            return
        seen_ids.add(fid)

        ft = _to_str(field.get("/FT")) or inherited_ft
        tu = _to_str(field.get("/TU")) or inherited_tu
        kids = _deref(field.get("/Kids"))

        if isinstance(kids, (list, ArrayObject)) and len(kids) > 0:
            # Check if kids are sub-fields or widget annotations for the same field
            has_child_fields = False
            for kid in kids:
                kid_obj = _deref(kid)
                if isinstance(kid_obj, DictionaryObject) and (kid_obj.get("/T") is not None or kid_obj.get("/FT") is not None):
                    has_child_fields = True
                    break
            if has_child_fields:
                for kid in kids:
                    _walk_field(kid, ft, tu)
                return

        # Terminal interactive form field (exclude hidden / read-only signature-only artifacts? No, all interactive fields need /TU)
        if ft or str(_deref(field.get("/Subtype")) or "") == "/Widget":
            field_count += 1
            if not tu:
                missing_tooltips += 1

    acroform = _deref(root.get("/AcroForm"))
    if isinstance(acroform, DictionaryObject):
        fields = _deref(acroform.get("/Fields"))
        if isinstance(fields, (list, ArrayObject)):
            for f in fields:
                _walk_field(f)

    # Also check page Widget annotations not listed in /AcroForm /Fields
    for page in reader.pages:
        annots = _deref(page.get("/Annots"))
        if not isinstance(annots, (list, ArrayObject)):
            continue
        for annot_ref in annots:
            annot = _deref(annot_ref)
            if not isinstance(annot, DictionaryObject):
                continue
            if str(_deref(annot.get("/Subtype")) or "") != "/Widget":
                continue
            if id(annot) in seen_ids:
                continue
            parent = _deref(annot.get("/Parent"))
            if isinstance(parent, DictionaryObject) and id(parent) in seen_ids:
                continue
            _walk_field(annot)

    return field_count, missing_tooltips


def _estimate_remediation_usd(
    *,
    severity: str,
    page_count: int,
    scanned_image_pages: int,
    table_count: int,
    form_field_count: int,
) -> float:
    """Estimate remediation cost using Second Foreman LLC's published unit schedule."""
    if severity == "PASS" or page_count <= 0:
        return 0.0

    scanned = min(page_count, max(0, scanned_image_pages))
    remaining = max(0, page_count - scanned)
    # Approximate pages containing complex tables (capped by remaining non-scanned pages)
    table_pages = min(remaining, max(0, table_count))
    standard_pages = max(0, remaining - table_pages)

    page_cost = (
        scanned * RATE_SCANNED_OCR_PAGE
        + table_pages * RATE_COMPLEX_TABLE_PAGE
        + standard_pages * RATE_STANDARD_TEXT_PAGE
    )

    if form_field_count <= 0:
        form_cost = 0.0
    elif form_field_count <= 25:
        form_cost = 25.0
    elif form_field_count <= 75:
        form_cost = 40.0
    elif form_field_count <= 150:
        form_cost = 60.0
    else:
        form_cost = 60.0 + math.ceil((form_field_count - 150) / 50) * 20.0

    return round(page_cost + form_cost, 2)


def compute_score_and_severity(metrics: dict[str, Any]) -> tuple[int, str, list[str]]:
    """Compute ISO 14289-1 / WCAG 2.1 AA passScore (0-100), severity, and issue list."""
    issues: list[str] = []
    score = 0

    struct_tree_root = bool(metrics["structTreeRoot"])
    marked = bool(metrics["marked"])
    lang = metrics["lang"]
    title = metrics["title"]
    display_doc_title = bool(metrics["displayDocTitle"])
    pdf_ua_xmp = bool(metrics["pdfUaXmp"])
    page_count = int(metrics["pageCount"])
    scanned_pages = int(metrics["scannedImagePages"])
    missing_alt = int(metrics["missingAltFigures"])
    table_count = int(metrics["tableCount"])
    table_header_count = int(metrics["tableHeaderCount"])
    form_field_count = int(metrics["formFieldCount"])
    missing_tooltips = int(metrics["missingTooltips"])

    # 1. Structure tree (30 pts)
    if struct_tree_root:
        score += 30
    else:
        issues.append("Missing /StructTreeRoot: document is untagged (ISO 14289-1 §7.1 / WCAG 1.3.1)")

    # 2. Marked flag (10 pts)
    if marked:
        score += 10
    else:
        issues.append("Missing /MarkInfo /Marked=true flag (ISO 14289-1 §7.1)")

    # 3. Document language (15 pts)
    if lang:
        score += 15
    else:
        issues.append("Missing /Lang primary document language (WCAG 3.1.1 / ISO 14289-1 §7.2)")

    # 4. Document title (10 pts; 5 pts if auto-generated filename/Word title)
    if title:
        if _FILENAME_TITLE_RE.search(title):
            score += 5
            issues.append(f"Document title appears auto-generated ('{title[:60]}') (WCAG 2.4.2)")
        else:
            score += 10
    else:
        issues.append("Missing document title in XMP dc:title / Info /Title (WCAG 2.4.2)")

    # 5. DisplayDocTitle (10 pts)
    if display_doc_title:
        score += 10
    else:
        issues.append("Missing /ViewerPreferences /DisplayDocTitle=true (ISO 14289-1 §7.1)")

    # 6. PDF/UA-1 XMP conformance identifier (15 pts)
    if pdf_ua_xmp:
        score += 15
    else:
        issues.append("Missing PDF/UA-1 XMP identifier pdfuaid:part=1 (ISO 14289-1 §5)")

    # 7. Content accessibility quality (10 pts)
    content_pts = 10
    if scanned_pages > 0:
        ratio_penalty = round(10 * scanned_pages / max(1, page_count))
        content_pts -= max(4, min(10, ratio_penalty))
        issues.append(f"{scanned_pages}/{page_count} page(s) appear to be scanned images without OCR text (WCAG 1.4.5)")

    if missing_alt > 0:
        content_pts -= min(10 if not struct_tree_root else 6, missing_alt * 2)
        if struct_tree_root:
            issues.append(f"{missing_alt} /Figure tag(s) missing /Alt text (WCAG 1.1.1 / ISO 14289-1 §7.3)")
        else:
            issues.append(f"{missing_alt} untagged image(s) lacking /Figure tags and /Alt text (WCAG 1.1.1)")

    if table_count > 0 and table_header_count == 0:
        content_pts -= 4
        issues.append(f"{table_count} /Table element(s) missing /TH header cells (WCAG 1.3.1 / ISO 14289-1 §7.5)")

    if form_field_count > 0 and missing_tooltips > 0:
        content_pts -= min(5, missing_tooltips * 2)
        issues.append(f"{missing_tooltips}/{form_field_count} form field(s) missing /TU accessible tooltip (WCAG 4.1.2)")

    score += max(0, content_pts)
    score = max(0, min(100, score))

    # Severity determination
    has_blocking_content_issue = (
        scanned_pages > 0
        or missing_alt > 0
        or missing_tooltips > 0
        or (table_count > 0 and table_header_count == 0)
    )
    if score >= 90 and struct_tree_root and marked and bool(lang) and not has_blocking_content_issue:
        severity = "PASS"
    elif score < 30 or (not struct_tree_root and not marked and not lang):
        severity = "CRITICAL"
    elif score < 55 or not struct_tree_root or scanned_pages > 0:
        severity = "HIGH"
    elif score < 80:
        severity = "MEDIUM"
    else:
        severity = "LOW"

    return score, severity, issues


def audit_pdf_bytes(
    data: bytes,
    *,
    url: str = "",
    source_page: str = "",
    filename: str = "",
    http_status: int | None = None,
) -> dict[str, Any]:
    """Audit raw PDF bytes for ADA Title II / ISO 14289-1 / WCAG 2.1 AA conformance."""
    audited_at = datetime.now(timezone.utc).isoformat()
    base_result: dict[str, Any] = {
        "url": url,
        "sourcePage": source_page,
        "filename": filename,
        "httpStatus": http_status,
        "fileSizeBytes": len(data),
        "structTreeRoot": False,
        "marked": False,
        "lang": None,
        "title": None,
        "displayDocTitle": False,
        "pdfUaXmp": False,
        "pageCount": 0,
        "scannedImagePages": 0,
        "missingAltFigures": 0,
        "tableCount": 0,
        "tableHeaderCount": 0,
        "formFieldCount": 0,
        "missingTooltips": 0,
        "passScore": 0,
        "severity": "CRITICAL",
        "status": "FAIL",
        "remediationEstimateUsd": 0.0,
        "issues": [],
        "remediationUrl": REMEDIATION_URL,
        "auditedAt": audited_at,
    }

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                base_result["issues"] = ["PDF is password-encrypted and cannot be read by assistive technologies"]
                return base_result
    except Exception as exc:
        base_result["issues"] = [f"Failed to parse PDF: {exc}"]
        return base_result

    root = _deref(reader.trailer.get("/Root"))
    if not isinstance(root, DictionaryObject):
        base_result["issues"] = ["Malformed PDF: missing /Root Catalog dictionary"]
        return base_result

    page_count = len(reader.pages)
    struct_tree_root_obj = _deref(root.get("/StructTreeRoot"))
    has_struct_tree = isinstance(struct_tree_root_obj, DictionaryObject)

    mark_info = _deref(root.get("/MarkInfo"))
    marked = isinstance(mark_info, DictionaryObject) and _is_truthy_pdf_bool(mark_info.get("/Marked"))

    lang = _to_str(root.get("/Lang"))

    viewer_prefs = _deref(root.get("/ViewerPreferences"))
    display_doc_title = isinstance(viewer_prefs, DictionaryObject) and _is_truthy_pdf_bool(
        viewer_prefs.get("/DisplayDocTitle")
    )

    pdf_ua_xmp, xmp_title = _extract_xmp_info(root)
    info_title: str | None = None
    try:
        if reader.metadata and reader.metadata.title:
            info_title = _to_str(reader.metadata.title)
    except Exception:
        info_title = None
    title = xmp_title or info_title

    scanned_pages, meaningful_images = _count_page_images_and_scanned(reader)

    if has_struct_tree:
        st_counts = _walk_struct_tree(struct_tree_root_obj)
        missing_alt_figures = st_counts["missing_alt_figures"]
        table_count = st_counts["table_count"]
        table_header_count = st_counts["table_header_count"]
    else:
        missing_alt_figures = meaningful_images
        table_count = 0
        table_header_count = 0

    form_field_count, missing_tooltips = _audit_form_fields(root, reader)

    metrics = {
        "structTreeRoot": has_struct_tree,
        "marked": marked,
        "lang": lang,
        "title": title,
        "displayDocTitle": display_doc_title,
        "pdfUaXmp": pdf_ua_xmp,
        "pageCount": page_count,
        "scannedImagePages": scanned_pages,
        "missingAltFigures": missing_alt_figures,
        "tableCount": table_count,
        "tableHeaderCount": table_header_count,
        "formFieldCount": form_field_count,
        "missingTooltips": missing_tooltips,
    }

    pass_score, severity, issues = compute_score_and_severity(metrics)
    remediation_usd = _estimate_remediation_usd(
        severity=severity,
        page_count=page_count,
        scanned_image_pages=scanned_pages,
        table_count=table_count,
        form_field_count=form_field_count,
    )

    base_result.update(metrics)
    base_result["passScore"] = pass_score
    base_result["severity"] = severity
    base_result["status"] = "PASS" if severity == "PASS" else "FAIL"
    base_result["remediationEstimateUsd"] = remediation_usd
    base_result["issues"] = issues
    return base_result

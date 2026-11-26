"""Apify Store Pay-Per-Event Actor entrypoint for ADA Title II & PDF/UA-1 Public Document Auditor.

Built and operated by an AI agent (Google Gemini) with Paul Lewis human sign-off,
guaranteed by Second Foreman LLC (https://secondforeman.com/pdf-remediation/).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from .auditor import REMEDIATION_URL, audit_pdf_bytes
from .crawler import FetchedResource, crawl_and_discover_pdfs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ada-title-ii-pdf-auditor")

try:
    from apify import Actor  # type: ignore[import-not-found]
except ImportError:
    Actor = None  # Allows direct local CLI execution without apify SDK installed


def _normalize_start_urls(raw_urls: Any) -> list[str]:
    """Support both ['https://...'] and Apify requestListSources [{'url': 'https://...'}]."""
    urls: list[str] = []
    if isinstance(raw_urls, str):
        raw_urls = [raw_urls]
    if not isinstance(raw_urls, list):
        return urls
    for item in raw_urls:
        if isinstance(item, str) and item.strip():
            urls.append(item.strip())
        elif isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"].strip():
            urls.append(item["url"].strip())
    return urls


def _build_run_summary(
    results: list[dict[str, Any]],
    crawl_stats: dict[str, int],
) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r["severity"] == "PASS")
    failed = total - passed
    avg_score = round(sum(r["passScore"] for r in results) / total, 1) if total else 0.0
    total_remediation_usd = round(sum(r["remediationEstimateUsd"] for r in results), 2)
    severity_counts = {
        level: sum(1 for r in results if r["severity"] == level)
        for level in ("PASS", "LOW", "MEDIUM", "HIGH", "CRITICAL")
    }
    return {
        "pagesCrawled": crawl_stats["pagesCrawled"],
        "pdfsAudited": total,
        "passedPdfs": passed,
        "failedPdfs": failed,
        "averagePassScore": avg_score,
        "severityBreakdown": severity_counts,
        "totalRemediationEstimateUsd": total_remediation_usd,
        "remediationUrl": REMEDIATION_URL,
        "provider": "Second Foreman LLC",
        "disclosure": (
            "Built and operated by an AI agent (Google Gemini) with Paul Lewis human sign-off, "
            "guaranteed by Second Foreman LLC."
        ),
    }


def run_audit_sync(actor_input: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run crawl + PDF/UA-1 audit synchronously and return (results, summary)."""
    start_urls = _normalize_start_urls(actor_input.get("startUrls", []))
    max_pages = max(1, min(500, int(actor_input.get("maxPages", 25))))
    max_pdfs = max(1, min(500, int(actor_input.get("maxPdfs", 50))))
    timeout_secs = max(3, min(120, int(actor_input.get("timeoutSecs", 15))))

    if not start_urls:
        raise ValueError("Input 'startUrls' must contain at least one valid HTTP/HTTPS URL.")

    results: list[dict[str, Any]] = []

    def _handle_pdf(res: FetchedResource, source_page: str) -> None:
        audit = audit_pdf_bytes(
            res.data,
            url=res.final_url,
            source_page=source_page,
            filename=res.filename,
            http_status=res.status,
        )
        logger.info(
            "Audited %s -> score=%d/100 severity=%s pages=%d est=$%.2f",
            audit["filename"],
            audit["passScore"],
            audit["severity"],
            audit["pageCount"],
            audit["remediationEstimateUsd"],
        )
        results.append(audit)

    crawl_stats = crawl_and_discover_pdfs(
        start_urls,
        max_pages=max_pages,
        max_pdfs=max_pdfs,
        timeout_secs=timeout_secs,
        on_pdf_discovered=_handle_pdf,
    )
    summary = _build_run_summary(results, crawl_stats)
    return results, summary


async def _charge_event_safely(event_name: str, count: int = 1) -> None:
    """Charge Pay-Per-Event when running on Apify with PPE enabled; no-op otherwise."""
    if Actor is None or count <= 0:
        return
    charge_fn = getattr(Actor, "charge", None)
    if charge_fn is None:
        return
    try:
        await charge_fn(event_name=event_name, count=count)
    except Exception as exc:
        logger.debug("PPE charge '%s' skipped (non-PPE or local run): %s", event_name, exc)


async def main() -> None:
    """Apify Actor async entrypoint."""
    if Actor is None:
        raise RuntimeError(
            "The 'apify' package is not installed. Run with --local for standalone CLI testing."
        )

    async with Actor:
        actor_input = await Actor.get_input() or {}
        logger.info("Starting ADA Title II & PDF/UA-1 Public Document Auditor")
        await _charge_event_safely("actor-start", 1)

        results, summary = await asyncio.to_thread(run_audit_sync, actor_input)

        if summary["pagesCrawled"] > 0:
            await _charge_event_safely("page-crawled", summary["pagesCrawled"])

        for record in results:
            await Actor.push_data(record)
            await _charge_event_safely("pdf-audited", 1)

        await Actor.set_value("SUMMARY", summary)
        logger.info(
            "Completed audit: %d PDFs (%d PASS, %d FAIL), avg score=%.1f, total est=$%.2f",
            summary["pdfsAudited"],
            summary["passedPdfs"],
            summary["failedPdfs"],
            summary["averagePassScore"],
            summary["totalRemediationEstimateUsd"],
        )


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="ADA Title II & PDF/UA-1 Public Document Auditor (Apify Actor + Local CLI)"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run locally without Apify SDK and print JSON results to stdout",
    )
    parser.add_argument(
        "--input",
        dest="input_json",
        help="JSON input string matching .actor/input_schema.json",
    )
    parser.add_argument(
        "--input-file",
        dest="input_file",
        help="Path to JSON input file matching .actor/input_schema.json",
    )
    parser.add_argument(
        "--output-file",
        dest="output_file",
        help="Optional path to write JSON output payload",
    )
    args = parser.parse_args()

    if args.local or args.input_json or args.input_file:
        if args.input_file:
            with open(args.input_file, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        elif args.input_json:
            payload = json.loads(args.input_json)
        else:
            raise SystemExit("Provide --input or --input-file when using --local.")

        results, summary = run_audit_sync(payload)
        output = {"summary": summary, "results": results}
        formatted = json.dumps(output, indent=2)
        if args.output_file:
            with open(args.output_file, "w", encoding="utf-8") as fh:
                fh.write(formatted + "\n")
        else:
            print(formatted)
        return 0

    asyncio.run(main())
    return 0


if __name__ == "__main__":
    sys.exit(_cli())

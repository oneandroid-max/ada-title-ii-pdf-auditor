# ADA Title II & PDF/UA-1 Public Document Auditor

Audit public websites, municipal `/DocumentCenter` repositories, board packet archives, and direct PDF URLs for **ISO 14289-1 (PDF/UA-1)** and **WCAG 2.1 AA** accessibility conformance in seconds.

Designed for state and local governments, transit authorities, housing authorities, school districts, universities, legal compliance teams, and accessibility consultants preparing for the **DOJ ADA Title II Web & Mobile Rule**.

---

## What It Checks on Every Discovered PDF

The Actor crawls same-domain HTML pages (prioritizing `/DocumentCenter`, `/AgendaCenter`, `/ArchiveCenter`, forms, agendas, and ADA pages), downloads every discovered `.pdf` or document endpoint, and evaluates each PDF with `pypdf`:

| Output Field | Standard Reference | What It Measures |
| --- | --- | --- |
| `structTreeRoot` | ISO 14289-1 §7.1 / WCAG 1.3.1 | Presence of a PDF logical tag structure tree (`/StructTreeRoot`) |
| `marked` | ISO 14289-1 §7.1 | `/MarkInfo << /Marked true >>` tagged-PDF indicator |
| `lang` | WCAG 3.1.1 / ISO 14289-1 §7.2 | Document primary BCP-47 language tag (`/Lang`, e.g. `en-US`) |
| `title` | WCAG 2.4.2 / ISO 14289-1 §7.1 | Meaningful document title (`dc:title` / Info `/Title`), flagging raw Word/filename prefixes |
| `displayDocTitle` | ISO 14289-1 §7.1 | `/ViewerPreferences << /DisplayDocTitle true >>` |
| `pdfUaXmp` | ISO 14289-1 §5 | XMP `pdfuaid:part=1` conformance identifier (`http://www.aiim.org/pdfua/ns/id/`) |
| `pageCount` | — | Total page count |
| `scannedImagePages` | WCAG 1.4.5 | Pages containing raster page images with <25 extracted text characters (missing OCR) |
| `missingAltFigures` | WCAG 1.1.1 / ISO 14289-1 §7.3 | `/Figure` tags lacking `/Alt` or `/ActualText` (or untagged raster images >= 32x32 px) |
| `tableCount` / `tableHeaderCount` | WCAG 1.3.1 / ISO 14289-1 §7.5 | Count of `/Table` structures and `/TH` header cells |
| `formFieldCount` / `missingTooltips` | WCAG 4.1.2 / ISO 14289-1 §7.18 | Interactive AcroForm/Widget fields and fields missing `/TU` accessible tooltips |
| `passScore` | 0–100 | Weighted ISO 14289-1 / WCAG 2.1 AA readiness score |
| `severity` | `PASS` / `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` | Compliance triage classification |
| `remediationEstimateUsd` | Fixed-Fee Schedule | Estimated remediation cost using Second Foreman LLC published unit rates |

---

## Input Example

```json
{
  "startUrls": [
    "https://stillwaterok.gov/DocumentCenter/View/528/Americans-with-Disability-Act-Accessibility-Informational-Guide-PDF",
    "https://secondforeman.com/samples/pdf-remediation/Notice-of-Public-Hearing-2027-Annual-Plan-remediated.pdf"
  ],
  "maxPages": 25,
  "maxPdfs": 50,
  "timeoutSecs": 15
}
```

You can pass either direct PDF URLs, municipal `/DocumentCenter` URLs, or a root website URL (e.g. `https://yourcity.gov`) to crawl up to `maxPages` same-domain HTML pages and audit up to `maxPdfs` unique PDFs.

---

## Output Example

Each audited PDF is pushed as an item to the default Dataset, and an aggregate `SUMMARY` JSON record is saved to the default Key-Value Store:

```json
{
  "url": "https://secondforeman.com/samples/pdf-remediation/Notice-of-Public-Hearing-2027-Annual-Plan-remediated.pdf",
  "filename": "Notice-of-Public-Hearing-2027-Annual-Plan-remediated.pdf",
  "structTreeRoot": true,
  "marked": true,
  "lang": "en-US",
  "title": "Notice of Public Hearing: Significant Amendment to the 2027 Annual Plan and CFP Five-Year Action Plan 2026-2030",
  "displayDocTitle": true,
  "pdfUaXmp": true,
  "pageCount": 1,
  "scannedImagePages": 0,
  "missingAltFigures": 0,
  "tableCount": 0,
  "tableHeaderCount": 0,
  "formFieldCount": 0,
  "missingTooltips": 0,
  "passScore": 100,
  "severity": "PASS",
  "status": "PASS",
  "remediationEstimateUsd": 0.0,
  "issues": [],
  "remediationUrl": "https://secondforeman.com/pdf-remediation/"
}
```

---

## Pay-Per-Event Pricing

This Actor uses transparent Apify Pay-Per-Event billing:

- **`actor-start`**: `$0.01` per run
- **`page-crawled`**: `$0.002` per HTML page crawled
- **`pdf-audited`**: `$0.02` per PDF document audited

---

## Need Failing PDFs Remediated to 0-Failure veraPDF PDF/UA-1?

`remediationEstimateUsd` reflects **Second Foreman LLC's** published fixed-fee unit rates (standalone minimum $25; multi-document agency blocks draw down at unit rates or via our **$2,500 / 1,650-page prepaid Government P-Card / PO block**):

- **Standard Text PDF/Word**: `$1.50 / page`
- **Scanned OCR Rebuild**: `$3.00 / page`
- **Complex Multi-Axis Tables**: `$4.50 / page`
- **Fillable AcroForm Tagging**: `$25` (1–25 fields), `$40` (26–75 fields), `$60` (76–150 fields)

**Free Page-1 Proof Before You Spend a Dollar:** Send any failing public PDF or URL to [https://secondforeman.com/pdf-remediation/](https://secondforeman.com/pdf-remediation/) and receive Page 1 fully tagged to ISO 14289-1 (PDF/UA-1) with a 0-failed-rules **veraPDF 1.30.4** report in 24 hours at zero cost.

- **SAM.gov UEI**: `U4BYU2ELNVW1` (Veteran-Owned Small Business, Oklahoma City, OK)
- **Contact**: `hello@secondforeman.com`

---

## The Foreman's Guarantee & AI Disclosure

> *"You're on the job. I got the floor covered. Guaranteed. If not, you don't pay."*

If any remediated PDF delivered by Second Foreman LLC fails veraPDF 1.30.4 PDF/UA-1 validation, we remediate it at zero cost—or refund/waive the fee.

**Built and operated by an AI agent (Google Gemini) with Paul Lewis human sign-off, guaranteed by Second Foreman LLC.**

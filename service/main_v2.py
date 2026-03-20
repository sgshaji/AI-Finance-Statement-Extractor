"""
main_v2.py
----------
Hybrid extraction pipeline v2.

Architecture:
  PDF
   |
   v
  CU Locator ----> statement detection + metadata (any language)
   |
   v
  CU prebuilt-read ----> raw markdown + page spans
   |
   v
  Python parser ----> precise rows/values/structure (deterministic)
   |
   v
  CU Extractor ----> canonical keys, English labels, sections (enrichment)
   |
   v
  Schema mapper ----> v1.2 unified JSON per statement

Usage:
    python main_v2.py <path-to-pdf>
    python main_v2.py <path-to-pdf> --no-enrichment  # skip CU extractor enrichment

Output: output_v2/<statement_type>.json per statement + summary.json
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from extractor.cu_client import analyze_document
from extractor.statement_detector import (
    _build_page_map,
    _offset_to_page,
    _find_section_end_by_density,
    _NOTES_TERMINATOR_RE,
)
from extractor.html_table_parser import parse_html_table
from extractor.enrichment import build_enrichment_lookup, enrich_row
from extractor.schema_mapper import assemble_v12_statement

OUTPUT_DIR = Path("output_v2")
STATEMENT_TYPES = ["balance_sheet", "income_statement", "cash_flow"]


def _pdf_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Step 1: CU Locator — detect statements and metadata
# ---------------------------------------------------------------------------

def _parse_locator_statements(locator_result: dict) -> list[dict]:
    """Extract structured statement metadata from the CU locator response."""
    contents = locator_result.get("result", {}).get("contents", [])
    if not contents:
        return []

    fields = contents[0].get("fields", {})
    stmts = fields.get("statements", {}).get("valueArray", [])

    results = []
    for s in stmts:
        props = s.get("valueObject", {})

        def _str(key: str) -> str | None:
            return props.get(key, {}).get("valueString")

        def _int(key: str) -> int | None:
            return props.get(key, {}).get("valueInteger")

        def _bool(key: str) -> bool | None:
            return props.get(key, {}).get("valueBoolean")

        results.append({
            "statement_type": _str("statement_type"),
            "title_raw": _str("title_raw"),
            "title_english": _str("title_english"),
            "page_start": _int("page_start"),
            "page_end": _int("page_end"),
            "company_name": _str("company_name"),
            "company_name_raw": _str("company_name_raw"),
            "report_language": _str("report_language"),
            "currency": _str("currency"),
            "unit": _str("unit"),
            "is_consolidated": _bool("is_consolidated"),
            "accounting_standard": _str("accounting_standard"),
        })

    return results


def _select_consolidated(statements: list[dict]) -> dict[str, dict]:
    """
    Select the first consolidated instance of each statement type.

    Filters for is_consolidated=True and deduplicates by statement_type,
    keeping the first (usually the main) occurrence.
    """
    selected: dict[str, dict] = {}
    for s in statements:
        stype = s.get("statement_type")
        if stype not in STATEMENT_TYPES:
            continue
        if stype in selected:
            continue  # keep first
        # Prefer consolidated, but accept any if no consolidated found
        if s.get("is_consolidated") is True or stype not in selected:
            selected[stype] = s
    return selected


# ---------------------------------------------------------------------------
# Step 2: Get markdown (via prebuilt-read or from locator)
# ---------------------------------------------------------------------------

def _get_markdown_from_locator(locator_result: dict) -> tuple[str, list[dict]]:
    """Extract markdown and pages from the locator result."""
    contents = locator_result.get("result", {}).get("contents", [])
    if not contents:
        return "", []
    c0 = contents[0]
    markdown = c0.get("markdown", "")
    pages = c0.get("pages", [])
    return markdown, pages


# ---------------------------------------------------------------------------
# Step 3: Python parser — locate and extract each statement
# ---------------------------------------------------------------------------

def _locate_statement_in_markdown(
    statement_type: str,
    locator_meta: dict,
    markdown: str,
    pages: list[dict],
) -> dict | None:
    """
    Find the precise markdown offset range for a statement using the
    locator's page info + the markdown page spans.

    Returns {"md_offset": int, "md_end_offset": int, "start_page": int, "end_page": int}
    or None if not found.
    """
    import re

    page_map = _build_page_map(pages)
    if not page_map:
        return None

    # Get page range from locator metadata
    start_page = locator_meta.get("page_start")
    end_page = locator_meta.get("page_end")

    # If locator didn't give reliable pages, search markdown directly
    # for the heading text followed by a <table> tag.
    title_raw = locator_meta.get("title_raw", "")
    heading_patterns = {
        "balance_sheet": ["合并资产负债表", "CONDENSED CONSOLIDATED BALANCE SHEET",
                          "CONSOLIDATED BALANCE SHEET", "BALANCE SHEET"],
        "income_statement": ["合并利润表", "CONDENSED CONSOLIDATED STATEMENTS OF INCOME",
                             "CONSOLIDATED STATEMENTS OF INCOME", "INCOME STATEMENT"],
        "cash_flow": ["合并现金流量表", "CONDENSED CONSOLIDATED STATEMENTS OF CASH",
                       "CONSOLIDATED STATEMENTS OF CASH", "CASH FLOW"],
    }

    # Strategy: find headings that are followed by <table> (actual tables, not TOC)
    direct_offset = None
    for pattern in heading_patterns.get(statement_type, []):
        for m in re.finditer(re.escape(pattern), markdown, re.IGNORECASE):
            # Check if there's a <table> within 500 chars
            lookahead = markdown[m.start():m.start() + 500]
            if "<table>" in lookahead.lower():
                direct_offset = m.start()
                break
        if direct_offset is not None:
            break

    # Also try title_raw from locator
    if direct_offset is None and title_raw:
        for prefix_len in [40, 20, 10]:
            prefix = title_raw.strip()[:prefix_len]
            idx = markdown.find(prefix)
            if idx >= 0 and "<table>" in markdown[idx:idx + 500].lower():
                direct_offset = idx
                break

    if direct_offset is not None:
        start_page = _offset_to_page(direct_offset, page_map) if page_map else 1

    if not start_page:
        return None

    # Convert page numbers to character offsets
    page_start_offset = None
    page_end_offset = None
    for pm_start, pm_end, pm_page in page_map:
        if pm_page == start_page and page_start_offset is None:
            page_start_offset = pm_start
        if end_page and pm_page <= end_page:
            page_end_offset = pm_end

    if page_start_offset is None:
        return None

    # Use direct heading offset if found, otherwise fall back to page offset
    start_offset = direct_offset if direct_offset is not None else (page_start_offset or 0)

    # Find end: the next </table> closing tag after the <table> that follows
    # the heading.  This precisely isolates one financial statement table.
    table_start = markdown.find("<table>", start_offset)
    if table_start >= 0:
        table_end = markdown.find("</table>", table_start)
        if table_end >= 0:
            end_offset = table_end + len("</table>")
        else:
            end_offset = min(len(markdown), start_offset + 20000)
    else:
        end_offset = page_end_offset or _find_section_end_by_density(markdown, start_offset)

    # Check for terminators before end_offset
    term_m = _NOTES_TERMINATOR_RE.search(markdown, start_offset + 200)
    if term_m and term_m.start() < end_offset:
        end_offset = term_m.start()

    return {
        "md_offset": start_offset,
        "md_end_offset": end_offset,
        "start_page": start_page,
        "end_page": end_page or start_page,
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(pdf_path: str, use_enrichment: bool = True) -> None:
    pdf = Path(pdf_path)
    if not pdf.exists():
        print(f"ERROR: File not found: {pdf}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)
    source_file_name = pdf.name

    # ------------------------------------------------------------------ #
    # Step 1 — CU Locator: detect statements + metadata
    # ------------------------------------------------------------------ #
    print(f"\n[1/4] Running CU Locator on '{pdf.name}'...")
    locator_result = analyze_document("financial-statement-locator", str(pdf))

    all_statements = _parse_locator_statements(locator_result)
    selected = _select_consolidated(all_statements)

    print(f"      Found {len(all_statements)} statement(s), selected {len(selected)} consolidated:")
    for stype, meta in selected.items():
        print(f"        {stype}: {meta['title_english']} "
              f"(pages {meta['page_start']}-{meta['page_end']}, "
              f"{meta['currency']}, {meta['unit']})")

    # ------------------------------------------------------------------ #
    # Step 2 — Get markdown from the locator result
    # ------------------------------------------------------------------ #
    print(f"\n[2/4] Extracting markdown...")
    markdown, pages = _get_markdown_from_locator(locator_result)
    print(f"      {len(markdown)} chars, {len(pages)} pages")

    # Save raw locator result
    (OUTPUT_DIR / "locator_result.json").write_text(
        json.dumps(locator_result, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ------------------------------------------------------------------ #
    # Step 3 — CU Extractor: get enrichment data (canonical keys, etc.)
    # ------------------------------------------------------------------ #
    enrichment_lookup: dict = {}
    if use_enrichment:
        print(f"\n[3/4] Running CU Extractor for enrichment...")
        extractor_result = analyze_document("financial-statement-extractor", str(pdf))
        enrichment_lookup = build_enrichment_lookup(extractor_result)
        print(f"      Built enrichment lookup: {len(enrichment_lookup)} labels")

        (OUTPUT_DIR / "extractor_result.json").write_text(
            json.dumps(extractor_result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    else:
        print(f"\n[3/4] Skipping enrichment (--no-enrichment)")

    # ------------------------------------------------------------------ #
    # Step 4 — Python parser + schema mapper per statement
    # ------------------------------------------------------------------ #
    print(f"\n[4/4] Parsing and assembling v1.2 JSON...")

    summary_entries = []
    for stype in STATEMENT_TYPES:
        meta = selected.get(stype)
        if not meta:
            print(f"      {stype}: not found")
            summary_entries.append({
                "statement_type": stype,
                "status": "not_found",
                "page_range": {"start": None, "end": None},
            })
            # Write empty statement
            empty = {
                "schema_version": "1.2.0",
                "document_metadata": {
                    "source_file_name": source_file_name,
                    "company_name": None, "company_name_raw": None,
                    "report_type": "other", "report_language": "en",
                    "source_file_hash": None, "source_country": None,
                    "source_exchange": None, "ticker": None, "identifier": None,
                },
                "statement_metadata": {
                    "statement_type": stype, "statement_title": None,
                    "statement_title_raw": None, "accounting_standard": None,
                    "currency": "USD", "currency_symbol": None,
                    "unit": "ones", "unit_raw": None,
                    "is_consolidated": None, "is_audited": None,
                    "page_range": {"start": 1, "end": 1},
                    "bbox_coordinate_system": "normalized_0_1",
                },
                "columns": [],
                "rows": [],
                "validation": {
                    "status": "not_run",
                    "validator_version": "2.0.0", "rule_set_version": "1.0.0",
                    "validation_run_id": None,
                    "validated_at": None, "warnings": [], "errors": [],
                },
            }
            (OUTPUT_DIR / f"{stype}.json").write_text(
                json.dumps(empty, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            continue

        # Locate statement section in markdown
        location = _locate_statement_in_markdown(stype, meta, markdown, pages)
        if not location:
            print(f"      {stype}: could not locate in markdown")
            continue

        # Parse HTML tables from the documentAnalyzer markdown
        rows, columns, cells = parse_html_table(
            markdown, location["md_offset"], location["md_end_offset"]
        )

        print(f"      {stype}: {len(rows)} rows, {len(columns)} columns, "
              f"pages {location['start_page']}-{location['end_page']}")

        # Assemble v1.2 JSON
        v12_doc = assemble_v12_statement(
            statement_type=stype,
            locator_metadata=meta,
            parser_rows=rows,
            parser_columns=columns,
            parser_cells=cells,
            enrichment_lookup=enrichment_lookup,
            source_file_name=source_file_name,
        )

        # Write output
        out_path = OUTPUT_DIR / f"{stype}.json"
        out_path.write_text(
            json.dumps(v12_doc, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"      Saved: {out_path}")

        # Validation summary
        val = v12_doc["validation"]
        warn_count = len(val["warnings"])
        err_count = len(val["errors"])
        print(f"      Validation: {val['status']} "
              f"({warn_count} warnings, {err_count} errors)")

        summary_entries.append({
            "statement_type": stype,
            "status": "extracted",
            "page_range": {
                "start": location["start_page"],
                "end": location["end_page"],
            },
        })

    # Write summary
    summary = {"summary": summary_entries}
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\nDone. Output in: {OUTPUT_DIR.resolve()}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Financial Statement Extraction Pipeline v2 (Hybrid)"
    )
    parser.add_argument("pdf", help="Path to the PDF file to process")
    parser.add_argument(
        "--no-enrichment",
        action="store_true",
        help="Skip CU extractor enrichment (faster, no canonical keys)",
    )
    args = parser.parse_args()
    run(args.pdf, use_enrichment=not args.no_enrichment)

"""
main.py
-------
Pipeline entry point for the Financial Statement Extraction system.

Usage:
    python main.py <path-to-pdf>           # extraction only
    python main.py <path-to-pdf> --llm     # extraction + LLM label correction

Output files written to ./output/
    raw_result.json      — raw Azure CU response (cached for reprocessing)
    summary.json         — statement locations (pages, status)
    balance_sheet.json   — extracted balance sheet rows/columns/cells
    cash_flow.json       — extracted cash flow statement
    income_statement.json— extracted income statement

The --llm flag enables the LLM post-processing pass (extractor/llm_reconciler.py)
which fixes dropped labels, truncated labels, and noise rows using Azure OpenAI.
Requires AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY in .env.
"""

import argparse
import json
import sys
from pathlib import Path

from extractor import submit_document, poll_result
from extractor import locate_statements, build_statement_json, build_summary

OUTPUT_DIR = Path("output")
STATEMENT_TYPES = ["balance_sheet", "cash_flow", "income_statement"]


def run(pdf_path: str, use_llm: bool = False) -> None:
    pdf = Path(pdf_path)
    if not pdf.exists():
        print(f"ERROR: File not found: {pdf}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)

    # ------------------------------------------------------------------ #
    # Step 1 — Submit PDF to Azure Content Understanding
    # ------------------------------------------------------------------ #
    print(f"\n[1/4] Submitting '{pdf.name}' to Azure Content Understanding...")
    operation_location, raw_result = submit_document(str(pdf))

    # ------------------------------------------------------------------ #
    # Step 2 — Poll if async, or skip if result already returned
    # ------------------------------------------------------------------ #
    if operation_location:
        print(f"      Async mode. Operation URL: {operation_location}")
        print("\n[2/4] Polling for result...")
        raw_result = poll_result(operation_location)
    else:
        print("      Synchronous mode. Result received immediately.")
        print("\n[2/4] Skipping poll (result already available).")

    raw_path = OUTPUT_DIR / "raw_result.json"
    raw_path.write_text(json.dumps(raw_result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"      Saved: {raw_path}")

    # ------------------------------------------------------------------ #
    # Step 3 — Detect statements and page ranges
    # ------------------------------------------------------------------ #
    print("\n[3/4] Detecting statements...")
    locations = locate_statements(raw_result)

    for stype, loc in locations.items():
        if loc:
            print(f"      {stype}: pages {loc['start_page']}–{loc['end_page']}")
        else:
            print(f"      {stype}: not found")

    # ------------------------------------------------------------------ #
    # Step 4 — Build and save output files
    # ------------------------------------------------------------------ #
    print("\n[4/4] Building output files...")
    if use_llm:
        print("      LLM reconciliation pass: ON")

    for stype in STATEMENT_TYPES:
        stmt = build_statement_json(stype, raw_result, locations.get(stype), use_llm=use_llm)
        out_path = OUTPUT_DIR / f"{stype}.json"
        out_path.write_text(json.dumps(stmt, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"      Saved: {out_path}")

    summary = build_summary(locations)
    summary_path = OUTPUT_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"      Saved: {summary_path}")

    # ------------------------------------------------------------------ #
    # Done
    # ------------------------------------------------------------------ #
    print(f"\nDone. All output files are in: {OUTPUT_DIR.resolve()}\n")

    # Print summary to console
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Financial Statement Extraction Pipeline"
    )
    parser.add_argument("pdf", help="Path to the PDF file to process")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Enable LLM post-processing to fix dropped/truncated labels "
             "(requires AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY in .env)",
    )
    args = parser.parse_args()
    run(args.pdf, use_llm=args.llm)

"""
scripts/print_tables.py
-----------------------
Developer utility: print all three financial statements as formatted ASCII
tables to the console, reading from the output/*.json files.

Usage (run from the project root):
    python scripts/print_tables.py

Prerequisites:
    output/balance_sheet.json, output/income_statement.json,
    output/cash_flow.json must exist (produced by main.py or reprocess.py).
"""

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# Locate the output directory relative to this script's location.
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def print_table(stype: str) -> None:
    """Print one statement as a formatted ASCII table."""
    stmt = json.loads((OUTPUT_DIR / f"{stype}.json").read_text(encoding="utf-8"))
    cells = stmt["cells"]
    grid: dict = {}
    for c in cells:
        grid.setdefault(c["row"], {})[c["col"]] = c["content"]

    cols = stmt["columns"]
    c0 = cols[0] if len(cols) > 0 else "Col 1"
    c1 = cols[1] if len(cols) > 1 else "Col 2"

    title = stype.upper().replace("_", " ")
    pages = f"pages {stmt['start_page']}-{stmt['end_page']}"
    nrows = len(grid)

    sep = "-" * 120
    print(f"\n{'='*120}")
    print(f"  {title}   |   {pages}   |   {nrows} rows")
    print(f"{'='*120}")
    print(f"  {'Label':<68}  {c0:>22}  {c1:>22}")
    print(sep)
    for r in sorted(grid):
        label = grid[r].get(0, "")
        v1    = grid[r].get(1, "")
        v2    = grid[r].get(2, "")
        # Section header rows (no values) are indented slightly more
        indent = "    " if not v1 and not v2 else "  "
        print(f"{indent}{label:<68}  {v1:>22}  {v2:>22}")
    print(sep)



for s in ["balance_sheet", "income_statement", "cash_flow"]:
    print_table(s)

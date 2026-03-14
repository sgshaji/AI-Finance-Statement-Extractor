# Financial Statement Extraction Pipeline

Extracts the **Balance Sheet**, **Income Statement**, and **Cash Flow Statement** from any financial PDF using Azure Content Understanding, with an optional LLM post-processing pass to maximise label accuracy.

---

## Architecture

```
PDF
 │
 ▼
Azure Content Understanding (prebuilt-read)
 │  raw markdown + page spans
 ▼
extractor/statement_detector.py
 │  locate statements → parse tokens → rows/columns/cells JSON
 ▼
extractor/llm_reconciler.py  (optional --llm flag)
 │  1. suppress_noise_rows       heuristic — remove currency headers, stubs
 │  2. reconcile_suspect_ghost   LLM — fix dropped labels at page breaks
 │  3. complete_truncated_labels LLM — complete labels cut off mid-phrase
 ▼
output/*.json
```

### Why an LLM pass?

Azure Content Understanding occasionally **drops row labels** when a financial table spans a page boundary. The values are preserved but attached to a continuation fragment instead of the true label. The LLM reconciler detects these "suspect + ghost" row pairs and swaps the labels using GPT-4o-mini (~$0.001 per document).

---

## Project Structure

```
├── main.py                         Pipeline entry point
├── requirements.txt
├── .env.example                    Template for credentials
│
├── extractor/                      Core library
│   ├── __init__.py                 Re-exports public API
│   ├── azure_cu_client.py          Azure CU REST API wrapper
│   ├── statement_detector.py       Statement location + parser
│   └── llm_reconciler.py           LLM post-processing (optional)
│
├── scripts/                        Developer utilities
│   ├── reprocess.py                Re-parse cached output (no API call)
│   └── print_tables.py             Print output JSON as ASCII tables
│
├── docs/
│   └── blueprint.md                Design blueprint for this pipeline
│
└── output/                         Generated — not committed to source control
    ├── raw_result.json             Raw Azure CU response (cached)
    ├── summary.json                Statement location summary
    ├── balance_sheet.json
    ├── cash_flow.json
    └── income_statement.json
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Required for extraction:
```
AZURE_CU_ENDPOINT=https://your-resource.cognitiveservices.azure.com/contentunderstanding/analyzers/prebuilt-read:analyze?api-version=2024-12-01-preview
AZURE_CU_API_KEY=your-key
```

Required only for `--llm` mode:
```
AZURE_OPENAI_ENDPOINT=https://your-aoai.openai.azure.com
AZURE_OPENAI_KEY=your-key
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
```

---

## Usage

### Run the full pipeline

```bash
python main.py path/to/financial_report.pdf
```

### Run with LLM label correction

```bash
python main.py path/to/financial_report.pdf --llm
```

### Re-parse cached output (no Azure CU call)

```bash
python scripts/reprocess.py
python scripts/reprocess.py --llm
```

### Print extracted statements as tables

```bash
python scripts/print_tables.py
```

---

## Output Format

Each statement JSON file contains:

```json
{
  "statement_type": "cash_flow",
  "status": "extracted",
  "start_page": 154,
  "end_page": 155,
  "title_english": "Cash Flow",
  "columns": ["Note  Fiscal Year ended December 31, 2023", "...2024..."],
  "rows": ["Income (Loss) before income tax", "..."],
  "cells": [
    {"row": 1, "col": 0, "content": "Income (Loss) before income tax", "kind": "content"},
    {"row": 1, "col": 1, "content": "(217,741)", "kind": "content"},
    {"row": 1, "col": 2, "content": "16,277", "kind": "content"}
  ]
}
```

`cells` is a flat array of `{row, col, content, kind}` objects — the same structure as Azure Document Intelligence table cells, making it easy to convert to a DataFrame:

```python
import pandas as pd, json
cf = json.load(open("output/cash_flow.json"))
grid = {}
for c in cf["cells"]:
    grid.setdefault(c["row"], {})[c["col"]] = c["content"]
df = pd.DataFrame.from_dict(grid, orient="index").sort_index()
```

---

## Accuracy (Rakuten Group FY2024 annual report, 351 pages)

| Statement | Label accuracy | Value accuracy |
|---|---|---|
| Balance Sheet | ~99% | 100% |
| Income Statement | ~95% | 100% |
| Cash Flow (no LLM) | ~83% | 100% |
| Cash Flow (with LLM) | ~98% | 100% |

All values are correct — errors are label misattribution caused by Azure CU dropping labels at page boundaries, which the LLM reconciler fixes.

---

## How Statement Detection Works

1. **Heading search** — scans the full markdown for IFRS-standard headings in priority order (most specific first). Validates each match by checking that financial numbers appear within 1000 chars.

2. **Cluster selection** — annual reports often repeat statements in summaries. The detector finds the tightest window where all three statement headings co-occur (the main consolidated statements section) and ignores scattered summary tables.

3. **Section boundary** — each statement ends at the next statement's heading, or at a hard-stop terminator (`[Notes to the Consolidated Financial Statements]`, `Consolidated Statement of Changes in Equity`, etc.), or by density-based detection (windows of 3000 chars with fewer than 5 financial numbers).

4. **Token parsing** — splits the section on `\n\n`, separates header tokens (years, "Note", currency) from data tokens, then walks data tokens building rows. Handles continuation fragments (labels split across lines) and orphaned values (values detached from their labels by Azure CU).

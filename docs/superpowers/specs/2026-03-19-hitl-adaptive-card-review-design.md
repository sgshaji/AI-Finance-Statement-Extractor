# HITL Adaptive Card Review — Design Spec

**Date:** 2026-03-19
**Status:** Draft
**Scope:** MVP — no Dataverse, no external persistence

---

## 1. Overview

Add a Human-in-the-Loop (HITL) review step to the Financial Statement Extraction Agent. After extraction, analysts review and correct results via Adaptive Cards in Teams/Copilot Chat before a single Excel write. No intermediate persistence — corrections are tracked as lightweight diffs against the original extraction.

### Goals

- Analysts review extracted data before Excel is generated
- Editable Adaptive Cards for inline corrections
- Confidence scoring flags problematic rows/statements automatically
- Single Excel write after all corrections are applied
- Wizard-style navigation: one statement at a time, submit once at end

### Non-Goals (MVP)

- Dataverse persistence (future enhancement)
- Translation cache from corrections (future)
- Accuracy metrics dashboard (future)
- Margins/ratios extraction

---

## 2. End-to-End Flow

```
1. Analyst uploads PDF in Teams/Copilot Chat
2. Agent submits PDF to Azure Function → CU extraction → JSON result
3. Azure Function computes confidence scores per statement
4. Agent stores:
   - confidence JSON in a topic variable (~2-5KB)
   - statement JSONs stored server-side keyed by job_id (see §2.1)
5. Agent presents Statement Navigator card
6. Wizard flow — one statement per card:
   - Card 1: Balance Sheet → review/edit → [Next →]
   - Card 2: Income Statement → review/edit → [Next →]
   - Card 3: Cash Flow → review/edit → [Submit & Generate Excel]
   - [← Previous] on each card to navigate back (card regenerated from current state)
7. On submit:
   - Agent sends accumulated corrections to /api/apply-corrections
   - Azure Function applies corrections, re-parses values, returns final JSON
   - Single call to Excel generation (Power Automate + Office Script)
   - Excel returned in chat
8. If a statement was "not_found", it's shown as skipped in navigator
```

### 2.1 State Management — Server-Side JSON, Client-Side Diffs

**Problem**: Full v1.2 statement JSONs can exceed 64KB per variable (Copilot Studio limit). A 42-row balance sheet is already ~50-80KB minified.

**Solution**: Keep full JSON server-side, track only corrections in topic variables.

```
Topic variables (lightweight):
  - job_id: string (~36 chars, UUID)
  - confidence: string (~2-5KB, confidence block only)
  - bs_corrections: string (~1-3KB, only changed fields)
  - is_corrections: string (~1-3KB)
  - cf_corrections: string (~1-3KB)
  - current_step: integer (1, 2, or 3)

Server-side (Azure Blob or in-memory cache, keyed by job_id):
  - Full extraction result JSON (balance_sheet, income_statement, cash_flow)
  - TTL: 30 minutes (covers the review session)
```

The existing blob-based job store in `function_app.py` already supports this pattern — extraction results are stored by job_id and deleted after retrieval. For HITL, we extend the TTL and add a `/api/apply-corrections` endpoint that reads the stored JSON, applies the corrections dict, re-parses values, and returns final JSON for Excel generation.

### 2.2 Known Limitations

- **Session timeout**: If the analyst abandons the conversation, the server-side JSON expires after 30 minutes. They must re-upload the PDF. This is acceptable for MVP.
- **Single analyst per job**: No concurrent editing. The job_id is scoped to one conversation.

---

## 3. Confidence Scoring

### Purpose

Drives which rows get flagged and which statements need analyst attention. Replaces raw CU confidence which is misleading after post-processing.

### Per-Statement Composite Score

Weighted average of pass/fail signals. Each signal returns 0.0-1.0, multiplied by its weight. **Score is normalized by total weight** so that adding new signals doesn't break thresholds.

**Formula:**
```
score = sum(signal_score[i] * weight[i]) / sum(weight[i])
```

| Signal | Weight | How it's scored |
|--------|--------|----------------|
| Subtotal validation | 0.20 | Ratio of subtotals that match sum of children (from `validate_totals()`) |
| Section coverage | 0.20 | Ratio of expected sections present (BS needs assets + liabilities + equity) |
| Row count plausible | 0.15 | 1.0 if in range, 0.0 if outside. Ranges: BS 15-80, IS 10-50, CF 15-60 |
| Column year labels | 0.15 | 1.0 if columns contain 4-digit years, 0.0 if not (e.g. "£000") |
| Empty label ratio | 0.15 | 1.0 if blank labels < 5% of rows, 0.0 if above |
| No leaked headers | 0.15 | 1.0 if no data rows contain year/unit strings as values, 0.0 if found |

**Thresholds:**
- **High** (0.85+): analyst steps through with a read-only summary card, editable on request via [Edit] button
- **Medium** (0.60-0.85): flagged rows expanded and editable by default
- **Low** (<0.60): flagged with warning banner, all rows editable

### Per-Row Flagging

A row is flagged when any of these conditions are true:
- `label_raw` is blank
- Subtotal doesn't match sum of children (from validation warnings)
- `section` is "other" (enrichment couldn't classify)
- `normalized` is null but `raw` is non-empty (parse failure)
- Row type appears wrong (label contains "Total" but `row_type` is `line_item`)

`flagged_rows` is computed **once at extraction time** and frozen in the confidence payload. Row indices correspond to `row_index` values on the v1.2 row objects and do not change during the review session.

Flag reason is shown as hint text on the card.

### Configuration

All weights, thresholds, and ranges are configurable constants. **Weights are normalized by sum**, so adding a new signal only requires appending it — existing thresholds remain valid.

```python
CONFIDENCE_WEIGHTS = {
    "subtotal_validation": 0.20,
    "section_coverage": 0.20,
    "row_count": 0.15,
    "column_dates": 0.15,
    "empty_label_ratio": 0.15,
    "leaked_headers": 0.15,
}

CONFIDENCE_THRESHOLDS = {
    "high": 0.85,
    "medium": 0.60,
}

ROW_COUNT_RANGES = {
    "balance_sheet": (15, 80),
    "income_statement": (10, 50),
    "cash_flow": (15, 60),
}

EMPTY_LABEL_MAX_RATIO = 0.05

EXPECTED_SECTIONS = {
    "balance_sheet": ["assets", "current_assets", "non_current_assets",
                      "liabilities", "current_liabilities", "equity"],
    "income_statement": ["revenue", "operating_expenses"],
    "cash_flow": ["operating_activities", "investing_activities",
                  "financing_activities"],
}
```

### Where Confidence Scoring Runs

`confidence_scorer.py` receives the **fully assembled v1.2 statement dict** and inspects it directly. It does not depend on intermediate pipeline stages — it reads the final `rows`, `columns`, and `validation` blocks.

```python
def score_statement(statement_json: dict, statement_type: str) -> dict:
    """
    Input: assembled v1.2 statement dict (with rows, columns, validation)
    Output: { score, level, signals, flagged_rows }
    """
```

Called from `function_app.py:_run_pipeline()` after `assemble_v12_statement()` returns.

### Response Payload

Confidence is returned as a **separate top-level key**, not embedded in the statement JSON. This ensures the statement JSON passed to Excel generation remains a clean v1.2 document.

```json
{
  "summary": [...],
  "balance_sheet": { "...v1.2 schema only..." },
  "income_statement": { "..." },
  "cash_flow": { "..." },
  "confidence": {
    "balance_sheet": {
      "score": 0.718,
      "level": "medium",
      "signals": {
        "subtotal_validation": { "score": 0.67, "detail": "2/3 subtotals pass" },
        "section_coverage": { "score": 0.67, "detail": "missing: equity" },
        "row_count": { "score": 1.0, "detail": "42 rows (range 15-80)" },
        "column_dates": { "score": 1.0, "detail": "2025, 2024" },
        "empty_label_ratio": { "score": 0.0, "detail": "14% empty (threshold 5%)" },
        "leaked_headers": { "score": 1.0, "detail": "none detected" }
      },
      "flagged_rows": [5, 12, 18, 24, 27, 31]
    },
    "income_statement": { "score": 0.91, "level": "high", "..." },
    "cash_flow": { "score": 0.88, "level": "high", "..." }
  }
}
```

The agent topic stores `confidence` in its own topic variable and never passes it to the Power Automate Excel flow.

---

## 4. Adaptive Card Design

### 4.1 Statement Navigator Card

Sent after extraction completes. Entry point for the review wizard.

```
┌─────────────────────────────────────────────┐
│  Extraction Complete                         │
│  Global Reach Partners Ltd | 2025            │
│  Currency: GBP | Unit: thousands             │
│                                              │
│  ● Balance Sheet     ⚠ Medium  (2 issues)   │
│  ● Income Statement  ✓ High                  │
│  ● Cash Flow         ✓ High                  │
│                                              │
│  [Start Review]        [View All Data]       │
└─────────────────────────────────────────────┘
```

- **Start Review** → enters wizard, shows first statement card. All three statements are shown in sequence regardless of confidence level. High-confidence statements show a read-only summary with an [Edit] button to switch to editable mode.
- **View All Data** → sends a **read-only** summary card per statement showing all rows and values (no edit fields). Analyst can review visually without editing. Paginated if > 30 rows per card.
- Statements marked "not_found" shown as greyed out / skipped in wizard (step counter adjusts)

### 4.2 Statement Review Card (one per statement)

```
┌──────────────────────────────────────────────────────┐
│  Balance Sheet Review             ⚠ Medium Confidence │
│  Pages 11-12 | 42 rows | 2 issues flagged             │
├──────────────────────────────────────────────────────┤
│                                                        │
│  ⚠ FLAGGED ITEMS (2)                                   │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Row 5: Missing label                             │  │
│  │ Label:   [____________________________] (edit)   │  │
│  │ 2025:    [(8,432)     ]  2024: [76        ]      │  │
│  │ Section: [Current Assets      ▼]                 │  │
│  │ Type:    [Line Item           ▼]                 │  │
│  ├──────────────────────────────────────────────────┤  │
│  │ Row 12: Subtotal mismatch (expected 6,541)       │  │
│  │ Label:   [Total current assets________] (edit)   │  │
│  │ 2025:    [6,439      ]  2024: [43,178    ]       │  │
│  │ Section: [Current Assets      ▼]                 │  │
│  │ Type:    [Subtotal            ▼]                 │  │
│  └──────────────────────────────────────────────────┘  │
│                                                        │
│  ✓ CLEAN ROWS (40)                   [View/Edit All ▼] │
│                                                        │
│  [← Previous]     Statement 1 of 3        [Next →]    │
│                              [Submit & Generate Excel] │
└──────────────────────────────────────────────────────┘
```

**For high-confidence statements**, the card is the same layout but with text blocks instead of input fields (read-only). An [Edit] button at the top switches to editable mode by sending a new card with input fields.

### 4.3 Editable Fields

Each row exposes these editable inputs:

| Field | Adaptive Card type | Input ID pattern | JSON target |
|-------|-------------------|-----------------|-------------|
| Label | `Input.Text` | `row_{index}_label` | `rows[i].label_raw` |
| Value (per column) | `Input.Text` | `row_{index}_val_{col}` | `rows[i].values[j].raw` |
| Section | `Input.ChoiceSet` | `row_{index}_section` | `rows[i].section` |
| Row Type | `Input.ChoiceSet` | `row_{index}_row_type` | `rows[i].row_type` |

**Section dropdown options:** current_assets, non_current_assets, current_liabilities, non_current_liabilities, equity, revenue, operating_expenses, tax, other

**Row Type dropdown options:** line_item, section_header, subtotal, total

Input fields are pre-populated with `value` property set to the current extraction value (or the last correction if navigating back).

### 4.4 Card Size Management

Adaptive Cards have a ~28KB limit.

- **Flagged rows**: always expanded and editable (typically 1-10 rows, ~5-8KB)
- **Clean rows**: collapsed by default behind "View/Edit All"
- **Pagination for clean rows**: if > 15 clean rows, send a **new card message** per page of 15 rows. The topic tracks current page in a variable. Each page card has [More Rows →] and [Back to Review].
- `Action.ShowCard` is NOT used for pagination — nested cards count toward the parent's 28KB budget and don't solve the size problem.

### 4.5 Back Navigation — Card Regeneration

When analyst clicks [← Previous], the topic re-sends the previous statement's card as a **new message** with all input fields pre-populated from the current correction state.

The topic calls `/api/build-review-card` (or constructs the card inline) with:
- The original statement data (from server-side cache via job_id)
- The corrections dict for that statement (from topic variable)
- These are merged to produce `value` properties on each input field

This ensures the analyst sees their previous corrections preserved when navigating back.

---

## 5. Edit-to-JSON Mapping

### Card Submission Payload

When analyst clicks Next or Submit, Adaptive Card returns a flat key-value object containing **only the fields that were rendered** (flagged rows + any expanded clean rows):

```json
{
  "row_5_label": "Trade debtors",
  "row_5_val_0": "(8,432)",
  "row_5_val_1": "76",
  "row_5_section": "current_assets",
  "row_5_row_type": "line_item",
  "row_12_label": "Total current assets",
  "row_12_val_0": "6,541",
  "row_12_val_1": "43,178",
  "row_12_section": "current_assets",
  "row_12_row_type": "subtotal"
}
```

### Processing in Agent Topic

1. Receive flat payload from Adaptive Card action
2. Diff against original values — only store fields that actually changed
3. Merge into the statement's corrections variable (e.g. `bs_corrections`)
4. On [Next →]: store corrections, advance `current_step`, send next card
5. On [← Previous]: store corrections, decrement `current_step`, regenerate previous card
6. On [Submit]: send all corrections to `/api/apply-corrections`

### Server-Side Correction Application (`/api/apply-corrections`)

```
POST /api/apply-corrections
{
  "job_id": "abc-123",
  "corrections": {
    "balance_sheet": {
      "row_5": { "label": "Trade debtors", "section": "current_assets" },
      "row_12": { "val_0": "6,541" }
    },
    "income_statement": {},
    "cash_flow": { "row_3": { "val_1": "(2,100)" } }
  }
}
```

The endpoint:
1. Loads the original extraction JSON from blob storage (by job_id)
2. For each correction, updates the target field in the statement JSON
3. Re-parses corrected value strings using `_parse_financial_value()`:
   - `"6,541"` → `{ raw: "6,541", normalized: 6541.0, is_null: false, is_zero: false }`
   - `"(3,200)"` → `{ raw: "(3,200)", normalized: -3200.0, is_null: false, is_zero: false }`
   - `"-"` → `{ raw: "-", normalized: 0.0, is_null: false, is_zero: true }` (matches existing `_parse_financial_value` behavior where dashes = zero)
   - `""` → `{ raw: null, normalized: null, is_null: true }`
4. Recomputes `canonical_group` from corrected `section` using `_SECTION_TO_GROUP` mapping (see Appendix A)
5. Returns the corrected statement JSONs (clean v1.2, no confidence block)
6. Deletes the server-side cached JSON (one-time use)

**Error handling**: If a value string can't be parsed, the endpoint returns a 200 with a `parse_errors` array listing the row/column. The topic shows an error message and re-sends the card for that statement.

---

## 6. Component Inventory

### New Components

| Component | Type | Purpose |
|-----------|------|---------|
| `confidence_scorer.py` | Python module in `service/extractor/` | Computes per-statement composite confidence + row flags. Receives assembled v1.2 statement dict, inspects rows/columns/validation directly. |
| Navigator Card template | Adaptive Card JSON | Statement overview with Start Review / View All |
| Statement Review Card template | Adaptive Card JSON | Per-statement editable review card (flagged + clean sections) |
| HITL review topic | Copilot Studio topic YAML | Orchestrates wizard flow: navigator → cards → submit → Excel |
| `/api/apply-corrections` endpoint | Azure Function route | Applies correction diffs to cached extraction JSON, re-parses values, returns clean v1.2 |

### Modified Components

| Component | Change |
|-----------|--------|
| `function_app.py` | Add confidence scoring to extraction response. Extend job TTL for HITL sessions. Add `/api/apply-corrections` route. |
| `FinancialsExtractor` action definition | Add `confidence` output property to action schema |
| FileUploadHandler topic | Capture `confidence` output, route to new HITL review topic instead of directly to Excel generation |
| Power Automate flow | No schema changes — receives the same statement JSON strings as today, but now they include analyst corrections |

### Unchanged Components

- `extractor/` modules (statement_detector, html_table_parser, enrichment, llm_reconciler, schema_mapper)
- `cu_client.py` / `azure_cu_client.py`
- `CreateFinancialReport.ts` (Office Script — receives JSON, doesn't care if it's corrected or not)
- CU custom analyzers

---

## 7. Data Flow Diagram

```
Analyst uploads PDF
        │
        ▼
  Copilot Studio Agent
  (FileUploadHandler topic)
        │
        ▼
  Azure Function: POST /api/extract
        │
        ├── CU Locator (find statements)
        ├── CU Extractor (enrichment)
        ├── HTML table parser
        ├── Schema mapper (v1.2 JSON)
        ├── Confidence scorer (NEW)
        └── Cache result in blob (job_id, TTL 30min)
        │
        ▼
  Response: confidence + job_id (statement JSONs stay server-side)
        │
        ▼
  Topic variables: job_id, confidence, current_step, *_corrections
        │
        ▼
  Navigator Card
  [Start Review] or [View All]
        │
        ▼
  Statement Card 1 (BS) ←── edits ──┐
  [Next →]                           │
        │                            │
        ▼                            │
  Store corrections diff ◄───────────┘
  (bs_corrections topic variable)
        │
        ▼
  Statement Card 2 (IS) → edits → store is_corrections
        │
        ▼
  Statement Card 3 (CF) → edits → store cf_corrections
        │
        ▼
  [Submit & Generate Excel]
        │
        ▼
  POST /api/apply-corrections (job_id + all corrections)
        │
        ▼
  Azure Function: load cached JSON, apply diffs, re-parse values
        │
        ▼
  Return corrected v1.2 JSONs (clean, no confidence block)
        │
        ▼
  Power Automate: corrected JSON → Office Script → Excel
        │
        ▼
  Excel file returned in chat
```

---

## 8. Future Enhancements (Post-MVP)

| Enhancement | Benefit |
|-------------|---------|
| Dataverse persistence | Audit trail, accuracy metrics, correction history |
| Translation cache from corrections | Pipeline improves with every analyst review |
| Accuracy dashboard | Track precision/recall over time, identify failure patterns |
| Confidence weight tuning from correction data | Auto-optimize which signals matter most |
| Margins calculation | Derive ratios from extracted IS data |
| Bulk processing | Queue multiple PDFs, review in batch |

---

## Appendix A: Section to Canonical Group Mapping

Used when analyst changes a row's `section` dropdown — `canonical_group` is recomputed.

| Section | Canonical Group |
|---------|----------------|
| current_assets | assets |
| non_current_assets | assets |
| current_liabilities | liabilities |
| non_current_liabilities | liabilities |
| equity | equity |
| revenue | revenue |
| operating_expenses | expenses |
| tax | tax |
| other | other |

Reference: `enrichment.py:_SECTION_TO_GROUP`

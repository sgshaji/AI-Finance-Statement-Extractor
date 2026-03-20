# HITL Adaptive Card Review — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Human-in-the-Loop review via editable Adaptive Cards so analysts can correct extraction results before Excel is generated.

**Architecture:** Confidence scorer runs server-side after extraction, flags problematic rows. Copilot Studio topic presents wizard-style Adaptive Cards (one per statement). Corrections stored as lightweight diffs in topic variables, applied server-side via `/api/apply-corrections` endpoint. Single Excel write at end.

**Tech Stack:** Python (Azure Function), Copilot Studio YAML topics, Adaptive Card JSON templates, Power Automate (existing flow)

**Spec:** `docs/superpowers/specs/2026-03-19-hitl-adaptive-card-review-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `service/extractor/confidence_scorer.py` | Composite confidence scoring + row flagging |
| `service/extractor/corrections.py` | Apply analyst corrections to v1.2 statement dicts |
| `service/tests/test_confidence_scorer.py` | Tests for all scoring signals and edge cases |
| `service/tests/test_corrections.py` | Tests for correction application logic |
| `agent/topics/HITLReview.mcs.yml` | Wizard flow topic: navigator → statement cards → submit |

### Modified Files

| File | Change |
|------|--------|
| `service/function_app.py` | Add confidence to pipeline output. Add `/api/apply-corrections` route. Extend job TTL. Fix health route `_jobs` NameError. |
| `service/extractor/__init__.py` | Re-export confidence scorer |
| `agent/topics/FileUploadHandler.mcs.yml` | Route to HITLReview topic instead of direct Excel generation |
| `agent/actions/FinancialsExtractor-ExtractfinancialstatementsfromaPDFdoc.mcs.yml` | Add `confidence` output property |

### Unchanged Files

- `service/extractor/statement_detector.py`
- `service/extractor/html_table_parser.py`
- `service/extractor/enrichment.py`
- `service/extractor/schema_mapper.py`
- `service/extractor/llm_reconciler.py`
- `report-generator/CreateFinancialReport.ts`

---

## Task 1: Confidence Scorer — Core Scoring Logic

**Files:**
- Create: `service/extractor/confidence_scorer.py`
- Create: `service/tests/test_confidence_scorer.py`

### Step 1.1: Write failing tests for individual signals

- [ ] Create `service/tests/test_confidence_scorer.py` with tests for each of the 6 signals.

```python
"""Tests for confidence_scorer module."""
import pytest
from extractor.confidence_scorer import (
    score_statement,
    _score_subtotal_validation,
    _score_section_coverage,
    _score_row_count,
    _score_column_dates,
    _score_empty_label_ratio,
    _score_leaked_headers,
    flag_rows,
)


# ---------------------------------------------------------------------------
# Signal: subtotal_validation
# ---------------------------------------------------------------------------

class TestSubtotalValidation:
    def test_all_subtotals_pass(self):
        """Statement where all subtotals match their children sums."""
        stmt = _make_statement(rows=[
            _row(0, "Revenue", "line_item", 1, vals=["100"]),
            _row(1, "Cost of sales", "line_item", 1, vals=["(60)"]),
            _row(2, "Total expenses", "subtotal", 0, vals=["(60)"]),
        ], validation_warnings=[])
        assert _score_subtotal_validation(stmt) == 1.0

    def test_one_of_two_subtotals_fail(self):
        stmt = _make_statement(rows=[
            _row(0, "A", "subtotal", 0, vals=["100"]),
            _row(1, "B", "subtotal", 0, vals=["200"]),
        ], validation_warnings=[
            {"row_index": 1, "column_index": 0, "difference": 50}  # one fails
        ])
        assert _score_subtotal_validation(stmt) == 0.5

    def test_no_subtotals(self):
        """No subtotals to validate → perfect score (nothing wrong)."""
        stmt = _make_statement(rows=[
            _row(0, "Cash", "line_item", 0, vals=["100"]),
        ], validation_warnings=[])
        assert _score_subtotal_validation(stmt) == 1.0


# ---------------------------------------------------------------------------
# Signal: section_coverage
# ---------------------------------------------------------------------------

class TestSectionCoverage:
    def test_balance_sheet_all_sections(self):
        stmt = _make_statement(rows=[
            _row(0, "Cash", "line_item", 0, section="current_assets"),
            _row(1, "PPE", "line_item", 0, section="non_current_assets"),
            _row(2, "Payables", "line_item", 0, section="current_liabilities"),
            _row(3, "Shares", "line_item", 0, section="equity"),
        ])
        assert _score_section_coverage(stmt, "balance_sheet") == 1.0

    def test_balance_sheet_missing_equity(self):
        stmt = _make_statement(rows=[
            _row(0, "Cash", "line_item", 0, section="current_assets"),
            _row(1, "Payables", "line_item", 0, section="current_liabilities"),
        ])
        # Has assets + liabilities but missing equity → 2/3
        score = _score_section_coverage(stmt, "balance_sheet")
        assert 0.6 < score < 0.7


# ---------------------------------------------------------------------------
# Signal: row_count
# ---------------------------------------------------------------------------

class TestRowCount:
    def test_in_range(self):
        stmt = _make_statement(rows=[_row(i, f"R{i}", "line_item", 0) for i in range(30)])
        assert _score_row_count(stmt, "balance_sheet") == 1.0

    def test_too_few(self):
        stmt = _make_statement(rows=[_row(i, f"R{i}", "line_item", 0) for i in range(5)])
        assert _score_row_count(stmt, "balance_sheet") == 0.0

    def test_too_many(self):
        stmt = _make_statement(rows=[_row(i, f"R{i}", "line_item", 0) for i in range(100)])
        assert _score_row_count(stmt, "balance_sheet") == 0.0


# ---------------------------------------------------------------------------
# Signal: column_dates
# ---------------------------------------------------------------------------

class TestColumnDates:
    def test_year_columns(self):
        stmt = _make_statement(columns=[
            {"column_index": 0, "label": "2025"},
            {"column_index": 1, "label": "2024"},
        ])
        assert _score_column_dates(stmt) == 1.0

    def test_unit_only_columns(self):
        stmt = _make_statement(columns=[
            {"column_index": 0, "label": "\u00a3000"},
            {"column_index": 1, "label": "\u00a3000"},
        ])
        assert _score_column_dates(stmt) == 0.0

    def test_mixed(self):
        stmt = _make_statement(columns=[
            {"column_index": 0, "label": "2025"},
            {"column_index": 1, "label": "\u00a3000"},
        ])
        assert _score_column_dates(stmt) == 0.5


# ---------------------------------------------------------------------------
# Signal: empty_label_ratio
# ---------------------------------------------------------------------------

class TestEmptyLabelRatio:
    def test_no_empty_labels(self):
        stmt = _make_statement(rows=[_row(i, f"R{i}", "line_item", 0) for i in range(20)])
        assert _score_empty_label_ratio(stmt) == 1.0

    def test_above_threshold(self):
        rows = [_row(i, f"R{i}", "line_item", 0) for i in range(17)]
        rows += [_row(i + 17, "", "line_item", 0) for i in range(3)]  # 15%
        stmt = _make_statement(rows=rows)
        assert _score_empty_label_ratio(stmt) == 0.0


# ---------------------------------------------------------------------------
# Signal: leaked_headers
# ---------------------------------------------------------------------------

class TestLeakedHeaders:
    def test_no_leaks(self):
        stmt = _make_statement(rows=[
            _row(0, "Cash", "line_item", 0, vals=["1,000", "2,000"]),
        ])
        assert _score_leaked_headers(stmt) == 1.0

    def test_year_in_values(self):
        stmt = _make_statement(rows=[
            _row(0, "", "line_item", 0, vals=["2025", "2024"]),
        ])
        assert _score_leaked_headers(stmt) == 0.0


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

class TestScoreStatement:
    def test_perfect_statement(self):
        stmt = _make_statement(
            rows=[
                _row(0, "Cash", "line_item", 1, section="current_assets", vals=["100"]),
                _row(1, "Total", "subtotal", 0, section="current_assets", vals=["100"]),
                _row(2, "Debt", "line_item", 1, section="current_liabilities", vals=["50"]),
                _row(3, "Equity", "line_item", 0, section="equity", vals=["50"]),
            ],
            columns=[{"column_index": 0, "label": "2025"}],
            validation_warnings=[],
        )
        result = score_statement(stmt, "balance_sheet")
        assert result["level"] == "high"
        assert result["score"] >= 0.85

    def test_low_confidence(self):
        stmt = _make_statement(
            rows=[_row(i, "", "line_item", 0, vals=["2025"]) for i in range(100)],
            columns=[{"column_index": 0, "label": "\u00a3000"}],
            validation_warnings=[{"row_index": 0, "column_index": 0, "difference": 999}],
        )
        result = score_statement(stmt, "balance_sheet")
        assert result["level"] == "low"
        assert result["score"] < 0.60


# ---------------------------------------------------------------------------
# Row flagging
# ---------------------------------------------------------------------------

class TestFlagRows:
    def test_blank_label_flagged(self):
        stmt = _make_statement(rows=[
            _row(0, "", "line_item", 0, vals=["100"]),
            _row(1, "Cash", "line_item", 0, vals=["200"]),
        ])
        flags = flag_rows(stmt)
        assert 0 in flags
        assert 1 not in flags

    def test_other_section_flagged(self):
        stmt = _make_statement(rows=[
            _row(0, "Mystery", "line_item", 0, section="other", vals=["100"]),
        ])
        flags = flag_rows(stmt)
        assert 0 in flags

    def test_parse_failure_flagged(self):
        stmt = _make_statement(rows=[
            _row(0, "Cash", "line_item", 0, vals=["abc"]),
        ])
        flags = flag_rows(stmt)
        assert 0 in flags


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _row(index, label, row_type, indent, section="current_assets", vals=None):
    values = []
    for i, v in enumerate(vals or []):
        from extractor.statement_detector import _parse_financial_value
        parsed = _parse_financial_value(v)
        values.append({
            "column_index": i,
            "raw": v,
            "normalized": parsed,
            "is_null": v is None or v == "",
            "is_zero": parsed == 0.0 if parsed is not None else None,
            "value_kind": "currency",
            "confidence": None,
        })
    return {
        "row_index": index,
        "label_raw": label,
        "label_normalized": label if label else None,
        "label_language": "en",
        "canonical_key": label.lower().replace(" ", "_") if label else "unknown",
        "canonical_group": "assets",
        "row_type": row_type,
        "indent_level": indent,
        "section": section,
        "parent_canonical_key": None,
        "sign_hint": None,
        "is_derived_total": row_type in ("subtotal", "total"),
        "is_required_anchor": False,
        "source_page": None,
        "source_bbox": None,
        "values": values,
    }


def _make_statement(rows=None, columns=None, validation_warnings=None):
    return {
        "schema_version": "1.2.0",
        "document_metadata": {"source_file_name": "test.pdf"},
        "statement_metadata": {"statement_type": "balance_sheet"},
        "columns": columns or [{"column_index": 0, "label": "2025"}],
        "rows": rows or [],
        "validation": {
            "status": "passed",
            "warnings": validation_warnings or [],
            "errors": [],
        },
    }
```

- [ ] Run tests to verify they fail:

```bash
cd service && python -m pytest tests/test_confidence_scorer.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'extractor.confidence_scorer'`

### Step 1.2: Implement confidence_scorer.py

- [ ] Create `service/extractor/confidence_scorer.py`:

```python
"""
extractor/confidence_scorer.py
-------------------------------
Computes per-statement composite confidence scores and flags problematic rows.

Receives a fully assembled v1.2 statement dict and inspects rows, columns,
and validation results to produce a confidence score (0.0-1.0) with a
human-readable level (high/medium/low) and a list of flagged row indices.
"""

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Configuration — all weights, thresholds, and ranges are tunable
# ---------------------------------------------------------------------------

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
    "balance_sheet": [
        {"assets", "current_assets", "non_current_assets"},
        {"liabilities", "current_liabilities", "non_current_liabilities"},
        {"equity"},
    ],
    "income_statement": [
        {"revenue"},
        {"operating_expenses", "expenses"},
    ],
    "cash_flow": [
        {"operating_activities"},
        {"investing_activities"},
        {"financing_activities"},
    ],
}


# ---------------------------------------------------------------------------
# Individual scoring signals
# ---------------------------------------------------------------------------

def _score_subtotal_validation(stmt: dict) -> float:
    """Ratio of subtotals that pass cross-validation."""
    rows = stmt.get("rows", [])
    warnings = stmt.get("validation", {}).get("warnings", [])
    subtotal_count = sum(1 for r in rows if r.get("row_type") == "subtotal")
    if subtotal_count == 0:
        return 1.0
    failed_rows = {w.get("row_index") for w in warnings}
    failed_subtotals = sum(
        1 for r in rows
        if r.get("row_type") == "subtotal" and r.get("row_index") in failed_rows
    )
    return (subtotal_count - failed_subtotals) / subtotal_count


def _score_section_coverage(stmt: dict, statement_type: str) -> float:
    """Ratio of expected section groups present."""
    expected_groups = EXPECTED_SECTIONS.get(statement_type, [])
    if not expected_groups:
        return 1.0
    present_sections = {r.get("section", "") for r in stmt.get("rows", [])}
    matched = sum(
        1 for group in expected_groups
        if group & present_sections
    )
    return matched / len(expected_groups)


def _score_row_count(stmt: dict, statement_type: str) -> float:
    """1.0 if row count is in plausible range, 0.0 otherwise."""
    count = len(stmt.get("rows", []))
    lo, hi = ROW_COUNT_RANGES.get(statement_type, (10, 80))
    return 1.0 if lo <= count <= hi else 0.0


def _score_column_dates(stmt: dict) -> float:
    """Ratio of columns that contain a 4-digit year label."""
    columns = stmt.get("columns", [])
    if not columns:
        return 0.0
    year_re = re.compile(r"\b20\d{2}\b")
    matches = sum(1 for c in columns if year_re.search(c.get("label", "")))
    return matches / len(columns)


def _score_empty_label_ratio(stmt: dict) -> float:
    """1.0 if blank labels are below threshold, 0.0 if above."""
    rows = stmt.get("rows", [])
    if not rows:
        return 1.0
    empty = sum(1 for r in rows if not r.get("label_raw", "").strip())
    ratio = empty / len(rows)
    return 1.0 if ratio <= EMPTY_LABEL_MAX_RATIO else 0.0


def _score_leaked_headers(stmt: dict) -> float:
    """1.0 if no data rows contain year/unit strings as values, 0.0 if found."""
    year_re = re.compile(r"^\d{4}$")
    unit_re = re.compile(r"^[\$\u00a3\u20ac\u00a5]\s*['\u2019]?\s*\d*$", re.IGNORECASE)
    rows = stmt.get("rows", [])
    if not rows:
        return 1.0
    leaked = 0
    for r in rows:
        values = r.get("values", [])
        if not r.get("label_raw", "").strip() and values:
            all_header_like = all(
                year_re.match(str(v.get("raw", "") or "").strip())
                or unit_re.match(str(v.get("raw", "") or "").strip())
                or not str(v.get("raw", "") or "").strip()
                for v in values
            ) and any(str(v.get("raw", "") or "").strip() for v in values)
            if all_header_like:
                leaked += 1
    return 0.0 if leaked > 0 else 1.0


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------

def score_statement(stmt: dict, statement_type: str) -> dict:
    """
    Compute composite confidence score for a single statement.

    Args:
        stmt: Fully assembled v1.2 statement dict with rows, columns, validation
        statement_type: "balance_sheet", "income_statement", or "cash_flow"

    Returns:
        {
            "score": float (0.0-1.0),
            "level": "high" | "medium" | "low",
            "signals": { signal_name: { "score": float, "detail": str } },
            "flagged_rows": [int]
        }
    """
    signal_funcs = {
        "subtotal_validation": lambda: _score_subtotal_validation(stmt),
        "section_coverage": lambda: _score_section_coverage(stmt, statement_type),
        "row_count": lambda: _score_row_count(stmt, statement_type),
        "column_dates": lambda: _score_column_dates(stmt),
        "empty_label_ratio": lambda: _score_empty_label_ratio(stmt),
        "leaked_headers": lambda: _score_leaked_headers(stmt),
    }

    signals = {}
    total_weighted = 0.0
    total_weight = 0.0

    for name, func in signal_funcs.items():
        weight = CONFIDENCE_WEIGHTS.get(name, 0.0)
        score = func()
        signals[name] = {"score": round(score, 2), "detail": ""}
        total_weighted += score * weight
        total_weight += weight

    composite = total_weighted / total_weight if total_weight > 0 else 0.0
    composite = round(composite, 3)

    if composite >= CONFIDENCE_THRESHOLDS["high"]:
        level = "high"
    elif composite >= CONFIDENCE_THRESHOLDS["medium"]:
        level = "medium"
    else:
        level = "low"

    flagged = flag_rows(stmt)

    return {
        "score": composite,
        "level": level,
        "signals": signals,
        "flagged_rows": sorted(flagged),
    }


# ---------------------------------------------------------------------------
# Row flagging
# ---------------------------------------------------------------------------

def flag_rows(stmt: dict) -> set[int]:
    """
    Return set of row_index values for rows that need analyst review.

    A row is flagged when:
    - label_raw is blank
    - Subtotal doesn't match sum of children (from validation warnings)
    - section is "other"
    - normalized is null but raw is non-empty (parse failure)
    - Row type appears wrong (label contains "Total" but row_type is line_item)
    """
    flagged = set()
    validation_warnings = stmt.get("validation", {}).get("warnings", [])
    warned_rows = {w.get("row_index") for w in validation_warnings}

    for row in stmt.get("rows", []):
        idx = row.get("row_index")

        # Blank label
        if not row.get("label_raw", "").strip():
            flagged.add(idx)
            continue

        # Subtotal mismatch
        if idx in warned_rows:
            flagged.add(idx)
            continue

        # Unclassified section
        if row.get("section") == "other":
            flagged.add(idx)
            continue

        # Parse failure
        for val in row.get("values", []):
            raw = val.get("raw")
            if raw and str(raw).strip() and val.get("normalized") is None:
                flagged.add(idx)
                break

        # Row type mismatch
        label = row.get("label_raw", "").lower()
        if "total" in label and row.get("row_type") == "line_item":
            flagged.add(idx)

    return flagged
```

- [ ] Run tests:

```bash
cd service && python -m pytest tests/test_confidence_scorer.py -v
```

Expected: All tests PASS.

- [ ] Commit:

```bash
git add service/extractor/confidence_scorer.py service/tests/test_confidence_scorer.py
git commit -m "feat: add confidence scorer with 6 signals and row flagging"
```

---

## Task 2: Integrate Confidence Scorer into Pipeline

**Files:**
- Modify: `service/function_app.py` (lines 313-439: `_run_pipeline`, lines 72-88: job store, lines 241-287: status route)
- Modify: `service/extractor/__init__.py`

### Step 2.1: Export scorer from extractor package

- [ ] Add to `service/extractor/__init__.py`:

```python
from .confidence_scorer import score_statement, flag_rows
```

And update `__all__` to include `"score_statement"`, `"flag_rows"`.

- [ ] Commit:

```bash
git add service/extractor/__init__.py
git commit -m "feat: export confidence scorer from extractor package"
```

### Step 2.2: Add confidence scoring to _run_pipeline

- [ ] In `service/function_app.py`, after the statement assembly loop (around line 431), add confidence scoring:

Find the line `return output` at the end of `_run_pipeline` and add before it:

```python
    # ------------------------------------------------------------------ #
    # Step 5: Confidence scoring
    # ------------------------------------------------------------------ #
    from extractor.confidence_scorer import score_statement
    confidence = {}
    for stype in STATEMENT_TYPES:
        stmt = output.get(stype)
        if stmt and isinstance(stmt, dict) and stmt.get("rows"):
            confidence[stype] = score_statement(stmt, stype)
        else:
            confidence[stype] = {
                "score": 0.0, "level": "low",
                "signals": {}, "flagged_rows": [],
            }
    output["confidence"] = confidence
```

- [ ] Verify by running existing tests (nothing should break):

```bash
cd service && python -m pytest tests/ -v
```

Expected: All 84 existing tests + new confidence tests PASS.

- [ ] Commit:

```bash
git add service/function_app.py
git commit -m "feat: integrate confidence scoring into extraction pipeline"
```

### Step 2.3: Extend job TTL and separate confidence from statement JSON in status response

- [ ] In `service/function_app.py`, modify `_build_response_payload` (around line 91) to include confidence as a separate key and ensure it's NOT embedded in statement JSONs:

```python
def _build_response_payload(result: dict) -> dict:
    """Build the stringified response payload from pipeline result."""
    company_name = None
    for stype in ["balance_sheet", "income_statement", "cash_flow"]:
        stmt = result.get(stype)
        if stmt and isinstance(stmt, dict):
            company_name = stmt.get("document_metadata", {}).get("company_name")
            if company_name:
                break
    return {
        "company_name": company_name or "Unknown_Company",
        "summary": json.dumps(result.get("summary", []), ensure_ascii=False),
        "balance_sheet": json.dumps(result.get("balance_sheet"), ensure_ascii=False),
        "income_statement": json.dumps(result.get("income_statement"), ensure_ascii=False),
        "cash_flow": json.dumps(result.get("cash_flow"), ensure_ascii=False),
        "confidence": json.dumps(result.get("confidence", {}), ensure_ascii=False),
    }
```

- [ ] Modify the `extract_status` route's "completed" branch to include confidence in the response.

- [ ] Commit:

```bash
git add service/function_app.py
git commit -m "feat: include confidence in extraction response, separate from statement JSON"
```

---

## Task 3: Corrections Module + Endpoint

**Files:**
- Create: `service/extractor/corrections.py`
- Create: `service/tests/test_corrections.py`
- Modify: `service/function_app.py` (add route only)

### Step 3.1: Write failing tests for correction application

- [ ] Create `service/tests/test_corrections.py`:

```python
"""Tests for the correction application logic."""
import json
import pytest
from extractor.corrections import apply_corrections


def test_apply_label_correction():
    """Correcting a label updates label_raw."""
    stmt = {
        "rows": [
            {"row_index": 0, "label_raw": "", "values": [{"raw": "100", "normalized": 100.0}], "section": "assets"},
            {"row_index": 1, "label_raw": "Cash", "values": [{"raw": "200", "normalized": 200.0}], "section": "current_assets"},
        ]
    }
    corrections = {"row_0": {"label": "Trade debtors"}}
    result = apply_corrections(stmt, corrections)
    assert result["rows"][0]["label_raw"] == "Trade debtors"
    assert result["rows"][1]["label_raw"] == "Cash"  # unchanged


def test_apply_value_correction_reparses():
    """Correcting a value re-parses the normalized float."""
    stmt = {
        "rows": [
            {"row_index": 0, "label_raw": "Cash", "values": [
                {"raw": "100", "normalized": 100.0, "is_null": False, "is_zero": False, "column_index": 0}
            ], "section": "assets"},
        ]
    }
    corrections = {"row_0": {"val_0": "(3,200)"}}
    result = apply_corrections(stmt, corrections)
    assert result["rows"][0]["values"][0]["raw"] == "(3,200)"
    assert result["rows"][0]["values"][0]["normalized"] == -3200.0
    assert result["rows"][0]["values"][0]["is_zero"] == False


def test_apply_section_correction_updates_group():
    """Correcting section recomputes canonical_group."""
    stmt = {
        "rows": [
            {"row_index": 0, "label_raw": "Creditors", "values": [], "section": "assets", "canonical_group": "assets"},
        ]
    }
    corrections = {"row_0": {"section": "current_liabilities"}}
    result = apply_corrections(stmt, corrections)
    assert result["rows"][0]["section"] == "current_liabilities"
    assert result["rows"][0]["canonical_group"] == "liabilities"


def test_apply_empty_value_sets_null():
    """Empty string correction → is_null=True."""
    stmt = {
        "rows": [
            {"row_index": 0, "label_raw": "X", "values": [
                {"raw": "100", "normalized": 100.0, "is_null": False, "is_zero": False, "column_index": 0}
            ], "section": "assets"},
        ]
    }
    corrections = {"row_0": {"val_0": ""}}
    result = apply_corrections(stmt, corrections)
    assert result["rows"][0]["values"][0]["is_null"] == True
    assert result["rows"][0]["values"][0]["normalized"] is None


def test_apply_dash_value_sets_zero():
    """Dash correction → normalized=0.0, is_zero=True."""
    stmt = {
        "rows": [
            {"row_index": 0, "label_raw": "X", "values": [
                {"raw": "100", "normalized": 100.0, "is_null": False, "is_zero": False, "column_index": 0}
            ], "section": "assets"},
        ]
    }
    corrections = {"row_0": {"val_0": "-"}}
    result = apply_corrections(stmt, corrections)
    assert result["rows"][0]["values"][0]["normalized"] == 0.0
    assert result["rows"][0]["values"][0]["is_zero"] == True
    assert result["rows"][0]["values"][0]["is_null"] == False
```

- [ ] Run to verify failure:

```bash
cd service && python -m pytest tests/test_corrections.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'extractor.corrections'`

### Step 3.2: Implement corrections module

- [ ] Create `service/extractor/corrections.py` (decoupled from Azure Functions runtime for testability):

```python
"""
extractor/corrections.py
-------------------------
Applies analyst corrections from HITL Adaptive Cards to v1.2 statement dicts.
"""

def apply_corrections(statement: dict, corrections: dict) -> dict:
    """
    Apply analyst corrections to a v1.2 statement dict.

    corrections format: {"row_0": {"label": "...", "val_0": "...", "section": "..."}, ...}
    """
    from extractor.statement_detector import _parse_financial_value
    from extractor.enrichment import _SECTION_TO_GROUP

    rows = statement.get("rows", [])
    row_by_index = {r["row_index"]: r for r in rows}

    for key, fields in corrections.items():
        # Parse "row_N" → integer index
        if not key.startswith("row_"):
            continue
        try:
            row_idx = int(key.split("_", 1)[1])
        except (ValueError, IndexError):
            continue

        row = row_by_index.get(row_idx)
        if row is None:
            continue

        # Apply label correction
        if "label" in fields:
            row["label_raw"] = fields["label"]

        # Apply value corrections
        for fkey, fval in fields.items():
            if fkey.startswith("val_"):
                try:
                    col_idx = int(fkey.split("_", 1)[1])
                except (ValueError, IndexError):
                    continue
                if col_idx < len(row.get("values", [])):
                    val_cell = row["values"][col_idx]
                    raw = fval.strip() if fval else ""
                    if not raw:
                        val_cell["raw"] = None
                        val_cell["normalized"] = None
                        val_cell["is_null"] = True
                        val_cell["is_zero"] = None
                    else:
                        parsed = _parse_financial_value(raw)
                        val_cell["raw"] = raw
                        val_cell["normalized"] = parsed
                        val_cell["is_null"] = False
                        val_cell["is_zero"] = parsed == 0.0 if parsed is not None else None

        # Apply section correction
        if "section" in fields:
            row["section"] = fields["section"]
            row["canonical_group"] = _SECTION_TO_GROUP.get(fields["section"], "other")

        # Apply row_type correction
        if "row_type" in fields:
            row["row_type"] = fields["row_type"]
            row["is_derived_total"] = fields["row_type"] in ("subtotal", "total")

    return statement
```

- [ ] Run tests:

```bash
cd service && python -m pytest tests/test_corrections.py -v
```

Expected: All PASS.

- [ ] Commit:

```bash
git add service/extractor/corrections.py service/tests/test_corrections.py
git commit -m "feat: add corrections module for HITL correction application"
```

### Step 3.3: Add /api/apply-corrections HTTP route

- [ ] In `service/function_app.py`, add the route:

```python
@app.route(route="apply-corrections", methods=["POST"])
def apply_corrections(req: func.HttpRequest) -> func.HttpResponse:
    """
    Apply analyst corrections to a cached extraction result.

    Body: {"job_id": "...", "corrections": {"balance_sheet": {...}, ...}}
    Returns: corrected statement JSONs (clean v1.2, no confidence).
    """
    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400, mimetype="application/json",
        )

    job_id = body.get("job_id")
    corrections = body.get("corrections", {})

    if not job_id:
        return func.HttpResponse(
            json.dumps({"error": "Missing job_id"}),
            status_code=400, mimetype="application/json",
        )

    # Load cached extraction result
    job = _load_job(job_id)
    if not job or job.get("status") != "completed":
        return func.HttpResponse(
            json.dumps({"error": f"Job {job_id} not found or not completed"}),
            status_code=404, mimetype="application/json",
        )

    result = job.get("result", {})
    parse_errors = []

    # Apply corrections per statement
    from extractor.corrections import apply_corrections as _apply
    for stype in STATEMENT_TYPES:
        stmt_json = result.get(stype)
        stmt_corrections = corrections.get(stype, {})
        if stmt_json and stmt_corrections:
            # Parse the statement JSON string if it's still stringified
            if isinstance(stmt_json, str):
                stmt_json = json.loads(stmt_json)
            stmt_json = _apply(stmt_json, stmt_corrections)
            result[stype] = json.dumps(stmt_json, ensure_ascii=False)

    # Build final payload (same format as extract_status completed response)
    payload = {
        "status": "corrected",
        "company_name": result.get("company_name", "Unknown_Company"),
        "summary": result.get("summary", "[]"),
        "balance_sheet": result.get("balance_sheet", "null"),
        "income_statement": result.get("income_statement", "null"),
        "cash_flow": result.get("cash_flow", "null"),
    }

    if parse_errors:
        payload["parse_errors"] = parse_errors

    # Clean up cached job
    _delete_job(job_id)

    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        status_code=200, mimetype="application/json",
    )
```

- [ ] Run all tests:

```bash
cd service && python -m pytest tests/ -v
```

Expected: All tests PASS (84 existing + new confidence + correction tests).

- [ ] Commit:

```bash
git add service/function_app.py
git commit -m "feat: add /api/apply-corrections endpoint for HITL corrections"
```

---

## Task 4: Update Copilot Studio Agent — Action Definition

**Files:**
- Modify: `agent/actions/FinancialsExtractor-ExtractfinancialstatementsfromaPDFdoc.mcs.yml`

### Step 4.1: Add confidence output to extractor action

- [ ] Read the current action file and add a `confidence` output property alongside the existing outputs (balance_sheet, cash_flow, income_statement, summary, company_name).

The output should be a string type (JSON-stringified confidence object). Add it after the `company_name` output in the YAML.

- [ ] Commit:

```bash
git add agent/actions/FinancialsExtractor-ExtractfinancialstatementsfromaPDFdoc.mcs.yml
git commit -m "feat: add confidence output to extractor action definition"
```

---

## Task 5: Create HITL Review Topic

**Files:**
- Create: `agent/topics/HITLReview.mcs.yml`
- Modify: `agent/topics/FileUploadHandler.mcs.yml`

### Step 5.1: Create the HITL review topic

- [ ] Create `agent/topics/HITLReview.mcs.yml` with the wizard flow:
  1. Receive job_id, confidence, and statement metadata from FileUploadHandler
  2. Display Navigator card with confidence levels
  3. On "Start Review": loop through statements, send editable Adaptive Card per statement
  4. Collect corrections from each card submission into topic variables
  5. On final submit: call `/api/apply-corrections` with job_id + corrections
  6. Pass corrected JSON to FinanceStatementProcessor (Excel generation)
  7. Return Excel file in chat

**Note for implementer:** This topic will contain Adaptive Card JSON templates inline. The card templates should follow the designs in the spec (Section 4). Use `AdaptiveCardPrompt` nodes for each card. Use `Input.Text` for editable fields with `id` matching the `row_{index}_{field}` pattern. Use `Input.ChoiceSet` for section and row_type dropdowns.

- [ ] Commit:

```bash
git add agent/topics/HITLReview.mcs.yml
git commit -m "feat: add HITL review topic with wizard flow and adaptive cards"
```

### Step 5.2: Modify FileUploadHandler to route to HITL

- [ ] Update `agent/topics/FileUploadHandler.mcs.yml`:
  - After extraction completes, store `confidence` in a topic variable
  - Instead of calling FinanceStatementProcessor directly, redirect to HITLReview topic
  - Pass job_id, confidence, company_name, and summary to the new topic

- [ ] Commit:

```bash
git add agent/topics/FileUploadHandler.mcs.yml
git commit -m "feat: route FileUploadHandler to HITL review instead of direct Excel"
```

---

## Task 6: Extend Job Store for HITL Sessions

**Files:**
- Modify: `service/function_app.py`

### Step 6.1: Keep job results after status retrieval

- [ ] Currently, `extract_status` deletes the job blob after returning "completed" results (line ~274: `_delete_job(job_id)`). For HITL, the job must persist until `/api/apply-corrections` is called.

Change: Remove `_delete_job(job_id)` from the "completed" branch of `extract_status`. The job is deleted in `apply_corrections` instead.

Add a TTL cleanup: Add a comment noting that blob TTL (30 min) should be configured at the storage container level, or add a `created_at` timestamp to the job metadata for manual cleanup.

- [ ] Run all tests:

```bash
cd service && python -m pytest tests/ -v
```

- [ ] Commit:

```bash
git add service/function_app.py
git commit -m "feat: retain job results for HITL review session"
```

---

## Task 7: End-to-End Verification

### Step 7.1: Run full test suite

- [ ] Run all tests:

```bash
cd service && python -m pytest tests/ -v
```

Expected: All tests PASS.

### Step 7.2: Manual integration test

- [ ] Start the Azure Function locally:

```bash
cd service && func start
```

- [ ] Test extraction + confidence:

```bash
curl -X POST http://localhost:7071/api/extract \
  -H "Content-Type: application/pdf" \
  --data-binary @test.pdf
```

Verify response includes `confidence` block with scores and flagged_rows.

- [ ] Test correction application:

```bash
curl -X POST http://localhost:7071/api/apply-corrections \
  -H "Content-Type: application/json" \
  -d '{"job_id": "<from above>", "corrections": {"balance_sheet": {"row_0": {"label": "Fixed"}}}}'
```

Verify response has corrected statement JSON with updated label.

### Step 7.3: Final commit

- [ ] Commit any remaining changes and tag:

```bash
git add -A
git commit -m "feat: HITL adaptive card review - complete implementation"
```

---

## Execution Order Summary

| Task | What it builds | Depends on |
|------|---------------|-----------|
| **1** | Confidence scorer module + tests | Nothing |
| **2** | Pipeline integration (confidence in response) | Task 1 |
| **3** | Corrections module + `/api/apply-corrections` endpoint | Nothing (uses `_parse_financial_value` from statement_detector) |
| **4** | Agent action definition update | Task 2 (new output field) |
| **5** | HITL review topic + FileUploadHandler routing | Tasks 2, 3, 4, 6 |
| **6** | Job store TTL extension | Must be done before Task 5 and before e2e test |
| **7** | End-to-end verification | All above |

Tasks 1 and 3 can run in parallel (no dependency between them). Task 6 MUST be done before any end-to-end testing of `/api/apply-corrections` (it needs the job to still exist after status retrieval).

## Notes for Implementer

- **Pre-existing bug**: `function_app.py` line ~303 references `len(_jobs)` in the health route but `_jobs` doesn't exist. Fix: replace with `0` or remove the field.
- **"Processing" response consistency**: When adding `confidence` to the "completed" response in `extract_status`, also add `"confidence": ""` to the "processing" response (line ~259) so the connector schema stays consistent across polling calls.
- **`_SECTION_TO_GROUP` import**: The corrections module imports `_SECTION_TO_GROUP` from `enrichment.py`. This is a private name. Consider re-exporting it as `SECTION_TO_GROUP` via `extractor/__init__.py` in a future cleanup.

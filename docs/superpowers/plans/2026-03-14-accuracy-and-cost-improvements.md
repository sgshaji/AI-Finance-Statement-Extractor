# Accuracy & Cost Improvements Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate silent data corruption risks, reduce unnecessary LLM API calls, and add PDF caching to cut Azure CU costs to zero on repeated runs.

**Architecture:** Seven targeted fixes across three files (`llm_reconciler.py`, `statement_detector.py`, `main.py`) plus a new test suite. Each fix is independent and produces a working, testable change. Fixes are ordered by data-integrity impact.

**Tech Stack:** Python 3.11+, pytest, openai SDK, requests, hashlib (stdlib)

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `extractor/llm_reconciler.py` | Fixes 1, 2, 4 |
| Modify | `extractor/statement_detector.py` | Fixes 5, 6, 7 |
| Modify | `main.py` | Fix 3 |
| Modify | `requirements.txt` | Add pytest |
| Create | `tests/__init__.py` | Test package marker |
| Create | `tests/test_llm_reconciler.py` | Tests for fixes 1, 2, 4 |
| Create | `tests/test_statement_detector.py` | Tests for fixes 5, 6, 7 |
| Create | `tests/test_caching.py` | Tests for fix 3 |
| Create | `pytest.ini` | Pytest configuration |

---

## Task 1: Fix regex precedence bug (statement_detector.py:358)

Fix `_SECTION_TOTAL_RE` so `activities$` doesn't match arbitrary labels.

## Task 2: Validate LLM row indices (llm_reconciler.py)

Build valid-index sets from the lists sent to the LLM. Reject out-of-set mappings and duplicate ghost targets.

## Task 3: Sync rows[] after ghost label swap (llm_reconciler.py)

Update `rows[suspect_row]` alongside `_set_label(cells, ...)` in `reconcile_suspect_ghost`.

## Task 4: Heuristic-first matching before LLM (llm_reconciler.py)

Add deterministic matching for unambiguous cases (single suspect/ghost, prefix/suffix match). Only call LLM for remaining ambiguous candidates.

## Task 5: Dynamic column count (statement_detector.py)

Replace `_MAX_VALUE_COLS = 2` with column count derived from header tokens.

## Task 6: Cross-validate totals (statement_detector.py)

After building statement JSON, compare section-total rows against sum of line items. Emit warnings and a `validation_warnings` field.

## Task 7: PDF hash caching (main.py)

Compute SHA-256 of input PDF. Skip Azure CU call if cached result exists for that hash.

## Task 8: Test infrastructure + unit tests

Create pytest config and test files covering all seven fixes.

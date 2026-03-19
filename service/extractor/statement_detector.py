"""
extractor/statement_detector.py
-------------------------------
Locates and parses the three core financial statements from the markdown
string produced by Azure Content Understanding prebuilt-read.

How Azure CU structures the output
  The full document is returned as a single markdown string at:
    result.contents[0].markdown
  Page boundaries are tracked via:
    result.contents[0].pages[*].spans[*].offset / .length
  Azure CU does NOT return structured table objects for financial tables;
  instead each table row is a sequence of \n\n-separated plain-text tokens.

Pipeline (in order)
  1. _all_heading_offsets   — find all pages where each statement appears
  2. _pick_cluster_offsets  — select the main consolidated statements cluster
                              (ignores summary tables scattered earlier)
  3. locate_statements      — determine char-offset boundaries per statement
  4. _parse_plain_text_table— tokenise and reconstruct rows/columns/cells
  5. build_statement_json   — assemble final JSON; optionally run LLM pass
"""

from __future__ import annotations

import re
from typing import Optional

# Heading patterns to find in the markdown (tried in order; first match wins)
HEADINGS: dict[str, list[str]] = {
    "balance_sheet": [
        "Consolidated Statement of Financial Position",
        "Consolidated Balance Sheet",
        "Statement of Financial Position",
        "Balance Sheet",
    ],
    "cash_flow": [
        "Consolidated Statement of Cash Flows",
        "Consolidated Statement of Cash Flow",
        "Statement of Cash Flows",
    ],
    "income_statement": [
        "Consolidated Statement of Income",
        "Consolidated Statement of Operations",
        "Consolidated Statement of Profit",
        "Income Statement",
    ],
}

# A heading match is valid only if numeric data appears within this many
# characters after it — rules out TOC references.
_DATA_LOOKAHEAD = 1000
_NUMBER_RE    = re.compile(r"\d[\d,]+")

# Density-based end-of-section detection
# Scan forward in windows; when financial number count drops below threshold
# for two consecutive windows, that marks the end of the statement section.
_DENSITY_WINDOW    = 3000   # chars per window
_DENSITY_THRESHOLD = 5      # min numbers per window to still be "in statement"
_DENSITY_MIN_CONSECUTIVE = 2  # consecutive low-density windows required

# Note refs are small integers like "5", "6, 15", "10, 15" — max 2 digits each part
_NOTE_REF_RE  = re.compile(r"^\d{1,2}(?:[,\s]+\d{1,2})*$")
# Financial values: optional leading minus, digits+commas+optional decimal,
# or (negatives).  Decimal support handles EPS and per-share figures.
_VALUE_RE     = re.compile(r"^-?[\d,]+(?:\.\d+)?$|^\([\d,]+(?:\.\d+)?\)$")
# Page markers to strip from sections before parsing.
# Also strips section-numbering stubs (e.g. "2\)" / "3"") that Azure CU
# sometimes emits as isolated tokens at section boundaries.
_PAGE_MARKER_RE = re.compile(r"^-\d+-$|^<!--.*-->$|^\d+\\?\)$")

# Hard-stop pattern: Azure CU wraps document-level section headings in [...].
# "[Notes to ..." universally signals the narrative notes section that follows
# financial tables in any annual/interim report.
# Additional patterns catch other IFRS-standard statement blocks that can
# appear between the three statements we extract (e.g. Equity section between
# Income and Cash Flow in Rakuten-style layouts).
_NOTES_TERMINATOR_RE = re.compile(
    r"\[Notes to\b"
    r"|Consolidated Statement of Comprehensive Income"
    r"|Consolidated Statement of Changes"
    r"|Statement of Changes in (Stockholders|Shareholders|Owners|Parent)",
    re.IGNORECASE,
)


def _find_section_end_by_density(markdown: str, start_offset: int) -> int:
    """
    Generic end-of-section detector.
    Scans forward in fixed windows from start_offset.
    Returns the offset where financial number density drops to near-zero,
    indicating the transition from tabular statements to narrative notes.
    Works for any company, any language.
    """
    # Hard-stop: if a "[Notes to ..." heading is found within 100K chars,
    # use it as the section boundary — the notes section has high number
    # density (all the footnote tables) so the density scan alone is
    # unreliable once we reach it.
    note_m = _NOTES_TERMINATOR_RE.search(markdown, start_offset)
    if note_m and (note_m.start() - start_offset) < 100_000:
        return note_m.start()

    pos = start_offset
    low_count = 0
    while pos < len(markdown):
        window = markdown[pos: pos + _DENSITY_WINDOW]
        nums = len(re.findall(r"\b\d[\d,]{2,}\b", window))
        if nums < _DENSITY_THRESHOLD:
            low_count += 1
            if low_count >= _DENSITY_MIN_CONSECUTIVE:
                # Return the start of the first low-density window
                return pos - (_DENSITY_MIN_CONSECUTIVE - 1) * _DENSITY_WINDOW
        else:
            low_count = 0
        pos += _DENSITY_WINDOW
    return len(markdown)

def _get_contents(raw_result: dict) -> dict:
    """Return contents[0] from the Azure CU result envelope."""
    result = raw_result.get("result", raw_result)
    contents = result.get("contents", [])
    if not contents:
        raise ValueError("Azure CU result has no 'contents' array.")
    return contents[0]


def _get_markdown(raw_result: dict) -> str:
    return _get_contents(raw_result).get("markdown", "")


def _get_pages(raw_result: dict) -> list[dict]:
    return _get_contents(raw_result).get("pages", [])


# ---------------------------------------------------------------------------
# Page number lookup (char offset -> page number)
# ---------------------------------------------------------------------------

def _build_page_map(pages: list[dict]) -> list[tuple[int, int, int]]:
    """
    Build a sorted list of (start_offset, end_offset, page_number) tuples
    using the spans[] array on each page object.
    """
    mapping: list[tuple[int, int, int]] = []
    for page in pages:
        page_num: int = page.get("pageNumber", 0)
        for span in page.get("spans", []):
            offset: int = span.get("offset", 0)
            length: int = span.get("length", 0)
            mapping.append((offset, offset + length, page_num))
    mapping.sort(key=lambda t: t[0])
    return mapping


def _offset_to_page(offset: int, page_map: list[tuple[int, int, int]]) -> int:
    """Return the page number containing the given character offset."""
    for start, end, page_num in page_map:
        if start <= offset < end:
            return page_num
    return page_map[-1][2] if page_map else 0


# ---------------------------------------------------------------------------
# Statement locator
# ---------------------------------------------------------------------------

def _all_heading_offsets(markdown: str, headings: list[str]) -> list[int]:
    """
    Return ALL char offsets where a heading appears AND is followed by numeric
    financial data within _DATA_LOOKAHEAD chars.

    Headings are tried in priority order (most specific first). As soon as one
    heading yields any valid match, those offsets are returned exclusively.
    This prevents short fallback terms (e.g. "Balance Sheet") from matching
    unrelated fragments (e.g. "balance sheet amount" in notes tables) when a
    more specific heading would have matched.
    """
    for heading in headings:
        results = []
        for m in re.finditer(re.escape(heading), markdown, re.IGNORECASE):
            lookahead = markdown[m.start(): m.start() + _DATA_LOOKAHEAD]
            if _NUMBER_RE.search(lookahead):
                results.append(m.start())
        if results:
            return sorted(results)
    return []


def _pick_cluster_offsets(
    all_offsets: dict[str, list[int]]
) -> dict[str, Optional[int]]:
    """
    Generic cluster selection: annual reports always contain a section where
    all three financial statements appear close together (within ~200,000 chars).
    Summary tables and segment disclosures scatter individual statements
    throughout the document, but the actual consolidated statements section
    is a tight cluster.

    Algorithm:
      1. Collect every valid occurrence of every statement heading.
      2. For the statement type with the fewest occurrences (most specific),
         use each occurrence as a candidate anchor.
      3. For each anchor, count how many other statement types have an
         occurrence within MAX_CLUSTER_SPAN chars after it.
      4. Pick the anchor with the most co-located statements. Ties broken
         by preferring later anchors (main statements appear after summaries).
      5. For each statement type choose the occurrence nearest to and after
         the winning anchor.
    """
    MAX_CLUSTER_SPAN = 200_000  # chars — tuned to fit any typical annual report

    # Flatten all offsets into (offset, type) pairs
    all_pairs: list[tuple[int, str]] = []
    for stype, offsets in all_offsets.items():
        for o in offsets:
            all_pairs.append((o, stype))
    all_pairs.sort()

    best_anchor: Optional[int] = None
    best_score = -1

    for anchor_offset, anchor_type in all_pairs:
        # Count distinct statement types with an occurrence in [anchor, anchor+SPAN]
        types_found = set()
        for offset, stype in all_pairs:
            if offset >= anchor_offset and offset <= anchor_offset + MAX_CLUSTER_SPAN:
                types_found.add(stype)
        score = len(types_found)
        # Prefer higher score; on tie prefer EARLIER anchor — the first
        # complete cluster is the main consolidated statements section;
        # later ones tend to be subsidiary or segment statements.
        if score > best_score or (score == best_score and best_anchor is not None and anchor_offset < best_anchor):
            best_score = score
            best_anchor = anchor_offset

    if best_anchor is None:
        # Fallback: just return first occurrence of each
        return {stype: (offsets[0] if offsets else None)
                for stype, offsets in all_offsets.items()}

    # For each statement type pick the occurrence closest to and >= best_anchor
    chosen: dict[str, Optional[int]] = {}
    for stype, offsets in all_offsets.items():
        candidates = [o for o in offsets if o >= best_anchor]
        if candidates:
            chosen[stype] = min(candidates)
        elif offsets:
            chosen[stype] = offsets[0]  # only occurrence is before anchor
        else:
            chosen[stype] = None

    return chosen


def locate_statements(raw_result: dict) -> dict[str, Optional[dict]]:
    """
    Scan the markdown and return a mapping of statement type -> page range.
    Uses cluster detection so summary tables scattered earlier in the document
    do not override the actual consolidated financial statements section.
    """
    markdown = _get_markdown(raw_result)
    pages = _get_pages(raw_result)
    page_map = _build_page_map(pages)

    # Collect all valid occurrences per statement type
    all_offsets: dict[str, list[int]] = {}
    for statement_type, headings in HEADINGS.items():
        all_offsets[statement_type] = _all_heading_offsets(markdown, headings)

    # Pick the best cluster
    offsets = _pick_cluster_offsets(all_offsets)

    # Determine section boundaries
    located: dict[str, Optional[dict]] = {}
    for statement_type, offset in offsets.items():
        if offset is None:
            located[statement_type] = None
            continue

        start_page = _offset_to_page(offset, page_map)

        # Section ends at the nearest other chosen statement heading
        next_offset = None
        for other_type, other_offset in offsets.items():
            if other_type == statement_type or other_offset is None:
                continue
            if other_offset > offset:
                if next_offset is None or other_offset < next_offset:
                    next_offset = other_offset

        # For all statements: also look for hard-stop section terminators
        # (e.g. Comprehensive Income or Changes in Equity sections that can
        # appear between the three main statements).  Skip 200 chars past the
        # current heading to avoid matching the heading text itself.
        term_m = _NOTES_TERMINATOR_RE.search(markdown, offset + 200)
        if term_m:
            if next_offset is None or term_m.start() < next_offset:
                next_offset = term_m.start()

        # No adjacent statement or terminator found — use density-based end
        if next_offset is None:
            next_offset = _find_section_end_by_density(markdown, offset)

        end_page = _offset_to_page(next_offset - 1, page_map)
        if end_page < start_page:
            end_page = start_page

        located[statement_type] = {
            "start_page": start_page,
            "end_page": end_page,
            "md_offset": offset,
            "md_end_offset": next_offset,
        }

    return located


# ---------------------------------------------------------------------------
# Plain-text financial table parser
# ---------------------------------------------------------------------------
# Azure CU prebuilt-read returns tables as plain text with \n\n separators.
# Long labels are often split across consecutive \n\n-separated tokens when
# text wraps across PDF table cell lines, e.g.:
#
#   [tok A] "Net decrease (increase) in receivables under"   (first part)
#   [tok B] "(21,517)"                                        (row A values)
#   [tok C] "28,614"
#   [tok D] "securities borrowing transactions"              (continuation of A)
#   [tok E] "(42,391)"      ← these belong to the NEXT row whose label Azure
#   [tok F] "86,042"          CU dropped entirely
#
# Strategy
# --------
#  1. After consuming a row's values, peek at the next token. If it starts
#     with a lowercase letter it is a continuation fragment of the current
#     label — merge it.
#  2. After merging a fragment, check for more values. Any values found here
#     are ORPHANED (Azure CU dropped the true label for that row). Emit them
#     as a separate row using the fragment text as the best-available label.
#  3. "Section total" rows (label starts with "Net cash" or ends in
#     "activities") are never merged — this prevents [113-120]-style garbage
#     fragments from attaching to summary rows.
#  4. Rows with no values that are just continuation/garbage fragments (all
#     lowercase or repeated single-word noise) are suppressed from output.
# ---------------------------------------------------------------------------

# Japanese full-width dash used for nil/zero entries in Japanese filings
_NIL_RE      = re.compile(r"^[－\-－]+$")

# Header tokens: currency notes, "Note" keyword, year labels, period ranges.
_HEADER_TOKEN_RE = re.compile(
    r"\b(20\d{2}|Note|January|February|March|April|May|June|"
    r"July|August|September|October|November|December|Fiscal Year|Millions)\b",
    re.IGNORECASE,
)

# Section-total labels: never apply post-value continuation merging on these
_SECTION_TOTAL_RE = re.compile(
    r"^Net cash|activities$|^Cash and cash equivalents at|"
    r"^Net increase \(decrease\) in cash",
    re.IGNORECASE,
)

# Maximum data-value columns per row.  Standard annual reports compare two
# fiscal periods; capping at 2 prevents greedy consumption of nil dash tokens
# that appear as page separators after the final row of a section.
_MAX_VALUE_COLS = 2


def _is_value_or_note(tok: str) -> bool:
    return bool(_VALUE_RE.match(tok) or _NOTE_REF_RE.match(tok) or _NIL_RE.match(tok))


def _is_continuation_fragment(tok: str) -> bool:
    """True if tok is the wrapped tail of the previous label (starts lowercase)."""
    return bool(tok) and tok[0].islower()


def _parse_plain_text_table(section: str) -> tuple[list[str], list[str], list[dict]]:
    """
    Parse the plain-text financial table format into rows, columns, cells.
    Handles:
    - Header vs data token separation
    - Continuation fragment merging (restores full labels)
    - Orphaned value preservation (values that Azure CU detached from their labels)
    - Suppression of valueless noise rows
    """
    cleaned_tokens = [
        t.strip() for t in section.split("\n\n")
        if t.strip() and not _PAGE_MARKER_RE.match(t.strip())
    ]

    columns: list[str] = []
    rows: list[str] = []
    cells: list[dict] = []

    # ---- Phase 1: separate header tokens from data tokens ---- #
    col_headers_raw: list[str] = []
    data_tokens: list[str] = []
    header_done = False

    for tok in cleaned_tokens[1:]:   # skip statement heading itself
        if not header_done:
            if _is_value_or_note(tok):
                header_done = True
                data_tokens.append(tok)
            elif _HEADER_TOKEN_RE.search(tok):
                col_headers_raw.append(tok)
            else:
                header_done = True
                data_tokens.append(tok)
        else:
            data_tokens.append(tok)

    columns = [h for h in col_headers_raw if "20" in h or "Note" in h]
    if not columns:
        columns = col_headers_raw or ["Label", "Period 1", "Period 2"]

    # ---- Phase 2: walk data tokens ---- #
    n = len(data_tokens)

    def emit(label: str, vals: list[str]) -> None:
        """Append one row to rows/cells."""
        nonlocal row_index
        rows.append(label)
        cells.append({"row": row_index, "col": 0, "content": label, "kind": "content"})
        for vi, val in enumerate(vals):
            cells.append({"row": row_index, "col": vi + 1, "content": val, "kind": "content"})
        row_index += 1

    row_index = 0
    i = 0
    while i < n:
        tok = data_tokens[i]

        if _is_value_or_note(tok):
            i += 1   # orphaned value/note — skip
            continue

        # Skip mid-section page column headers that leaked through page breaks
        # (e.g. "(Millions of Yen)", "Note", "Fiscal Year ended March 31, …").
        # These match _HEADER_TOKEN_RE but are NOT followed by numeric values;
        # a real data label that incidentally contains a year IS followed by values.
        if _HEADER_TOKEN_RE.search(tok) and not _SECTION_TOTAL_RE.search(tok):
            has_vals = False
            for pi in range(i + 1, min(i + 5, n)):
                pt = data_tokens[pi]
                if _VALUE_RE.match(pt):
                    has_vals = True
                    break
                if not _NOTE_REF_RE.match(pt) and not _HEADER_TOKEN_RE.search(pt):
                    break  # hit a real label — stop lookahead
            if not has_vals:
                i += 1
                continue

        label = tok
        i += 1

        # Skip optional note ref
        if i < n and _NOTE_REF_RE.match(data_tokens[i]):
            i += 1

        # Consume numeric values for this label
        vals: list[str] = []
        while i < n and _VALUE_RE.match(data_tokens[i]):
            vals.append(data_tokens[i])
            i += 1
        # Fill a missing second column with an adjacent nil marker if present.
        # Japanese reports use "－" for nil entries; they follow immediately
        # after the numeric value(s) and should not be consumed past the
        # expected number of data columns to avoid polluting later rows.
        while vals and i < n and _NIL_RE.match(data_tokens[i]) and len(vals) < _MAX_VALUE_COLS:
            vals.append(data_tokens[i])
            i += 1

        # ---- Continuation fragment loop ----
        # Only applies when current label is NOT a section total.
        was_merged = False   # tracks whether any fragment was merged into label
        if not _SECTION_TOTAL_RE.search(label):
            while i < n and _is_continuation_fragment(data_tokens[i]):
                fragment = data_tokens[i]
                i += 1

                # Skip optional note ref after fragment
                if i < n and _NOTE_REF_RE.match(data_tokens[i]):
                    i += 1

                # Numeric values immediately after the fragment, plus any nil fill
                orphan_vals: list[str] = []
                while i < n and _VALUE_RE.match(data_tokens[i]):
                    orphan_vals.append(data_tokens[i]);
                    i += 1
                while orphan_vals and i < n and _NIL_RE.match(data_tokens[i]) and len(orphan_vals) < _MAX_VALUE_COLS:
                    orphan_vals.append(data_tokens[i])
                    i += 1

                if orphan_vals:
                    # Fragment is followed by values → Azure CU dropped the
                    # true label for those values. Emit current row (with
                    # fragment merged into label), then emit the orphaned
                    # values under the fragment text as best-available label.
                    emit(label + " " + fragment, vals)
                    emit(fragment, orphan_vals)
                    label = None   # signal already emitted
                    vals = []
                    break
                else:
                    # Clean continuation — just extend the label
                    label = label + " " + fragment
                    was_merged = True

        # Emit if not already done inside the continuation loop
        if label is not None:
            # Suppress valueless noise rows: only lowercase-start tokens which
            # are orphaned continuation fragments with no data of their own.
            # Uppercase merged labels are kept even with no values — they may
            # represent real rows whose values Azure CU failed to extract.
            is_noise = not vals and label[:1].islower()
            if not is_noise:
                emit(label, vals)

    return rows, columns, cells


# ---------------------------------------------------------------------------
# Statement builder
# ---------------------------------------------------------------------------

def build_statement_json(
    statement_type: str,
    raw_result: dict,
    location: Optional[dict],
    use_llm: bool = False,
) -> dict:
    """Build a single statement JSON object.

    Args:
        use_llm: When True, run the LLM reconciliation pass after parsing to
                 fix dropped labels, truncated labels, and noise rows.
                 Requires AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_KEY in .env.
    """
    if location is None:
        return {
            "statement_type": statement_type,
            "status": "not_found",
            "start_page": None,
            "end_page": None,
            "title_english": None,
            "rows": [],
            "columns": [],
            "cells": [],
        }

    markdown = _get_markdown(raw_result)
    section = markdown[location["md_offset"]: location["md_end_offset"]]
    rows, columns, cells = _parse_plain_text_table(section)

    if use_llm and cells:
        from .llm_reconciler import reconcile
        rows, columns, cells = reconcile(statement_type, rows, columns, cells)

    return {
        "statement_type": statement_type,
        "status": "extracted" if cells else "found",
        "start_page": location["start_page"],
        "end_page": location["end_page"],
        "title_english": statement_type.replace("_", " ").title(),
        "rows": rows,
        "columns": columns,
        "cells": cells,
    }


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

def build_summary(locations: dict[str, Optional[dict]]) -> dict:
    """Build the summary.json object from the located statements."""
    entries = []
    for statement_type, location in locations.items():
        if location is None:
            entries.append({
                "statement_type": statement_type,
                "status": "not_found",
                "start_page": None,
                "end_page": None,
            })
        else:
            entries.append({
                "statement_type": statement_type,
                "status": "extracted",
                "start_page": location["start_page"],
                "end_page": location["end_page"],
            })
    return {"summary": entries}

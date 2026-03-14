"""
extractor/llm_reconciler.py
---------------------------
Post-processing pass that uses Azure OpenAI to fix three classes of label
errors that occur when Azure Content Understanding processes page-spanning
financial tables:

  1. suppress_noise_rows()       — heuristic, no LLM
     Removes syntactically impossible tokens: currency unit headers like
     "(Yen)", broken sentence fragments like "of the", or section-numbering
     stubs. Applied using an explicit allowlist of impossible-label patterns
     so that real ghost rows (proper label, missing values) are never removed.

  2. reconcile_suspect_ghost()   — LLM (one call per statement)
     Fixes rows where Azure CU dropped the true row label entirely:
       SUSPECT row = correct values attached to a lowercase fragment label
       GHOST row   = correct uppercase label with no values
     GPT-4o-mini receives both lists and returns a suspect→ghost mapping.
     The fragment label is replaced with the true ghost label; the ghost row
     is then removed.

  3. complete_truncated_labels() — LLM (one call per statement)
     Fixes labels that end mid-phrase at a preposition or conjunction
     ("and", "for", "of", ...) because Azure CU wrapped the cell text and
     the continuation ended up after the values on the next token.
     All truncated labels are sent in a single batch for completion.

All LLM calls use response_format=json_object so no free-text parsing is
needed — the response is always a directly usable JSON structure.

Configuration (via .env):
  AZURE_OPENAI_ENDPOINT    — Azure OpenAI resource URL (no trailing slash)
  AZURE_OPENAI_KEY         — API key for the Azure OpenAI resource
  AZURE_OPENAI_DEPLOYMENT  — Model deployment name (e.g. "gpt-4o-mini")
  AZURE_OPENAI_API_VERSION — API version (default: "2024-02-01")

Usage:
  Called automatically by statement_detector.build_statement_json when
  use_llm=True.  Can also be called directly:

    from extractor.llm_reconciler import reconcile
    rows, columns, cells = reconcile(statement_type, rows, columns, cells)
"""

import json
import os
import re
from typing import Optional

from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()

# ---------------------------------------------------------------------------
# Azure OpenAI client  (lazy-initialised so import never fails if creds absent)
# ---------------------------------------------------------------------------

_client: Optional[AzureOpenAI] = None


def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        api_key  = os.environ.get("AZURE_OPENAI_KEY", "")
        api_ver  = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")
        if not endpoint or not api_key:
            raise EnvironmentError(
                "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY must be set in .env "
                "to use the LLM reconciler."
            )
        _client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_ver,
        )
    return _client


_DEPLOYMENT = lambda: os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")

# ---------------------------------------------------------------------------
# Helpers to navigate the cells list
# ---------------------------------------------------------------------------

def _build_grid(cells: list[dict]) -> dict[int, dict[int, str]]:
    """row_index -> {col_index -> content}"""
    grid: dict[int, dict[int, str]] = {}
    for c in cells:
        grid.setdefault(c["row"], {})[c["col"]] = c["content"]
    return grid


def _get_label(grid: dict, row: int) -> str:
    return grid.get(row, {}).get(0, "")


def _get_values(grid: dict, row: int) -> list[str]:
    d = grid.get(row, {})
    return [d[k] for k in sorted(k for k in d if k > 0)]


def _set_label(cells: list[dict], row: int, new_label: str) -> None:
    for c in cells:
        if c["row"] == row and c["col"] == 0:
            c["content"] = new_label
            return


def _remove_rows(cells: list[dict], row_indices: set[int]) -> list[dict]:
    return [c for c in cells if c["row"] not in row_indices]


# ---------------------------------------------------------------------------
# Step 1 — suppress_noise_rows  (no LLM)
# ---------------------------------------------------------------------------

# Patterns that are NEVER valid financial line-item labels.
# Only match when the row also has no values (empty row).
_NOISE_LABEL_RE = re.compile(
    r"^\(Yen\)$"                          # currency unit
    r"|^\(Millions"                        # currency unit
    r"|\bof the$"                          # broken sentence tail
    r"|^of\b"                              # broken sentence tail
    r"|^Company:?$"                        # ref to reporting entity
    r"|^attributable to"                   # incomplete phrase
    r"|\bper share attributable\b"         # EPS sub-header fragment
    r"|^\d+\\?\)$"                         # section numbering stub e.g. "3\)"
    r"|^-+$",                              # dash separators
    re.IGNORECASE,
)


def suppress_noise_rows(
    rows: list[str],
    columns: list[str],
    cells: list[dict],
) -> tuple[list[str], list[str], list[dict]]:
    """
    Remove rows that are syntactically impossible as financial line items
    and have no values.  Only applies an explicit allowlist of patterns
    so that real rows with missing values (ghost rows) are never removed.
    """
    grid = _build_grid(cells)
    drop: set[int] = set()

    for row_idx in sorted(grid):
        label = _get_label(grid, row_idx)
        vals  = _get_values(grid, row_idx)
        if not vals and _NOISE_LABEL_RE.search(label):
            drop.add(row_idx)

    if not drop:
        return rows, columns, cells

    new_cells = _remove_rows(cells, drop)
    new_rows  = [r for i, r in enumerate(rows) if i not in drop]
    return new_rows, columns, new_cells


# ---------------------------------------------------------------------------
# Step 2 — reconcile_suspect_ghost  (LLM)
# ---------------------------------------------------------------------------

# A suspect label starts with lowercase — it is a continuation fragment, not
# a real label.  It has values because Azure CU attached them to the fragment
# instead of to the (dropped) true label.
_SUSPECT_RE = re.compile(r"^[a-z]")

# A trailing-preposition label was truncated at a page wrap.
_TRUNCATED_RE = re.compile(
    r"\b(and|for|of|in|the|by|to|from|with|under|on|at)\s*$",
    re.IGNORECASE,
)


def reconcile_suspect_ghost(
    statement_type: str,
    rows: list[str],
    columns: list[str],
    cells: list[dict],
) -> tuple[list[str], list[str], list[dict]]:
    """
    Use GPT-4o-mini to match suspect rows (fragment label + correct values)
    to ghost rows (real label + no values), then swap the labels.
    """
    grid = _build_grid(cells)

    suspects: list[dict] = []   # {"row": int, "label": str, "values": list}
    ghosts:   list[dict] = []   # {"row": int, "label": str}

    for row_idx in sorted(grid):
        label = _get_label(grid, row_idx)
        vals  = _get_values(grid, row_idx)
        if vals and _SUSPECT_RE.match(label):
            suspects.append({"row": row_idx, "label": label, "values": vals})
        elif not vals and label and not _SUSPECT_RE.match(label):
            ghosts.append({"row": row_idx, "label": label})

    if not suspects or not ghosts:
        return rows, columns, cells

    print(f"      [LLM] reconcile_suspect_ghost: "
          f"{len(suspects)} suspects, {len(ghosts)} ghosts")

    prompt = f"""You are reconciling a {statement_type.replace('_', ' ')} where an OCR tool dropped some row labels at page boundaries.

SUSPECT ROWS — values are correct but the label is a continuation fragment (NOT the true label):
{json.dumps(suspects, indent=2)}

GHOST ROWS — label is correct but values are missing (they were attached to a suspect row instead):
{json.dumps(ghosts, indent=2)}

Match each suspect row to the ghost row whose label is the true label for those values.
Not every suspect needs a match (some fragments are genuinely orphaned with no recoverable label).
Not every ghost needs a match.

Return ONLY a JSON object with key "matches" containing an array of objects:
  {{"suspect_row": <int>, "ghost_row": <int>}}
"""

    client = _get_client()
    response = client.chat.completions.create(
        model=_DEPLOYMENT(),
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You are a financial data quality expert. Return only valid JSON."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0,
    )

    raw = response.choices[0].message.content
    try:
        result = json.loads(raw)
        matches = result.get("matches", [])
    except (json.JSONDecodeError, AttributeError):
        print(f"      [LLM] reconcile_suspect_ghost: bad JSON response, skipping")
        return rows, columns, cells

    drop_after_swap: set[int] = set()

    for m in matches:
        suspect_row = m.get("suspect_row")
        ghost_row   = m.get("ghost_row")
        if suspect_row is None or ghost_row is None:
            continue
        ghost_label = _get_label(grid, ghost_row)
        if not ghost_label:
            continue
        # Replace the fragment label on the suspect row with the true ghost label
        _set_label(cells, suspect_row, ghost_label)
        # Remove the now-resolved ghost row (it has no values; its label is moved)
        drop_after_swap.add(ghost_row)
        print(f"        row {suspect_row}: '{_get_label(grid, suspect_row)}' → '{ghost_label}'")

    new_cells = _remove_rows(cells, drop_after_swap)
    new_rows  = [r for i, r in enumerate(rows) if i not in drop_after_swap]
    return new_rows, columns, new_cells


# ---------------------------------------------------------------------------
# Step 3 — complete_truncated_labels  (LLM)
# ---------------------------------------------------------------------------

def complete_truncated_labels(
    statement_type: str,
    rows: list[str],
    columns: list[str],
    cells: list[dict],
) -> tuple[list[str], list[str], list[dict]]:
    """
    Use GPT-4o-mini to complete labels that end mid-phrase at a preposition
    or conjunction (e.g. "Proceeds from sales and redemption of investment").
    Sends all truncated labels in a single batch call.
    """
    grid = _build_grid(cells)

    truncated: list[dict] = []  # {"row": int, "label": str}
    for row_idx in sorted(grid):
        label = _get_label(grid, row_idx)
        vals  = _get_values(grid, row_idx)
        if vals and _TRUNCATED_RE.search(label):
            truncated.append({"row": row_idx, "label": label})

    if not truncated:
        return rows, columns, cells

    print(f"      [LLM] complete_truncated_labels: {len(truncated)} truncated labels")

    prompt = f"""You are correcting truncated row labels in a {statement_type.replace('_', ' ')} financial statement.
Each label below was cut off mid-phrase by an OCR tool at a page boundary.
Complete each label to its full, standard IFRS financial statement wording.
Keep the completion minimal — only add the words needed to complete the phrase.

Labels to complete:
{json.dumps(truncated, indent=2)}

Return ONLY a JSON object with key "completions" containing an array of objects:
  {{"row": <int>, "completed_label": "<full label string>"}}
Only include rows where you are confident in the completion.
"""

    client = _get_client()
    response = client.chat.completions.create(
        model=_DEPLOYMENT(),
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You are a financial data quality expert. Return only valid JSON."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0,
    )

    raw = response.choices[0].message.content
    try:
        result = json.loads(raw)
        completions = result.get("completions", [])
    except (json.JSONDecodeError, AttributeError):
        print(f"      [LLM] complete_truncated_labels: bad JSON response, skipping")
        return rows, columns, cells

    for comp in completions:
        row_idx       = comp.get("row")
        new_label     = comp.get("completed_label", "").strip()
        if row_idx is None or not new_label:
            continue
        old_label = _get_label(grid, row_idx)
        _set_label(cells, row_idx, new_label)
        # Keep rows list in sync
        if row_idx < len(rows):
            rows[row_idx] = new_label
        print(f"        row {row_idx}: '{old_label}' → '{new_label}'")

    return rows, columns, cells


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def reconcile(
    statement_type: str,
    rows: list[str],
    columns: list[str],
    cells: list[dict],
) -> tuple[list[str], list[str], list[dict]]:
    """
    Run all three reconciliation steps in order:
      1. suppress_noise_rows     (heuristic, always runs)
      2. reconcile_suspect_ghost (LLM, only when suspects+ghosts detected)
      3. complete_truncated_labels (LLM, only when truncated labels detected)

    Returns updated (rows, columns, cells).
    Raises EnvironmentError if Azure OpenAI credentials are missing.
    """
    rows, columns, cells = suppress_noise_rows(rows, columns, cells)
    rows, columns, cells = reconcile_suspect_ghost(statement_type, rows, columns, cells)
    rows, columns, cells = complete_truncated_labels(statement_type, rows, columns, cells)
    return rows, columns, cells

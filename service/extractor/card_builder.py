"""Adaptive Card JSON builder for financial statement HITL review."""

import re
from typing import Any

STATEMENT_DISPLAY_NAMES = {
    "balance_sheet": "Balance Sheet",
    "income_statement": "Income Statement",
    "cash_flow": "Cash Flow",
}

SECTION_CHOICES = [
    {"title": "Current Assets", "value": "current_assets"},
    {"title": "Non-Current Assets", "value": "non_current_assets"},
    {"title": "Current Liabilities", "value": "current_liabilities"},
    {"title": "Non-Current Liabilities", "value": "non_current_liabilities"},
    {"title": "Equity", "value": "equity"},
    {"title": "Revenue", "value": "revenue"},
    {"title": "Operating Expenses", "value": "operating_expenses"},
    {"title": "Tax", "value": "tax"},
    {"title": "Other", "value": "other"},
]

ROW_TYPE_CHOICES = [
    {"title": "Line Item", "value": "line_item"},
    {"title": "Section Header", "value": "section_header"},
    {"title": "Subtotal", "value": "subtotal"},
    {"title": "Total", "value": "total"},
]

MAX_CLEAN_ROWS_SHOWN = 15


# ─── Navigator Card ─────────────────────────────────────────────────────────


def build_navigator_card(
    company_name: str,
    currency: str,
    unit: str,
    confidence: dict,
    summary: list,
) -> dict:
    """Build the entry-point Navigator card showing all 3 statements."""

    columns = []
    for stmt_key in ("balance_sheet", "income_statement", "cash_flow"):
        display_name = STATEMENT_DISPLAY_NAMES[stmt_key]
        conf_entry = confidence.get(stmt_key)
        if conf_entry is None:
            level_text = "\u2014 Not found"
            color = "Default"
        else:
            level = conf_entry.get("level", "medium")
            flagged = conf_entry.get("flagged_rows", [])
            if level == "high":
                level_text = "\u2713 High"
                color = "Good"
            elif level == "medium":
                level_text = f"\u26a0 Medium ({len(flagged)} issues)"
                color = "Warning"
            else:
                level_text = f"\u26a0 Low ({len(flagged)} issues)"
                color = "Attention"

        col = {
            "type": "Column",
            "width": "stretch",
            "items": [
                {"type": "TextBlock", "text": f"\u25cf {display_name}", "weight": "Bolder"},
                {"type": "TextBlock", "text": level_text, "color": color},
            ],
        }
        columns.append(col)

    body = [
        {"type": "TextBlock", "text": "Extraction Complete", "size": "Large", "weight": "Bolder"},
        {"type": "TextBlock", "text": f"{company_name}", "size": "Medium", "isSubtle": True},
        {"type": "TextBlock", "text": f"Currency: {currency} | Unit: {unit}", "isSubtle": True},
        {"type": "ColumnSet", "columns": columns},
    ]

    actions = [
        {"type": "Action.Submit", "title": "Start Review", "data": {"action": "start_review"}},
        {"type": "Action.Submit", "title": "Skip Review & Generate Excel", "data": {"action": "skip_review"}},
    ]

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": body,
        "actions": actions,
    }


# ─── Statement Review Card ──────────────────────────────────────────────────


def _flag_reason(row: dict, validation: dict) -> str | None:
    """Determine the flag reason for a row."""
    reasons = []
    if not row.get("label_raw"):
        reasons.append("Missing label")
    # Check validation warnings for subtotal mismatch
    for w in validation.get("warnings", []):
        if isinstance(w, dict) and w.get("row_index") == row["row_index"] and "subtotal" in str(w).lower():
            reasons.append("Subtotal mismatch")
            break
    if row.get("section") == "other":
        reasons.append("Unclassified section")
    for val in row.get("values", []):
        if val.get("normalized") is None and val.get("raw"):
            reasons.append("Value parse error")
            break
    label = (row.get("label_raw") or "").lower()
    if "total" in label and row.get("row_type") == "line_item":
        reasons.append("Possible type mismatch")
    return "; ".join(reasons) if reasons else None


def _get_row_value(row: dict, field: str, corrections: dict) -> str:
    """Get the value for a row field, applying corrections if present."""
    row_key = f"row_{row['row_index']}"
    corr = corrections.get(row_key, {})
    if field == "label":
        return corr.get("label", row.get("label_raw", ""))
    elif field == "section":
        return corr.get("section", row.get("section", "other"))
    elif field == "row_type":
        return corr.get("row_type", row.get("row_type", "line_item"))
    elif field.startswith("val_"):
        col_idx = int(field.split("_")[1])
        if field in corr:
            return corr[field]
        values = row.get("values", [])
        if col_idx < len(values):
            return values[col_idx].get("raw", "") or ""
        return ""
    return ""


def _build_editable_row(row: dict, columns: list, corrections: dict, flag_text: str | None) -> dict:
    """Build an editable container for a single row."""
    idx = row["row_index"]
    items: list[dict[str, Any]] = []

    if flag_text:
        items.append({
            "type": "TextBlock",
            "text": f"Row {idx}: {flag_text}",
            "weight": "Bolder",
            "size": "Small",
            "color": "Warning",
        })

    # Label input
    items.append({"type": "TextBlock", "text": "Label", "size": "Small", "isSubtle": True})
    items.append({
        "type": "Input.Text",
        "id": f"row_{idx}_label",
        "value": _get_row_value(row, "label", corrections),
        "placeholder": "Enter label",
    })

    # Value columns
    val_columns = []
    for col in columns:
        ci = col["column_index"]
        val_columns.append({
            "type": "Column",
            "width": "stretch",
            "items": [
                {"type": "TextBlock", "text": col["label"], "size": "Small", "isSubtle": True},
                {
                    "type": "Input.Text",
                    "id": f"row_{idx}_val_{ci}",
                    "value": _get_row_value(row, f"val_{ci}", corrections),
                },
            ],
        })
    items.append({"type": "ColumnSet", "columns": val_columns})

    # Section and Type dropdowns
    items.append({
        "type": "ColumnSet",
        "columns": [
            {
                "type": "Column",
                "width": "stretch",
                "items": [
                    {"type": "TextBlock", "text": "Section", "size": "Small", "isSubtle": True},
                    {
                        "type": "Input.ChoiceSet",
                        "id": f"row_{idx}_section",
                        "value": _get_row_value(row, "section", corrections),
                        "style": "compact",
                        "choices": SECTION_CHOICES,
                    },
                ],
            },
            {
                "type": "Column",
                "width": "stretch",
                "items": [
                    {"type": "TextBlock", "text": "Type", "size": "Small", "isSubtle": True},
                    {
                        "type": "Input.ChoiceSet",
                        "id": f"row_{idx}_row_type",
                        "value": _get_row_value(row, "row_type", corrections),
                        "style": "compact",
                        "choices": ROW_TYPE_CHOICES,
                    },
                ],
            },
        ],
    })

    return {"type": "Container", "separator": True, "items": items}


def _build_readonly_row(row: dict, columns: list) -> dict:
    """Build a read-only container for a single row."""
    cols = [
        {
            "type": "Column",
            "width": "stretch",
            "items": [{"type": "TextBlock", "text": row.get("label_raw", ""), "weight": "Bolder"}],
        }
    ]
    for col in columns:
        ci = col["column_index"]
        values = row.get("values", [])
        raw = values[ci].get("raw", "") if ci < len(values) else ""
        cols.append({
            "type": "Column",
            "width": "auto",
            "items": [{"type": "TextBlock", "text": raw or ""}],
        })
    return {"type": "Container", "separator": True, "items": [{"type": "ColumnSet", "columns": cols}]}


def build_statement_review_card(
    statement_type: str,
    statement_json: dict,
    confidence_entry: dict,
    corrections: dict,
    step_num: int,
    total_steps: int,
    editable: bool = True,
) -> dict:
    """Build an editable (or read-only) review card for a single statement."""

    display_name = STATEMENT_DISPLAY_NAMES.get(statement_type, statement_type)
    rows = statement_json.get("rows", [])
    columns = statement_json.get("columns", [])
    validation = statement_json.get("validation", {})
    metadata = statement_json.get("statement_metadata", {})

    level = confidence_entry.get("level", "medium")
    flagged_indices = set(confidence_entry.get("flagged_rows", []))

    # Page info
    page_range = metadata.get("page_range", {})
    page_start = page_range.get("start", "?")
    page_end = page_range.get("end", "?")
    page_text = f"Pages {page_start}-{page_end}" if page_start != page_end else f"Page {page_start}"

    level_display = {"high": "High", "medium": "Medium", "low": "Low"}.get(level, level.title())
    confidence_icon = "\u2713" if level == "high" else "\u26a0"

    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": f"{display_name} Review", "size": "Large", "weight": "Bolder"},
        {
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "items": [{"type": "TextBlock", "text": f"{confidence_icon} {level_display} Confidence"}],
                },
                {
                    "type": "Column",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": f"{page_text} | {len(rows)} rows | {len(flagged_indices)} flagged",
                        }
                    ],
                },
            ],
        },
    ]

    # Handle empty statement
    if len(rows) == 0:
        body.append({"type": "TextBlock", "text": "No data extracted", "isSubtle": True})
        actions = _build_navigation_actions(step_num, total_steps, editable)
        return _wrap_card(body, actions)

    # Split rows
    flagged_rows = [r for r in rows if r["row_index"] in flagged_indices]
    clean_rows = [r for r in rows if r["row_index"] not in flagged_indices]

    if editable:
        # ── Flagged rows section ──
        if flagged_rows:
            body.append({
                "type": "TextBlock",
                "text": f"\u26a0 FLAGGED ITEMS ({len(flagged_rows)})",
                "weight": "Bolder",
                "color": "Warning",
            })
            flagged_items = []
            for row in flagged_rows:
                reason = _flag_reason(row, validation) or "Review needed"
                flagged_items.append(_build_editable_row(row, columns, corrections, reason))
            body.append({"type": "Container", "style": "warning", "items": flagged_items})

        # ── Clean rows section ──
        if clean_rows:
            body.append({
                "type": "TextBlock",
                "text": f"\u2713 CLEAN ROWS ({len(clean_rows)})",
                "weight": "Bolder",
                "color": "Good",
            })
            shown_clean = clean_rows[:MAX_CLEAN_ROWS_SHOWN]
            inner_body: list[dict[str, Any]] = []
            for row in shown_clean:
                inner_body.append(_build_editable_row(row, columns, corrections, None))
            if len(clean_rows) > MAX_CLEAN_ROWS_SHOWN:
                inner_body.append({
                    "type": "TextBlock",
                    "text": f"Showing 15 of {len(clean_rows)} clean rows",
                    "isSubtle": True,
                })
            body.append({
                "type": "ActionSet",
                "actions": [
                    {
                        "type": "Action.ShowCard",
                        "title": "View/Edit All \u25bc",
                        "card": {"type": "AdaptiveCard", "body": inner_body},
                    }
                ],
            })
    else:
        # Read-only mode — all rows rendered as readonly
        for row in rows:
            body.append(_build_readonly_row(row, columns))

    actions = _build_navigation_actions(step_num, total_steps, editable)
    return _wrap_card(body, actions)


def _build_navigation_actions(step_num: int, total_steps: int, editable: bool) -> list:
    actions = []
    if step_num > 1:
        actions.append({"type": "Action.Submit", "title": "\u2190 Previous", "data": {"action": "previous"}})
    if step_num < total_steps:
        actions.append({"type": "Action.Submit", "title": "Next \u2192", "data": {"action": "next"}})
    else:
        actions.append({
            "type": "Action.Submit",
            "title": "Submit & Generate Excel",
            "data": {"action": "submit"},
        })
    if not editable:
        actions.append({"type": "Action.Submit", "title": "Edit", "data": {"action": "edit"}})
    return actions


def _wrap_card(body: list, actions: list) -> dict:
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": body,
        "actions": actions,
    }


# ─── Parse Card Submission ───────────────────────────────────────────────────

_ROW_FIELD_RE = re.compile(r"^row_(\d+)_(.+)$")


def parse_card_submission(payload: dict, statement_json: dict) -> tuple[str, dict]:
    """Parse flat Adaptive Card submission into (action, corrections) tuple."""

    action = payload.get("action", "")
    rows = statement_json.get("rows", [])

    # Index rows by row_index for quick lookup
    row_map = {r["row_index"]: r for r in rows}

    corrections: dict[str, dict] = {}

    for key, submitted_value in payload.items():
        if key == "action":
            continue
        m = _ROW_FIELD_RE.match(key)
        if not m:
            continue
        row_idx = int(m.group(1))
        field = m.group(2)
        row = row_map.get(row_idx)
        if row is None:
            continue

        original = _get_original_value(row, field)
        if str(submitted_value) != str(original):
            row_key = f"row_{row_idx}"
            if row_key not in corrections:
                corrections[row_key] = {}
            corrections[row_key][field] = submitted_value

    return action, corrections


def _get_original_value(row: dict, field: str) -> str:
    """Get the original value from a row for comparison."""
    if field == "label":
        return row.get("label_raw", "")
    elif field == "section":
        return row.get("section", "other")
    elif field == "row_type":
        return row.get("row_type", "line_item")
    elif field.startswith("val_"):
        col_idx = int(field.split("_")[1])
        values = row.get("values", [])
        if col_idx < len(values):
            return values[col_idx].get("raw", "") or ""
        return ""
    return ""

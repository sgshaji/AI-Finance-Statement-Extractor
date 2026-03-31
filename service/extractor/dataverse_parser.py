"""
Parses extraction pipeline results (v1.2 schema) into Dataverse-ready dicts.
Each function returns dicts with cree1_ prefixed keys matching Dataverse column logical names.
All choice fields use integer values (PicklistType).
"""
import json
from typing import Any

# Choice values
STATUS_PENDING_REVIEW = 833060002

STATEMENT_TYPE_MAP = {
    "balance_sheet": 833060001,
    "income_statement": 833060000,
    "cash_flow": 833060002,
}

ROW_TYPE_MAP = {
    "section_header": 833060000,
    "line_item": 833060001,
    "subtotal": 833060002,
    "total": 833060003,
}

REVIEW_STATUS_PENDING = 833060000

# Picklist mappings for ExtractionJob choice fields
LANGUAGE_MAP = {"en": 833060000, "zh": 833060001, "jp": 833060002}
CURRENCY_MAP = {"USD": 833060000, "CNY": 833060001}
CURRENCY_UNIT_MAP = {"ones": 833060000, "thousands": 833060001, "millions": 833060002}
REPORT_TYPE_MAP = {"annual_report": 833060000, "quarterly_report": 833060001}
ACCOUNTING_STANDARD_MAP = {"IFRS": 833060000, "Chinese_ASBE": 833060001}


def parse_job_row(job_id: str, result: dict) -> dict[str, Any]:
    """Parse pipeline result into an ExtractionJob row."""
    doc_meta = {}
    stmt_meta = {}
    total_rows = 0
    periods = set()

    for stype in ["balance_sheet", "income_statement", "cash_flow"]:
        stmt = result.get(stype)
        if not stmt or not isinstance(stmt, dict):
            continue
        if not doc_meta:
            doc_meta = stmt.get("document_metadata", {})
        if not stmt_meta:
            stmt_meta = stmt.get("statement_metadata", {})
        total_rows += len(stmt.get("rows", []))
        for col in stmt.get("columns", []):
            periods.add(col.get("label", ""))

    statements_found = sum(1 for s in ["balance_sheet", "income_statement", "cash_flow"]
                           if result.get(s) and isinstance(result[s], dict))

    avg_confidence = None
    conf = result.get("confidence", {})
    if isinstance(conf, dict):
        scores = [v["score"] for v in conf.values() if isinstance(v, dict) and "score" in v]
        if scores:
            avg_confidence = sum(scores) / len(scores)

    row = {
        "cree1_jobid": job_id,
        "cree1_companyname": doc_meta.get("company_name", ""),
        "cree1_statementsfound": statements_found,
        "cree1_totallineitems": total_rows,
        "cree1_avgconfidence": avg_confidence,
        "cree1_periods": ",".join(sorted(periods)),
        "cree1_summaryjson": json.dumps(result.get("summary", []), ensure_ascii=False),
        "cree1_status": STATUS_PENDING_REVIEW,
    }

    # Only include choice fields if we have a valid mapping (avoid 400 errors)
    lang = doc_meta.get("report_language", "")
    if lang in LANGUAGE_MAP:
        row["cree1_reportlanguage"] = LANGUAGE_MAP[lang]

    currency = stmt_meta.get("currency", "")
    if currency in CURRENCY_MAP:
        row["cree1_currency"] = CURRENCY_MAP[currency]

    unit = stmt_meta.get("unit", "")
    if unit in CURRENCY_UNIT_MAP:
        row["cree1_currencyunit"] = CURRENCY_UNIT_MAP[unit]

    report_type = doc_meta.get("report_type", "")
    if report_type in REPORT_TYPE_MAP:
        row["cree1_reporttype"] = REPORT_TYPE_MAP[report_type]

    acct_std = stmt_meta.get("accounting_standard", "")
    if acct_std in ACCOUNTING_STANDARD_MAP:
        row["cree1_accountingstandard"] = ACCOUNTING_STANDARD_MAP[acct_std]

    return row


def parse_statement_row(statement_type: str, stmt: dict) -> dict[str, Any]:
    """Parse a single statement dict into an ExtractedStatement row."""
    meta = stmt.get("statement_metadata", {})
    page_range = meta.get("page_range", {})

    return {
        "cree1_statementtitle": meta.get("statement_title", ""),
        "cree1_statementname": statement_type,
        "cree1_statementtype": STATEMENT_TYPE_MAP.get(statement_type, 833060000),
        "cree1_pagerangestart": page_range.get("start"),
        "cree1_pagerangeend": page_range.get("end"),
        "cree1_isconsolidated": meta.get("is_consolidated"),
        "cree1_isaudited": meta.get("is_audited"),
        "cree1_rawstatementjson": json.dumps(stmt, ensure_ascii=False),
        "cree1_reviewcomplete": False,
    }


def parse_line_item_rows(statement_type: str, stmt: dict) -> list[dict[str, Any]]:
    """Parse rows x columns into ExtractedLineItem rows (one per value per period)."""
    columns = stmt.get("columns", [])
    col_lookup = {c["column_index"]: c for c in columns}

    items = []
    for row in stmt.get("rows", []):
        for val in row.get("values", []):
            col_idx = val.get("column_index", 0)
            col = col_lookup.get(col_idx, {})

            items.append({
                "cree1_lineitemname": row.get("label_normalized") or row.get("label_raw", ""),
                "cree1_rowindex": row.get("row_index"),
                "cree1_rowtype": ROW_TYPE_MAP.get(row.get("row_type", ""), 833060001),
                "cree1_indentlevel": row.get("indent_level", 0),
                "cree1_sectionname": row.get("section", ""),
                "cree1_canonicalkey": row.get("canonical_key", ""),
                "cree1_canonicalgroup": row.get("canonical_group", ""),
                "cree1_labelraw": row.get("label_raw", ""),
                "cree1_period": col.get("label", ""),
                "cree1_periodtype": col.get("period_type", ""),
                "cree1_periodenddate": col.get("end_date"),
                "cree1_columnindex": col_idx,
                "cree1_valueraw": val.get("raw"),
                "cree1_valuenormalized": val.get("normalized"),
                "cree1_valuekind": val.get("value_kind", ""),
                "cree1_aiconfidence": val.get("confidence"),
                "cree1_sourcepage": row.get("source_page"),
                "cree1_signhint": row.get("sign_hint"),
                "cree1_reviewstatus": REVIEW_STATUS_PENDING,
            })

    return items

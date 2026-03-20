"""
function_app.py
---------------
Azure Function HTTP trigger for the Financial Statement Extraction Pipeline.

API:
    POST /api/extract
    Content-Type: application/pdf (or application/octet-stream)
    Body: raw PDF bytes

    Query params (optional):
      ?enrichment=false    Skip CU extractor enrichment (faster, no canonical keys)
      ?statements=balance_sheet,income_statement   Extract only specific statements

    Response 200:
    {
      "summary": [...],
      "balance_sheet": { v1.2 schema },
      "income_statement": { v1.2 schema },
      "cash_flow": { v1.2 schema }
    }

    Response 400: Missing or empty request body
    Response 500: Processing error with details
"""

import base64
import json
import logging
import os
import re
import tempfile
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import azure.functions as func

# ---------------------------------------------------------------------------
# Azure Function App
# ---------------------------------------------------------------------------

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

STATEMENT_TYPES = ["balance_sheet", "income_statement", "cash_flow"]

# ---------------------------------------------------------------------------
# Blob-based job store (uses function app's managed identity for storage)
# ---------------------------------------------------------------------------
_JOBS_CONTAINER = "extraction-jobs"
_container_client = None


def _get_container():
    global _container_client
    if _container_client is None:
        from azure.identity import ManagedIdentityCredential
        from azure.storage.blob import BlobServiceClient
        blob_uri = os.environ["AzureWebJobsStorage__blobServiceUri"]
        client_id = os.environ.get("AzureWebJobsStorage__clientId")
        credential = ManagedIdentityCredential(client_id=client_id)
        service = BlobServiceClient(blob_uri, credential=credential)
        _container_client = service.get_container_client(_JOBS_CONTAINER)
        if not _container_client.exists():
            _container_client.create_container()
    return _container_client


def _save_job(job_id: str, data: dict):
    _get_container().upload_blob(f"{job_id}.json", json.dumps(data, ensure_ascii=False), overwrite=True)


def _load_job(job_id: str) -> dict | None:
    try:
        blob = _get_container().download_blob(f"{job_id}.json")
        return json.loads(blob.readall())
    except Exception:
        return None


def _delete_job(job_id: str):
    try:
        _get_container().delete_blob(f"{job_id}.json")
    except Exception:
        pass


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


def _run_job(job_id: str, tmp_path: str, use_enrichment: bool,
             requested_types: list[str], file_name: str):
    """Run extraction in a background thread. Stores result in blob storage."""
    try:
        result = _run_pipeline(tmp_path, use_enrichment, requested_types, file_name)
        payload = _build_response_payload(result)
        _save_job(job_id, {"status": "completed", "result": payload})
        logging.info(f"Job {job_id} completed successfully")
    except Exception as e:
        logging.exception(f"Job {job_id} failed")
        _save_job(job_id, {"status": "failed", "error": str(e)})
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# POST /extract — accepts file, starts async job, returns 202
# ---------------------------------------------------------------------------

@app.route(route="extract", methods=["POST"])
def extract(req: func.HttpRequest) -> func.HttpResponse:
    """
    Async extraction endpoint. Accepts file upload, starts background
    processing, returns 202 with Location header for polling.
    """
    logging.info("Extract request received")

    # --- Parse query params ---
    use_enrichment = req.params.get("enrichment", "true").lower() != "false"
    requested_types = req.params.get("statements", "")
    if requested_types:
        requested_types = [s.strip() for s in requested_types.split(",")]
    else:
        requested_types = STATEMENT_TYPES

    # --- Get PDF bytes ---
    try:
        content_type = req.headers.get("Content-Type", "").lower()
        body = req.get_body()

        if "multipart/form-data" in content_type:
            uploaded_file = req.files.get("file")
            if not uploaded_file:
                return func.HttpResponse(
                    json.dumps({"error": "Missing 'file' in multipart form data"}),
                    status_code=400, mimetype="application/json",
                )
            pdf_bytes = uploaded_file.read()
            file_name = req.form.get("fileName", uploaded_file.filename or "document.pdf")
            logging.info(f"Received multipart file: {file_name} ({len(pdf_bytes)} bytes)")

        elif "application/json" in content_type or body[:1] == b"{":
            payload = req.get_json()
            file_url = payload.get("fileUrl") or payload.get("contentUrl")
            file_content = payload.get("fileContent")
            file_name = payload.get("fileName", "document.pdf")

            if file_content:
                if "," in file_content and file_content.index(",") < 100:
                    file_content = file_content.split(",", 1)[1]
                pdf_bytes = base64.b64decode(file_content)
            elif file_url:
                import requests as http_requests
                download_resp = http_requests.get(file_url, timeout=120)
                download_resp.raise_for_status()
                pdf_bytes = download_resp.content
            else:
                return func.HttpResponse(
                    json.dumps({"error": "Missing fileUrl or fileContent"}),
                    status_code=400, mimetype="application/json",
                )

        elif body and len(body) > 100:
            pdf_bytes = body
            file_name = "uploaded.pdf"

        else:
            return func.HttpResponse(
                json.dumps({"error": "Send multipart file, JSON, or raw PDF bytes"}),
                status_code=400, mimetype="application/json",
            )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": f"Failed to get PDF: {str(e)}"}),
            status_code=400, mimetype="application/json",
        )

    # --- Save to temp file and start background job ---
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        job_id = str(uuid.uuid4())
        _save_job(job_id, {"status": "processing"})

        thread = threading.Thread(
            target=_run_job,
            args=(job_id, tmp_path, use_enrichment, requested_types, file_name),
            daemon=True,
        )
        thread.start()
        logging.info(f"Job {job_id} started for {file_name}")

        # Return 202 with Location header for polling
        return func.HttpResponse(
            json.dumps({"jobId": job_id, "status": "processing"}),
            status_code=202,
            headers={
                "Location": f"/api/extract/status/{job_id}",
                "Retry-After": "5",
            },
            mimetype="application/json",
        )

    except Exception as e:
        logging.exception("Failed to start extraction job")
        return func.HttpResponse(
            json.dumps({"error": f"Failed to start job: {str(e)}", "traceback": traceback.format_exc()}),
            status_code=500,
            mimetype="application/json",
        )


# ---------------------------------------------------------------------------
# GET /extract/status/{jobId} — poll for results
# ---------------------------------------------------------------------------

@app.route(route="extract/status/{jobId}", methods=["GET"],
           auth_level=func.AuthLevel.ANONYMOUS)
def extract_status(req: func.HttpRequest) -> func.HttpResponse:
    """Poll endpoint for async extraction jobs. Anonymous auth — job ID is the token."""
    job_id = req.route_params.get("jobId")
    job = _load_job(job_id)

    if not job:
        return func.HttpResponse(
            json.dumps({"error": f"Job {job_id} not found"}),
            status_code=404, mimetype="application/json",
        )

    if job["status"] == "processing":
        # Return all fields (empty) so the connector schema stays consistent
        # across polling calls. The topic checks "status" to decide whether
        # to poll again.
        return func.HttpResponse(
            json.dumps({
                "status": "processing",
                "company_name": "",
                "summary": "",
                "balance_sheet": "",
                "income_statement": "",
                "cash_flow": "",
                "confidence": "",
            }),
            status_code=200,
            mimetype="application/json",
        )

    if job["status"] == "completed":
        result = job["result"]
        result["status"] = "completed"
        result["job_id"] = job_id
        # NOTE: Do NOT delete job here — HITL review needs it for
        # /api/apply-corrections. Job is deleted after corrections are applied,
        # or expires via blob TTL (30 min) if abandoned.
        return func.HttpResponse(
            json.dumps(result, ensure_ascii=False, indent=2),
            status_code=200,
            mimetype="application/json",
        )

    # Failed
    error = job.get("error", "Unknown error")
    _delete_job(job_id)
    return func.HttpResponse(
        json.dumps({"status": "failed", "error": error}),
        status_code=200, mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# POST /apply-corrections — apply HITL analyst corrections, return final JSON
# ---------------------------------------------------------------------------

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

    # Apply corrections per statement
    from extractor.corrections import apply_corrections as _apply
    for stype in STATEMENT_TYPES:
        stmt_json = result.get(stype)
        stmt_corrections = corrections.get(stype, {})
        if stmt_json and stmt_corrections:
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

    # Clean up cached job
    _delete_job(job_id)

    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        status_code=200, mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# POST /build-review-card — generate Adaptive Card JSON for HITL review
# ---------------------------------------------------------------------------

@app.route(route="build-review-card", methods=["POST"])
def build_review_card(req: func.HttpRequest) -> func.HttpResponse:
    """
    Generate an Adaptive Card JSON payload for a statement review.

    Body: {
      "job_id": "...",
      "card_type": "navigator" | "statement",
      "statement_type": "balance_sheet" (for statement cards),
      "step_num": 1,
      "total_steps": 3,
      "editable": true,
      "corrections": {}
    }
    """
    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400, mimetype="application/json",
        )

    from extractor.card_builder import build_navigator_card, build_statement_review_card

    job_id = body.get("job_id")
    card_type = body.get("card_type", "navigator")

    # Load cached job
    job = _load_job(job_id)
    if not job or job.get("status") != "completed":
        return func.HttpResponse(
            json.dumps({"error": f"Job {job_id} not found"}),
            status_code=404, mimetype="application/json",
        )

    result = job.get("result", {})

    if card_type == "navigator":
        # Parse confidence from result
        confidence_str = result.get("confidence", "{}")
        confidence = json.loads(confidence_str) if isinstance(confidence_str, str) else confidence_str

        # Extract metadata from first available statement
        company_name = result.get("company_name", "Unknown")
        currency = "USD"
        unit = "ones"
        for stype in STATEMENT_TYPES:
            stmt_str = result.get(stype)
            if stmt_str and stmt_str != "null":
                stmt = json.loads(stmt_str) if isinstance(stmt_str, str) else stmt_str
                if isinstance(stmt, dict):
                    meta = stmt.get("statement_metadata", {})
                    currency = meta.get("currency", currency)
                    unit = meta.get("unit", unit)
                    break

        summary_str = result.get("summary", "[]")
        summary = json.loads(summary_str) if isinstance(summary_str, str) else summary_str

        card = build_navigator_card(company_name, currency, unit, confidence, summary)

    elif card_type == "statement":
        stype = body.get("statement_type", "balance_sheet")
        stmt_str = result.get(stype)
        if not stmt_str or stmt_str == "null":
            return func.HttpResponse(
                json.dumps({"error": f"Statement {stype} not found in job"}),
                status_code=404, mimetype="application/json",
            )
        stmt = json.loads(stmt_str) if isinstance(stmt_str, str) else stmt_str

        confidence_str = result.get("confidence", "{}")
        confidence = json.loads(confidence_str) if isinstance(confidence_str, str) else confidence_str
        confidence_entry = confidence.get(stype, {"score": 0, "level": "low", "flagged_rows": []})

        card = build_statement_review_card(
            statement_type=stype,
            statement_json=stmt,
            confidence_entry=confidence_entry,
            corrections=body.get("corrections", {}),
            step_num=body.get("step_num", 1),
            total_steps=body.get("total_steps", 3),
            editable=body.get("editable", True),
        )
    else:
        return func.HttpResponse(
            json.dumps({"error": f"Unknown card_type: {card_type}"}),
            status_code=400, mimetype="application/json",
        )

    return func.HttpResponse(
        json.dumps(card, ensure_ascii=False),
        status_code=200, mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# POST /parse-card-submission — parse Adaptive Card submit payload into corrections
# ---------------------------------------------------------------------------

@app.route(route="parse-card-submission", methods=["POST"])
def parse_card_submission_route(req: func.HttpRequest) -> func.HttpResponse:
    """
    Parse an Adaptive Card submission payload into action + corrections diff.

    Body: {
      "job_id": "...",
      "statement_type": "balance_sheet",
      "payload": { "row_5_label": "Trade debtors", "action": "next", ... }
    }

    Returns: { "action": "next", "corrections": {"row_5": {"label": "Trade debtors"}} }
    """
    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400, mimetype="application/json",
        )

    from extractor.card_builder import parse_card_submission

    job_id = body.get("job_id")
    stype = body.get("statement_type", "balance_sheet")
    payload = body.get("payload", {})

    # Load statement from cached job
    job = _load_job(job_id)
    if not job or job.get("status") != "completed":
        return func.HttpResponse(
            json.dumps({"error": f"Job {job_id} not found"}),
            status_code=404, mimetype="application/json",
        )

    result = job.get("result", {})
    stmt_str = result.get(stype)
    if not stmt_str or stmt_str == "null":
        return func.HttpResponse(
            json.dumps({"error": f"Statement {stype} not found"}),
            status_code=404, mimetype="application/json",
        )

    stmt = json.loads(stmt_str) if isinstance(stmt_str, str) else stmt_str
    action, corrections = parse_card_submission(payload, stmt)

    return func.HttpResponse(
        json.dumps({"action": action, "corrections": corrections}, ensure_ascii=False),
        status_code=200, mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    """Health check endpoint."""
    return func.HttpResponse(
        json.dumps({
            "status": "healthy",
            "version": "2.1.0",
            "schema_version": "1.2.0",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "active_jobs": 0,
        }),
        mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# Pipeline logic (extracted from main_v2.py)
# ---------------------------------------------------------------------------

def _run_pipeline(
    pdf_path: str,
    use_enrichment: bool,
    requested_types: list[str],
    source_file_name: str = "document.pdf",
) -> dict:
    """Run the full hybrid extraction pipeline and return the result dict."""
    from extractor.cu_client import analyze_document
    from extractor.html_table_parser import parse_html_table
    from extractor.enrichment import build_enrichment_lookup, enrich_all_rows
    from extractor.schema_mapper import assemble_v12_statement
    from extractor.statement_detector import (
        _build_page_map,
        _offset_to_page,
        _find_section_end_by_density,
        _NOTES_TERMINATOR_RE,
    )

    source_file_name = source_file_name or Path(pdf_path).name

    # ------------------------------------------------------------------ #
    # Step 1: CU Locator — detect statements and metadata
    # ------------------------------------------------------------------ #
    logging.info("Running CU Locator")
    locator_result = analyze_document("financial-statement-locator", pdf_path)

    all_statements = _parse_locator_statements(locator_result)
    selected = _select_consolidated(all_statements, requested_types)

    # ------------------------------------------------------------------ #
    # Step 2: Get markdown from the locator result
    # ------------------------------------------------------------------ #
    contents = locator_result.get("result", {}).get("contents", [])
    markdown = contents[0].get("markdown", "") if contents else ""
    pages = contents[0].get("pages", []) if contents else []

    # ------------------------------------------------------------------ #
    # Step 3: CU Extractor — enrichment data
    # ------------------------------------------------------------------ #
    enrichment_lookup = {}
    if use_enrichment:
        logging.info("Running CU Extractor for enrichment")
        try:
            extractor_result = analyze_document(
                "financial-statement-extractor", pdf_path
            )
            enrichment_lookup = build_enrichment_lookup(extractor_result)
        except Exception as e:
            logging.warning(f"Enrichment failed, continuing without: {e}")

    # ------------------------------------------------------------------ #
    # Step 4: Parse and assemble each statement
    # ------------------------------------------------------------------ #
    output = {"summary": []}

    for stype in STATEMENT_TYPES:
        meta = selected.get(stype)
        if not meta or stype not in requested_types:
            output["summary"].append({
                "statement_type": stype,
                "status": "not_found" if stype in requested_types else "not_requested",
                "page_range": {"start": None, "end": None},
            })
            output[stype] = None
            continue

        # Locate the HTML table in the markdown
        location = _locate_table_in_markdown(
            stype, meta, markdown, pages,
        )

        if not location:
            logging.warning(f"{stype}: could not locate in markdown")
            output["summary"].append({
                "statement_type": stype,
                "status": "found_but_not_extracted",
                "page_range": {"start": meta.get("page_start"), "end": meta.get("page_end")},
            })
            output[stype] = None
            continue

        # Parse the HTML table
        rows, columns, cells = parse_html_table(
            markdown, location["md_offset"], location["md_end_offset"]
        )

        if not cells:
            logging.warning(f"{stype}: no cells extracted")
            output["summary"].append({
                "statement_type": stype,
                "status": "found_but_empty",
                "page_range": {"start": location["start_page"], "end": location["end_page"]},
            })
            output[stype] = None
            continue

        # Assemble v1.2 JSON (includes enrichment + validation)
        v12_doc = assemble_v12_statement(
            statement_type=stype,
            locator_metadata=meta,
            parser_rows=rows,
            parser_columns=columns,
            parser_cells=cells,
            enrichment_lookup=enrichment_lookup,
            source_file_name=source_file_name,
        )

        output["summary"].append({
            "statement_type": stype,
            "status": "extracted",
            "page_range": {
                "start": location["start_page"],
                "end": location["end_page"],
            },
            "row_count": len(v12_doc["rows"]),
            "column_count": len(v12_doc["columns"]),
            "validation_status": v12_doc["validation"]["status"],
        })
        output[stype] = v12_doc

        logging.info(
            f"{stype}: {len(v12_doc['rows'])} rows, "
            f"{len(v12_doc['columns'])} columns, "
            f"validation={v12_doc['validation']['status']}"
        )

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

    return output


# ---------------------------------------------------------------------------
# Helpers (from main_v2.py)
# ---------------------------------------------------------------------------

def _parse_locator_statements(locator_result: dict) -> list[dict]:
    """Extract statement metadata from the CU locator response."""
    contents = locator_result.get("result", {}).get("contents", [])
    if not contents:
        return []

    fields = contents[0].get("fields", {})
    stmts = fields.get("statements", {}).get("valueArray", [])

    results = []
    for s in stmts:
        props = s.get("valueObject", {})

        def _str(key):
            return props.get(key, {}).get("valueString")

        def _int(key):
            return props.get(key, {}).get("valueInteger")

        def _bool(key):
            return props.get(key, {}).get("valueBoolean")

        results.append({
            "statement_type": _str("statement_type"),
            "title_raw": _str("title_raw"),
            "title_english": _str("title_english"),
            "page_start": _int("page_start"),
            "page_end": _int("page_end"),
            "company_name": _str("company_name"),
            "company_name_raw": _str("company_name_raw"),
            "report_language": _str("report_language"),
            "currency": _str("currency"),
            "unit": _str("unit"),
            "is_consolidated": _bool("is_consolidated"),
            "accounting_standard": _str("accounting_standard"),
        })

    return results


def _select_consolidated(
    statements: list[dict],
    requested_types: list[str],
) -> dict[str, dict]:
    """Select the first consolidated instance of each requested statement type."""
    selected = {}
    for s in statements:
        stype = s.get("statement_type")
        if stype not in requested_types or stype in selected:
            continue
        if s.get("is_consolidated") is True or stype not in selected:
            selected[stype] = s
    return selected


def _locate_table_in_markdown(
    statement_type: str,
    locator_meta: dict,
    markdown: str,
    pages: list[dict],
) -> dict | None:
    """Find the precise markdown offset for a statement's HTML table."""
    from extractor.statement_detector import _build_page_map, _offset_to_page

    page_map = _build_page_map(pages)

    heading_patterns = {
        "balance_sheet": [
            "合并资产负债表", "連結貸借対照表",
            "CONDENSED CONSOLIDATED BALANCE SHEET",
            "CONSOLIDATED BALANCE SHEET", "BALANCE SHEET",
        ],
        "income_statement": [
            "合并利润表", "連結損益計算書",
            "CONDENSED CONSOLIDATED STATEMENTS OF INCOME",
            "CONSOLIDATED STATEMENTS OF INCOME",
            "CONSOLIDATED STATEMENT OF INCOME", "INCOME STATEMENT",
        ],
        "cash_flow": [
            "合并现金流量表", "連結キャッシュ・フロー計算書",
            "CONDENSED CONSOLIDATED STATEMENTS OF CASH",
            "CONSOLIDATED STATEMENTS OF CASH",
            "CONSOLIDATED STATEMENT OF CASH", "CASH FLOW",
        ],
    }

    # Find heading followed by <table>
    direct_offset = None
    for pattern in heading_patterns.get(statement_type, []):
        for m in re.finditer(re.escape(pattern), markdown, re.IGNORECASE):
            lookahead = markdown[m.start():m.start() + 500]
            if "<table>" in lookahead.lower():
                direct_offset = m.start()
                break
        if direct_offset is not None:
            break

    # Fallback: try locator's title_raw
    if direct_offset is None:
        title_raw = locator_meta.get("title_raw", "")
        if title_raw:
            for prefix_len in [40, 20, 10]:
                prefix = title_raw.strip()[:prefix_len]
                idx = markdown.find(prefix)
                if idx >= 0 and "<table>" in markdown[idx:idx + 500].lower():
                    direct_offset = idx
                    break

    if direct_offset is None:
        return None

    # Find the <table>...</table> block(s) for this statement.
    # Financial tables often span page breaks, producing multiple adjacent
    # <table> elements that belong to the same statement.  We merge them
    # cautiously: max 3 tables (page-break continuations), and stop when
    # the gap between tables contains note headings, other statement
    # headings, or when the next table's content looks unrelated.
    table_start = markdown.find("<table>", direct_offset)
    if table_start < 0:
        return None
    table_end = markdown.find("</table>", table_start)
    if table_end < 0:
        return None
    end_offset = table_end + len("</table>")

    # Build set of headings belonging to OTHER statement types
    other_headings: list[str] = []
    for other_type, patterns in heading_patterns.items():
        if other_type != statement_type:
            other_headings.extend(patterns)

    # Patterns that signal we've left the statement and entered notes/other
    # sections.  These appear in the gap text between </table> and <table>.
    _NOTE_HEADING_RE = re.compile(
        r"\b\d{1,2}\.\s+[A-Z]"                   # numbered notes: "1. Accounting", "5. Tax"
        r"|\bNote\s+\d"                            # "Note 1", "Note 2"
        r"|\bNotes to\b"                           # "Notes to the Financial Statements"
        r"|Statement of Changes"                   # equity changes
        r"|Comprehensive Income"                   # OCI statement
        r"|Directors[''']?\s*Report"               # UK filings
        r"|Accounting Policies"                    # notes section
        r"|Significant Accounting"                 # notes section
        , re.IGNORECASE
    )

    # Vocabulary that does NOT belong in a balance sheet / income / cash flow
    # continuation table.  If the next table's first data cells contain these,
    # it's a notes table, not a continuation.
    _NON_CONTINUATION_LABELS = {
        "balance_sheet": [
            "administrative expenses", "wages and salaries", "revenue",
            "cost of sales", "operating profit", "profit for",
            "depreciation", "amortisation", "amortization",
            "directors' emoluments", "directors emoluments",
            "pension cost", "social security", "audit fee",
            "tax charge", "corporation tax", "deferred tax",
            "dividend", "interest payable", "interest receivable",
        ],
        "income_statement": [
            "total assets", "total liabilities", "total equity",
            "cash and cash equivalents", "trade debtors", "trade creditors",
            "fixed assets", "current assets", "creditors",
        ],
        "cash_flow": [
            "total assets", "total liabilities", "total equity",
            "trade debtors", "trade creditors", "fixed assets",
            "share capital", "retained earnings",
        ],
    }

    MAX_CONTINUATION_TABLES = 3  # balance sheet rarely spans more than 3 pages
    tables_merged = 1

    while tables_merged < MAX_CONTINUATION_TABLES:
        remaining = markdown[end_offset:]
        next_table = remaining.find("<table>")
        if next_table < 0:
            break

        gap_text = remaining[:next_table]

        # Stop 1: another statement type heading in the gap
        hit_other = False
        for h in other_headings:
            if h.upper() in gap_text.upper():
                hit_other = True
                break
        if hit_other:
            break

        # Stop 2: note headings or section dividers in the gap
        if _NOTE_HEADING_RE.search(gap_text):
            break

        # Stop 3: large gap (> 500 chars of non-whitespace) suggests a
        # section break, not a page-break continuation
        gap_stripped = re.sub(r"\s+", "", gap_text)
        if len(gap_stripped) > 500:
            break

        # Stop 4: sniff the next table's content — if its first few <td>
        # labels contain vocabulary that doesn't belong in this statement
        # type, it's a notes table, not a continuation.
        next_table_abs = end_offset + next_table
        next_close = remaining.find("</table>", next_table)
        if next_close < 0:
            break
        next_table_html = remaining[next_table:next_close]
        first_cells = re.findall(
            r"<td[^>]*>(.*?)</td>", next_table_html[:2000],
            re.DOTALL | re.IGNORECASE,
        )
        first_labels = " ".join(
            re.sub(r"<[^>]+>", "", c).strip().lower()
            for c in first_cells[:6]
        )
        bad_labels = _NON_CONTINUATION_LABELS.get(statement_type, [])
        if any(bl in first_labels for bl in bad_labels):
            break

        # This table is a legitimate continuation — extend
        end_offset = end_offset + next_close + len("</table>")
        tables_merged += 1

    start_page = _offset_to_page(direct_offset, page_map) if page_map else 1
    end_page = _offset_to_page(end_offset, page_map) if page_map else start_page

    return {
        "md_offset": direct_offset,
        "md_end_offset": end_offset,
        "start_page": start_page,
        "end_page": end_page,
    }

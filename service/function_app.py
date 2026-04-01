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
        "companyName": company_name or "Unknown_Company",
        "summary": json.dumps(result.get("summary", []), ensure_ascii=False),
        "balanceSheet": json.dumps(result.get("balance_sheet"), ensure_ascii=False),
        "incomeStatement": json.dumps(result.get("income_statement"), ensure_ascii=False),
        "cashFlow": json.dumps(result.get("cash_flow"), ensure_ascii=False),
        "confidence": json.dumps(result.get("confidence", {}), ensure_ascii=False),
    }


def _run_job(job_id: str, tmp_path: str, use_enrichment: bool,
             requested_types: list[str], file_name: str):
    """Run extraction in a background thread. Stores result in blob storage and Dataverse."""
    try:
        result = _run_pipeline(tmp_path, use_enrichment, requested_types, file_name)
        payload = _build_response_payload(result)

        # Write to Dataverse (before marking as completed)
        _write_to_dataverse(job_id, result, file_name=file_name)

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


def _write_to_dataverse(job_id: str, result: dict, file_name: str = ""):
    """Parse extraction result and write to Dataverse tables."""
    from extractor.dataverse_parser import parse_job_row, parse_statement_row, parse_line_item_rows
    from extractor.dataverse_client import write_to_dataverse

    if not os.environ.get("DATAVERSE_URL"):
        logging.info("DATAVERSE_URL not set, skipping Dataverse write")
        return

    job_row = parse_job_row(job_id, result, file_name=file_name)

    # Pre-compute confidence results per statement for line item flagging
    confidence_results = result.get("confidence", {})

    statement_rows = []
    line_items = []

    for stype in STATEMENT_TYPES:
        stmt = result.get(stype)
        if not stmt or not isinstance(stmt, dict):
            continue
        stmt_row = parse_statement_row(stype, stmt)
        stmt_row["cree1_jobid"] = job_id
        statement_rows.append((stype, stmt_row))

        # Get confidence result for this statement (from pipeline confidence output)
        conf_result = confidence_results.get(stype) if isinstance(confidence_results, dict) else None

        for item in parse_line_item_rows(stype, stmt, confidence_result=conf_result):
            item["cree1_jobid"] = job_id
            line_items.append((stype, item))

    write_to_dataverse(job_id, job_row, statement_rows, line_items)
    logging.info(f"Job {job_id}: wrote {len(statement_rows)} statements, {len(line_items)} line items to Dataverse")


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
# POST /extract-by-url — URL-based extraction (bypasses CPS 5 MB connector limit)
# ---------------------------------------------------------------------------

@app.route(route="extract-by-url", methods=["POST"])
def extract_by_url(req: func.HttpRequest) -> func.HttpResponse:
    """
    URL-based extraction endpoint. Accepts a download URL for the PDF instead
    of the file bytes, bypassing the CPS 5 MB connector payload limit.

    Body: { "fileUrl": "...", "fileName": "...", "authToken": "..." }
    Returns: { "jobId": "...", "status": "processing" }
    """
    logging.info("Extract-by-url request received")

    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400, mimetype="application/json",
        )

    file_url = body.get("fileUrl") or body.get("contentUrl")
    file_name = body.get("fileName", "document.pdf")
    auth_token = body.get("authToken", "")

    if not file_url:
        return func.HttpResponse(
            json.dumps({"error": "Missing fileUrl"}),
            status_code=400, mimetype="application/json",
        )

    # --- Parse optional query params ---
    use_enrichment = req.params.get("enrichment", "true").lower() != "false"
    requested_types = req.params.get("statements", "")
    if requested_types:
        requested_types = [s.strip() for s in requested_types.split(",")]
    else:
        requested_types = STATEMENT_TYPES

    # --- Download PDF from URL ---
    try:
        import requests as http_requests
        headers = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        download_resp = http_requests.get(file_url, headers=headers, timeout=120)
        download_resp.raise_for_status()
        pdf_bytes = download_resp.content
        logging.info(f"Downloaded {file_name} from URL ({len(pdf_bytes)} bytes)")
    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": f"Failed to download PDF from URL: {str(e)}"}),
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
        logging.info(f"Job {job_id} started for {file_name} (via URL)")

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
        logging.exception("Failed to start extraction job (via URL)")
        return func.HttpResponse(
            json.dumps({"error": f"Failed to start job: {str(e)}"}),
            status_code=500, mimetype="application/json",
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
                "companyName": "",
                "summary": "",
                "balanceSheet": "",
                "incomeStatement": "",
                "cashFlow": "",
                "confidence": "",
            }),
            status_code=200,
            mimetype="application/json",
        )

    if job["status"] == "completed":
        result = job["result"]
        result["status"] = "completed"
        result["jobId"] = job_id
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

    Session-state driven: corrections are accumulated inside sessionState
    across all reviewed statements. The topic sends jobId + sessionState.

    Body: { "jobId": "...", "sessionState": "..." }
    Returns: corrected statement JSONs (camelCase keys, clean v1.2).
    """
    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400, mimetype="application/json",
        )

    job_id = body.get("jobId")
    session_state_str = body.get("sessionState", "{}")

    if not job_id:
        return func.HttpResponse(
            json.dumps({"error": "Missing jobId"}),
            status_code=400, mimetype="application/json",
        )

    # Extract corrections from session state
    try:
        state = json.loads(session_state_str) if session_state_str else {}
    except (json.JSONDecodeError, TypeError):
        return func.HttpResponse(
            json.dumps({"error": "Malformed sessionState"}),
            status_code=400, mimetype="application/json",
        )
    corrections = state.get("corrections", {})

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
        stmt = _parse_stmt_from_result(result, stype)
        stmt_corrections = corrections.get(stype, {})
        if stmt and stmt_corrections:
            stmt = _apply(stmt, stmt_corrections)
        # Write back using camelCase key
        camel_key = _SNAKE_TO_CAMEL.get(stype, stype)
        result[camel_key] = json.dumps(stmt, ensure_ascii=False) if stmt else "null"

    # Build final payload (camelCase keys)
    payload = {
        "status": "corrected",
        "companyName": result.get("companyName") or result.get("company_name", "Unknown_Company"),
        "summary": result.get("summary", "[]"),
        "balanceSheet": result.get("balanceSheet") or result.get("balance_sheet", "null"),
        "incomeStatement": result.get("incomeStatement") or result.get("income_statement", "null"),
        "cashFlow": result.get("cashFlow") or result.get("cash_flow", "null"),
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

# Key mapping: connector camelCase → blob store snake_case for statement keys
_CAMEL_TO_SNAKE = {
    "balanceSheet": "balance_sheet",
    "incomeStatement": "income_statement",
    "cashFlow": "cash_flow",
}
_SNAKE_TO_CAMEL = {v: k for k, v in _CAMEL_TO_SNAKE.items()}


def _parse_stmt_from_result(result: dict, stype_snake: str):
    """Load a statement dict from the (possibly camelCase-keyed) result blob."""
    camel_key = _SNAKE_TO_CAMEL.get(stype_snake, stype_snake)
    stmt_str = result.get(stype_snake) or result.get(camel_key)
    if not stmt_str or stmt_str == "null":
        return None
    return json.loads(stmt_str) if isinstance(stmt_str, str) else stmt_str


@app.route(route="build-review-card", methods=["POST"])
def build_review_card(req: func.HttpRequest) -> func.HttpResponse:
    """
    Generate an Adaptive Card JSON payload for HITL review.

    Session-state driven: the topic sends jobId + sessionState (opaque JSON
    string). On the very first call sessionState is empty — the backend
    creates it from confidence data and returns the navigator card. On
    subsequent calls the session state tells us which card to build.

    Body: { "jobId": "...", "sessionState": "..." }
    Returns: { "cardJson": "...", "sessionState": "..." }
    """
    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400, mimetype="application/json",
        )

    from extractor.card_builder import (
        build_navigator_card,
        build_statement_review_card,
        init_session_state,
    )

    job_id = body.get("jobId")
    session_state_str = body.get("sessionState", "")

    # Load cached job
    job = _load_job(job_id)
    if not job or job.get("status") != "completed":
        return func.HttpResponse(
            json.dumps({"error": f"Job {job_id} not found"}),
            status_code=404, mimetype="application/json",
        )

    result = job.get("result", {})

    # Parse confidence (needed for init and statement cards)
    confidence_str = result.get("confidence") or "{}"
    confidence = json.loads(confidence_str) if isinstance(confidence_str, str) else confidence_str

    # ── First call: initialise session state, return navigator ──
    if not session_state_str or session_state_str.strip() in ('{}', '"{}"'):
        # Determine which statements actually have data (not null/empty)
        available = [
            stype for stype in STATEMENT_TYPES
            if _parse_stmt_from_result(result, stype) is not None
        ]
        session_state_str = init_session_state(
            confidence, job_id=job_id, available_statements=available,
        )

        # Extract metadata from first available statement
        company_name = result.get("companyName") or result.get("company_name", "Unknown")
        currency = "USD"
        unit = "ones"
        for stype in STATEMENT_TYPES:
            stmt = _parse_stmt_from_result(result, stype)
            if stmt and isinstance(stmt, dict):
                meta = stmt.get("statement_metadata", {})
                currency = meta.get("currency", currency)
                unit = meta.get("unit", unit)
                break

        summary_str = result.get("summary", "[]")
        summary = json.loads(summary_str) if isinstance(summary_str, str) else summary_str

        card = build_navigator_card(company_name, currency, unit, confidence, summary, job_id=job_id)
        return func.HttpResponse(
            json.dumps({"cardJson": json.dumps(card, ensure_ascii=False),
                         "sessionState": session_state_str}),
            status_code=200, mimetype="application/json",
        )

    # ── Subsequent calls: session state tells us what to build ──
    try:
        state = json.loads(session_state_str)
    except (json.JSONDecodeError, TypeError):
        return func.HttpResponse(
            json.dumps({"error": "Malformed sessionState"}),
            status_code=400, mimetype="application/json",
        )
    step = state.get("step", 1)
    statements = state.get("statements", [])
    total_steps = len(statements)

    if step < 1 or step > total_steps:
        return func.HttpResponse(
            json.dumps({"error": f"Invalid step {step} for {total_steps} statements"}),
            status_code=400, mimetype="application/json",
        )

    stype = statements[step - 1]
    stmt = _parse_stmt_from_result(result, stype)
    if not stmt:
        return func.HttpResponse(
            json.dumps({"error": f"Statement {stype} not found in job"}),
            status_code=404, mimetype="application/json",
        )

    confidence_entry = confidence.get(stype, {"score": 0, "level": "low", "flagged_rows": []})
    corrections_for_stmt = state.get("corrections", {}).get(stype, {})

    card = build_statement_review_card(
        statement_type=stype,
        statement_json=stmt,
        confidence_entry=confidence_entry,
        corrections=corrections_for_stmt,
        step_num=step,
        total_steps=total_steps,
        editable=state.get("editable", False),
        edit_all=state.get("editAll", False),
        edit_all_page=state.get("editAllPage", 0),
    )

    return func.HttpResponse(
        json.dumps({"cardJson": json.dumps(card, ensure_ascii=False),
                     "sessionState": session_state_str}),
        status_code=200, mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# POST /parse-card-submission — advance session state from Adaptive Card payload
# ---------------------------------------------------------------------------

@app.route(route="parse-card-submission", methods=["POST"])
def parse_card_submission_route(req: func.HttpRequest) -> func.HttpResponse:
    """
    Parse an Adaptive Card submission and advance the session state machine.

    The topic sends two values: the opaque sessionState string and the raw
    Adaptive Card payload string. The backend resolves the current statement
    from sessionState, diffs corrections, and returns a topic-facing action
    (continue / done / skip) plus the updated sessionState.

    Body: { "sessionState": "...", "payload": "..." }
    Returns: { "action": "continue|done|skip", "sessionState": "..." }
    """
    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400, mimetype="application/json",
        )

    from extractor.card_builder import advance_session_state

    session_state_str = body.get("sessionState", "")
    payload_raw = body.get("payload", "{}")

    if not session_state_str:
        return func.HttpResponse(
            json.dumps({"error": "Missing sessionState"}),
            status_code=400, mimetype="application/json",
        )

    # Parse payload (topic sends it as a JSON string)
    if isinstance(payload_raw, str):
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = payload_raw
    # Guard against null/None payload
    if not isinstance(payload, dict):
        payload = {}

    logging.info(f"parse-card-submission: sessionState={session_state_str[:200]}, payload={json.dumps(payload)[:200]}")

    # Resolve the current statement from the session state so we can diff corrections
    try:
        state = json.loads(session_state_str)
    except (json.JSONDecodeError, TypeError):
        return func.HttpResponse(
            json.dumps({"error": "Malformed sessionState"}),
            status_code=400, mimetype="application/json",
        )
    statement_json = None

    try:
        if state.get("phase") == "review":
            step = state.get("step", 0)
            statements = state.get("statements", [])
            if 0 < step <= len(statements):
                stype = statements[step - 1]
                job_id = state.get("jobId") or payload.get("jobId")
                if job_id:
                    job = _load_job(job_id)
                    if job and job.get("status") == "completed":
                        statement_json = _parse_stmt_from_result(job.get("result", {}), stype)
                if statement_json is None:
                    logging.warning(
                        "parse-card-submission: could not load statement for "
                        f"step={step} — corrections will not be captured"
                    )

        topic_action, updated_state = advance_session_state(session_state_str, payload, statement_json)

        return func.HttpResponse(
            json.dumps({"action": topic_action, "sessionState": updated_state}, ensure_ascii=False),
            status_code=200, mimetype="application/json",
        )
    except Exception as e:
        logging.exception(f"parse-card-submission: unhandled error: {e}")
        return func.HttpResponse(
            json.dumps({"error": f"Internal error: {str(e)}"}),
            status_code=500, mimetype="application/json",
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
# GET /api/timeout-test?sleep=<seconds>
# ---------------------------------------------------------------------------

@app.route(route="timeout-test", methods=["GET"])
def timeout_test(req: func.HttpRequest) -> func.HttpResponse:
    """
    Test endpoint to determine the exact connector timeout.
    Call with ?sleep=30, ?sleep=60, ?sleep=120, etc.
    Returns the actual elapsed time if the response gets through.
    """
    sleep_seconds = int(req.params.get("sleep", "10"))
    start = time.time()
    logging.info(f"timeout-test: sleeping for {sleep_seconds}s ...")
    time.sleep(sleep_seconds)
    elapsed = round(time.time() - start, 2)
    logging.info(f"timeout-test: woke after {elapsed}s")
    return func.HttpResponse(
        json.dumps({
            "status": "ok",
            "requested_sleep": sleep_seconds,
            "actual_elapsed": elapsed,
            "timestamp": datetime.utcnow().isoformat() + "Z",
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

# Final Architecture: Financial Statement Agent

**Last updated:** 2026-03-31

## The Problem
Copilot Studio has a hard ~5MB limit on file uploads through the chat interface. Files >5MB cause a SystemError at the platform level before any topic code runs. Confirmed on all channels (test panel, Teams, M365 Copilot).

## The Solution
A hybrid AI agent platform:
- **Code App** (React) — frontend UI, handles what CPS physically can't (large file upload, editable data grids)
- **CPS Agent** (Copilot Studio) — AI brain, handles reasoning, Q&A, analysis, intelligent guidance
- **Power Automate** — file submission + extraction polling (bypasses 5MB limit)
- **Azure Function** (Python) — parses complex extraction JSON, writes to Dataverse
- **Dataverse** — single source of truth, shared state between all components

---

## How It Works

```
User uploads 7MB PDF in Code App
    ↓
Code App calls Flow 1 (Power Automate)
    ↓
Flow 1:
  1. Converts base64 → binary
  2. Calls Financial Extractor API: submitExtraction → gets jobId
  3. Creates ExtractionJob row in Dataverse (status: Extracting)
  4. Polls getExtractionStatus in a Do Until loop (every 10s, up to 10 min)
  5. When complete → calls Azure Function with extraction results
  6. Returns jobId + recordId to Code App
    ↓
Azure Function (Python):
  - Receives: jobId + 3 statement JSONs (incomeStatement, balanceSheet, cashFlow) + metadata
  - Parses the complex nested JSON (v1.2 schema with rows, columns, values, confidence)
  - Writes to Dataverse:
    - Updates ExtractionJob (all metadata: company, language, confidence, currency, etc.)
    - Creates 3 ExtractedStatement rows (with RawStatementJson preserved for Excel generation)
    - Creates 500+ ExtractedLineItem rows (one per line item per period)
  - Returns: success/failure
    ↓
Code App reads from Dataverse → shows HITL review grid
    ↓
Analyst reviews, corrects, approves in the Code App grid
  - During review, analyst chats with CPS Agent for help:
    "Why is this flagged?" → CPS explains
    "Validate balance sheet totals" → CPS computes and checks
    "What should I focus on?" → CPS prioritizes
    ↓
Code App calls Flow 2 → applies corrections → generates Excel workbook
    ↓
User continues chatting with CPS Agent for deep analysis:
  "What is the current ratio?" → CPS calculates from extracted data
  "How has gross margin trended?" → CPS analyzes 3-year trend
  "Summarize key findings" → CPS generates executive summary
```

---

## Components — What Does What

### 1. Code App (React/TypeScript)
**Role:** Frontend UI — the agent's interface
**Tech:** React 19, TypeScript, Tailwind CSS, shadcn/ui, TanStack Table, Zustand
**Deployed to:** Power Platform (Power Apps Code App)

| Screen | Purpose |
|---|---|
| Screen 0: Home Dashboard | Job list from Dataverse, quick stats, new extraction |
| Screen 1: Submit & Ingest | Drag-and-drop file upload (any size), progress spinner, extraction results |
| Screen 2: HITL Review | Side-by-side PDF viewer + editable data grid, flagged items, corrections |
| Screen 3: Analysis & Chat | CPS Agent chat UI, suggested prompts, download workbook |
| Collapsible Chat Panel | Available on all screens for contextual questions to CPS |

### 2. CPS Agent (Copilot Studio / GPT-4.1)
**Role:** AI brain — reasoning, Q&A, analysis, intelligent guidance
**NOT responsible for:** File upload, extraction pipeline, polling, JSON parsing

| Capability | How |
|---|---|
| Financial ratio calculations | LLM computes from extracted data in global variables |
| Trend analysis | LLM compares across periods |
| Cross-statement validation | LLM checks totals, identifies discrepancies |
| HITL suggestions | LLM explains why items are flagged, recommends corrections |
| Anomaly detection | LLM proactively flags issues (margin compression, mismatches) |
| Executive summaries | LLM synthesizes key findings |
| Natural language Q&A | User asks anything, LLM answers from the data |

**Connected to Code App via:** `ExecuteCopilotAsyncV2` (Power Apps SDK)

### 3. Flow 1 (Power Automate) — "Upload and Extract"
**Role:** File submission + extraction polling (bypasses CPS 5MB limit)

| Step | Action |
|---|---|
| 1 | Trigger: Power Apps V2 (fileName + fileContent as base64) |
| 2 | Initialize variable: extractionStatus = "processing" |
| 3 | Compose: base64ToBinary(fileContent) |
| 4 | Call Financial Extractor: submitExtraction (binary + fileName) → jobId |
| 5 | Dataverse: Create ExtractionJob row (status: Extracting) |
| 6 | Do Until loop: poll getExtractionStatus every 10s until complete |
| 7 | Call Azure Function: pass extraction results for parsing |
| 8 | Response: return jobId + recordId |

### 4. Azure Function (Python) — "Process Extraction Results"
**Role:** Parse complex JSON, write to Dataverse
**NOT responsible for:** Polling (Flow 1 does this), file handling

- **Input:** jobId, incomeStatement JSON, balanceSheet JSON, cashFlow JSON, summary, companyName, confidence
- **Parses:** v1.2 schema with document_metadata, statement_metadata, columns[], rows[], values[], validation
- **Writes to Dataverse:**
  - Updates ExtractionJob (25 fields: company, language, currency, confidence, periods, etc.)
  - Creates 3 ExtractedStatement rows (statement metadata + RawStatementJson)
  - Creates 500+ ExtractedLineItem rows (normalized: one row per line item per period)
- **Returns:** success/failure + summary stats

### 5. Dataverse (3 Tables)
**Role:** Single source of truth, shared state between Code App and CPS Agent

| Table | Rows per Job | Purpose |
|---|---|---|
| ExtractionJob | 1 | Job metadata, status, company info, 25 fields |
| ExtractedStatement | 3 | Per-statement metadata, page ranges, RawStatementJson |
| ExtractedLineItem | ~500 | Individual values (normalized: one row per line item per period) for HITL grid |

**Relationships:**
- ExtractionJob (1) → (many) ExtractedStatement
- ExtractionJob (1) → (many) ExtractedLineItem
- ExtractedStatement (1) → (many) ExtractedLineItem

### 6. Flow 2 (Power Automate) — "Apply Corrections & Generate Excel"
**Role:** After HITL approval, generate the final Excel workbook

| Step | Action |
|---|---|
| 1 | Read RawStatementJson from ExtractedStatement |
| 2 | Read corrections from ExtractedLineItem |
| 3 | Call Financial Extractor: applyCorrections |
| 4 | Call Finance Statement Processor flow → generate Excel |
| 5 | Update ExtractionJob: ExcelFileUrl, status = Approved |

### 7. Financial Extractor API (Existing, Azure)
**Host:** fin-statement-extractor.azurewebsites.net
**Operations:** submitExtraction, submitExtractionByUrl, getExtractionStatus, buildReviewCard, parseCardSubmission, applyCorrections

---

## Backend Configuration

The extraction backend is controlled by the `EXTRACTION_BACKEND` environment variable:

| Value | Backend | Description |
|-------|---------|-------------|
| `cu` (default) | Azure Content Understanding | Custom analyzers with financial AI fields |
| `textract` | AWS Textract | Table extraction via boto3 + LLM for metadata |

Both backends produce identical `AnalyzeResult` output — Stages 2-5 are backend-agnostic.

### Textract-Specific Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AWS_ACCESS_KEY_ID` | Yes (or ~/.aws/credentials) | IAM access key |
| `AWS_SECRET_ACCESS_KEY` | Yes (or ~/.aws/credentials) | IAM secret key |
| `AWS_REGION` | Yes | AWS region matching Textract deployment |
| `AWS_S3_BUCKET` | Yes | S3 bucket for temporary PDF upload |
| `AWS_S3_PREFIX` | No | S3 key prefix (default: textract-input) |

---

## What We Reuse vs Build New

| Component | Status |
|---|---|
| Financial Extractor API (all endpoints) | **Reuse** — no changes |
| Financial Extractor custom connector (6 operations) | **Reuse** — added submitExtractionByUrl |
| Finance Statement Processor Cloud Flow (Excel generation) | **Reuse** — no changes |
| Delayed Poll Cloud Flow (timer) | **Reuse** — no changes |
| CPS Agent (instructions, NLU) | **Modify** — updated for Q&A-only role |
| Dataverse tables (3 tables) | **New** — ExtractionJob, ExtractedStatement, ExtractedLineItem |
| Code App (5 screens) | **New** — React/TypeScript |
| Azure Function (Python) | **New** — parse + write to Dataverse |
| Flow 1 (upload + extract + poll) | **New** — Power Automate |
| Flow 2 (corrections + Excel) | **New** — Power Automate |

---

## Dataverse Schema

### ExtractionJob (25 columns)
JobId, FileName, FileUrl, Status, CompanyName, ReportType, ReportLanguage, AccountingStandard, Currency, CurrencyUnit, StatementsFound, TotalLineItems, AvgConfidence, Periods, SummaryJson, ValidationStatus, ExcelFileUrl, SubmittedBy, ProcessingTimeSeconds + system columns

### ExtractedStatement (10+ columns)
StatementTitle, StatementName, JobId, StatementType, PageRangeStart, PageRangeEnd, IsConsolidated, IsAudited, RawStatementJson, ReviewComplete + ExtractionJob (lookup)

### ExtractedLineItem (25 columns)
LineItemName, JobId, RowIndex, RowType, IndentLevel, SectionName, CanonicalKey, CanonicalGroup, LabelRaw, Period, PeriodType, PeriodEndDate, ColumnIndex, ValueRaw, ValueNormalized, ValueKind, AIConfidence, SourcePage, SignHint, AnalystCorrectedValue, ReviewStatus, AnalystNotes, ReviewedBy + ExtractionJob (lookup) + ExtractedStatement (lookup)

---

## HITL Review — How It Works

| Task | Who | How |
|---|---|---|
| Display editable grid | Code App | TanStack Table, reads ExtractedLineItem from Dataverse |
| Show PDF side-by-side | Code App | react-pdf library |
| Highlight flagged rows | Code App | Yellow/orange based on AIConfidence threshold |
| User edits a cell | Code App | Writes AnalystCorrectedValue to Dataverse |
| "Why is this flagged?" | CPS Agent | LLM explains based on confidence, OCR context |
| "Validate totals" | CPS Agent | LLM cross-references line items |
| "Suggest correction" | CPS Agent | LLM reasons from surrounding values |
| Approve all + generate Excel | Code App → Flow 2 | Reads corrections, calls applyCorrections, generates Excel |

---

## Build Phases

| Phase | What | Status |
|---|---|---|
| **1** | Code App scaffold + deploy + Dataverse tables | DONE |
| **2a** | Flow 1 (upload + extract + poll + trigger Azure Function) | IN PROGRESS |
| **2b** | Azure Function (parse + write to Dataverse) | NEXT |
| **2c** | Wire Code App to call Flow 1 | NEXT |
| **2d** | Test end-to-end with 7MB PDF | NEXT |
| **3** | HITL review screen (TanStack Table + PDF viewer) | PENDING |
| **4** | CPS Agent chat in Code App (ExecuteCopilotAsyncV2) | PENDING |
| **5** | Flow 2 (Excel generation) + Polish | PENDING |

---

## Key Decisions Log

| Decision | Reason |
|---|---|
| Power Apps Code App over Canvas App | Full control: drag-and-drop upload, react-pdf, TanStack Table, any npm package |
| CPS for Q&A only, not extraction pipeline | CPS can't handle >5MB files; extraction is deterministic work, not reasoning |
| Flow 1 does polling (not Azure Function) | Flow already has the connector + Do Until loop built |
| Azure Function for JSON parsing | Complex nested JSON (v1.2 schema) is easier to parse in Python than Power Automate expressions |
| Normalized Dataverse schema (one row per value per period) | Handles dynamic number of periods, any period format (2024, Q2 2024, H1 2025) |
| RawStatementJson preserved in ExtractedStatement | Needed for Excel generation (Flow 2) and CPS Q&A |
| Hybrid approach: Code App handles UI, CPS handles intelligence | Each component does what it's best at |

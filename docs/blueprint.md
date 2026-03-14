# Financial Statement Extraction Pipeline (Azure Content Understanding)

## Purpose

Rapidly extract three financial statements from uploaded reports: -
Balance Sheet - Cash Flow Statement - Income Statement

Return structured JSON plus a summary of detection status and page
numbers. Excel rendering will be handled separately (e.g., Copilot
Studio).

------------------------------------------------------------------------

# 1. Ideal Production Architecture

    User Upload
       ↓
    Copilot Studio / Frontend
       ↓
    Backend API / Azure Function
       ↓
    Azure Content Understanding
       ↓
    Raw Extraction JSON
       ↓
    Statement Locator
       ↓
    Layout Reconstructor
       ↓
    English Translation Layer
       ↓
    Summary + 3 Statement JSONs
       ↓
    Copilot Studio renders Excel

## Responsibilities

### Azure Content Understanding

Responsible for: - OCR - Layout detection - Table detection - Page
segmentation - Raw text extraction

### Backend Processing Layer

Responsible for: - Detecting statements - Assigning page numbers -
Reconstructing statement structure - Translating labels into English -
Packaging structured JSON output

### Copilot Studio

Responsible for: - File upload - Orchestration - Rendering Excel from
JSON outputs

------------------------------------------------------------------------

# 2. Quick Testing Architecture

For rapid prototyping, skip Copilot Studio and backend services.

    Local PDF
       ↓
    Python Script
       ↓
    Azure Content Understanding
       ↓
    Raw JSON
       ↓
    Statement Detection
       ↓
    summary.json + statement JSONs

This allows testing in minutes without infrastructure deployment.

------------------------------------------------------------------------

# 3. Minimal Quick Test Steps

### Step 1 --- Send PDF to Azure

Use a simple Python script or curl request to send a PDF to Azure
Content Understanding.

Example request:

    POST <TARGET_URI>
    Headers:
    Ocp-Apim-Subscription-Key: <API_KEY>
    Content-Type: application/pdf

### Step 2 --- Retrieve Result

Azure returns:

    operation-location: <RESULT_URL>

Poll this URL until status is `succeeded`.

Save the JSON as:

    raw_result.json

------------------------------------------------------------------------

# 4. Statement Detection Logic

Search extracted text for key indicators.

## Balance Sheet indicators

-   Balance Sheet
-   Statement of Financial Position
-   Assets
-   Liabilities
-   Equity

## Cash Flow indicators

-   Cash Flow
-   Cash flows from operating activities
-   Investing activities
-   Financing activities

## Income Statement indicators

-   Income Statement
-   Statement of Operations
-   Revenue
-   Net income

------------------------------------------------------------------------

# 5. Example Summary Output

`summary.json`

``` json
{
  "summary": [
    {
      "statement_type": "balance_sheet",
      "status": "extracted",
      "start_page": 9,
      "end_page": 9
    },
    {
      "statement_type": "cash_flow",
      "status": "extracted",
      "start_page": 10,
      "end_page": 10
    },
    {
      "statement_type": "income_statement",
      "status": "not_found",
      "start_page": null,
      "end_page": null
    }
  ]
}
```

------------------------------------------------------------------------

# 6. Statement JSON Structure

Each statement is returned separately.

Example:

    balance_sheet.json
    cash_flow.json
    income_statement.json

Example schema:

``` json
{
  "statement_type": "balance_sheet",
  "status": "extracted",
  "start_page": 9,
  "end_page": 9,
  "title_english": "Condensed Consolidated Balance Sheet",
  "rows": [],
  "columns": [],
  "cells": []
}
```

------------------------------------------------------------------------

# 7. Processing Components

## 1. Document Intake

Accept uploaded document.

Input

    file_name
    mime_type
    base64_content

Output

    document_id
    normalized document object

------------------------------------------------------------------------

## 2. Azure CU Client

Sends document to Azure.

Output:

    raw_result.json

Contains: - pages - lines - tables - paragraphs

------------------------------------------------------------------------

## 3. Statement Locator

Determines:

-   balance sheet presence
-   cash flow presence
-   income statement presence
-   start page
-   end page

------------------------------------------------------------------------

## 4. Statement Segment Extractor

Extracts relevant pages from raw JSON.

Example:

    pages: [6]
    lines: [...]
    tables: [...]

------------------------------------------------------------------------

## 5. Layout Reconstructor

Rebuilds a logical grid structure.

Handles: - headings - subheadings - totals - nested sections - variable
columns

------------------------------------------------------------------------

## 6. Translator

Ensures all text is in English.

Rules:

-   Translate labels only
-   Preserve numbers
-   Preserve formatting
-   Keep original labels alongside translated labels

------------------------------------------------------------------------

## 7. Statement Packager

Build final JSON objects:

    balance_sheet.json
    cash_flow.json
    income_statement.json

------------------------------------------------------------------------

## 8. Summary Builder

Creates:

    summary.json

Used by Excel renderer or UI layer.

------------------------------------------------------------------------

# 8. Status Rules

Allowed statuses:

-   not_found
-   found
-   partially_extracted
-   extracted
-   failed

## Meaning

### not_found

No relevant statement detected.

### found

Heading detected but extraction not complete.

### partially_extracted

Statement detected but structure incomplete.

### extracted

Statement successfully reconstructed and translated.

### failed

Processing error occurred.

------------------------------------------------------------------------

# 9. Final Output Contract

    {
      document_id,
      summary: [],
      balance_sheet: {},
      cash_flow: {},
      income_statement: {}
    }

All three statement objects must always be returned.

Even if status is `not_found`.

------------------------------------------------------------------------

# 10. Key Design Principles

1.  Use Azure CU for **layout and OCR only**
2.  Perform **statement detection in custom logic**
3.  Preserve **original structure**
4.  Translate **labels only**
5.  Keep **processing stateless during prototype**
6.  Separate **extraction logic from Excel rendering**

------------------------------------------------------------------------

# 11. Recommended Next Step

1.  Run quick Python prototype
2.  Confirm statements detected
3.  Validate page numbers
4.  Save raw JSON
5.  Add schema enforcement layer

Once validated, convert prototype to:

-   Azure Function
-   Container API
-   integrated Copilot workflow

------------------------------------------------------------------------

End of blueprint.

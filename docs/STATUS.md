# Project Status — 2026-03-20

## Backend (Azure Function) — Complete & Deployed

199 tests passing. 6 endpoints live at `financial-statement-extractor.azurewebsites.net`:

| Endpoint | Purpose | Status |
|----------|---------|--------|
| POST /api/extract | Upload PDF, start async extraction | Working |
| GET /api/extract/status/{jobId} | Poll for results + confidence | Working |
| POST /api/build-review-card | Generate HITL Adaptive Card JSON | Working |
| POST /api/parse-card-submission | Parse analyst edits into corrections | Working |
| POST /api/apply-corrections | Apply corrections, return final JSON | Working |
| GET /api/health | Health check | Working |

### Key modules
- `extractor/confidence_scorer.py` — 6-signal composite scoring + row flagging (69 tests)
- `extractor/corrections.py` — applies analyst edits, re-parses values (22 tests)
- `extractor/card_builder.py` — generates editable Adaptive Cards (24 tests)
- `extractor/html_table_parser.py` — per-table header extraction, currency/unit detection
- `extractor/enrichment.py` — UK GAAP + IFRS + Chinese label section inference

### Tested with
- Meta Platforms Q4 2025 (US GAAP) — 3/3 statements, BS high confidence
- Global Reach Partners 2025 (UK GAAP) — 1/3 statements (BS only)
- Xiamen ITG Group (Chinese) — 3/3 statements, translation working

## Copilot Studio Agent — In Progress

Working in Contoso (default) environment.

### Done
- Custom connector updated with 6 operations
- Document ProcessorV2 topic (submit + poll + routes to HITL)
- HITL Review topic (basic text-based triage + Excel generation)
- 3 HITL tools onboarded (BuildReviewCard, ParseCardSubmission, ApplyCorrections)

### Next Steps
1. Step 2 UX: Triage Adaptive Card with [Review & Edit] / [Proceed to Excel] buttons
2. Step 3: Wire editable Adaptive Cards for flagged items
3. Step 4: Full statement view
4. End-to-end testing in Copilot Studio
5. FDE environment cleanup

## Architecture

```
PDF Upload → Azure Function (CU Locator + Extractor + Confidence Scorer)
  → Copilot Studio (Document Processor topic: submit + poll)
  → HITL Review topic (triage card → review cards → apply corrections)
  → Power Automate Flow (Office Script → Excel)
  → Download link in chat
```

"""
AI Agent Evaluation Report Generator
Generates an Excel report with evaluation results across 5 AI agents,
showing 3 iterative rounds of evaluation per agent with a summary dashboard.
"""

import os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
from openpyxl.utils import get_column_letter

# ──────────────────────────────────────────────────────────────────────
# Color Palette
# ──────────────────────────────────────────────────────────────────────
HEADER_FILL = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
ALT_ROW_FILL = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
ROUND_HEADER_FILL = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")
ROUND_HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="1F4E79")
GREEN_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
ORANGE_FILL = PatternFill(start_color="FFE699", end_color="FFE699", fill_type="solid")
TITLE_FONT = Font(name="Calibri", size=14, bold=True, color="1F4E79")
SUBTITLE_FONT = Font(name="Calibri", size=11, italic=True, color="404040")
THIN_BORDER = Border(
    bottom=Side(style="thin", color="B0B0B0"),
)
THICK_BORDER = Border(
    bottom=Side(style="medium", color="1F4E79"),
)
WRAP_ALIGNMENT = Alignment(wrap_text=True, vertical="top")
CENTER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)


# ──────────────────────────────────────────────────────────────────────
# Evaluation Data Definitions
# ──────────────────────────────────────────────────────────────────────

AGENTS = [
    # ── Agent 1: DBS External Email Analyzer ──
    {
        "customer": "DBS Bank",
        "agent_name": "External Email Analyzer",
        "sheet_name": "DBS - Email Analyzer",
        "eval_method": "Copilot Studio (Capability Use + Compare Meaning) + External using Copilot Chat as Judge",
        "summary_metric": "Combined Score (CS + External)",
        "summary_baseline": 0.72,
        "summary_final": 0.94,
        "summary_target": 0.95,
        "summary_insight": "Copilot Studio Capability Use validated conversation flow at 97%. External evaluation validated AI classification at 90%. Combined score reached 94%.",
        "summary_learning": "1. CS and external evaluations can mask each other's failures when combined into a single metric. The CS layer passed at 97% while external classification lagged at 90% — a combined score hid the external gap until we broke it down.\n2. Email signatures are a persistent noise source. Dates, names, and action-like phrases in signatures polluted deadline detection, sender identification, and action item extraction across multiple iterations.",
        "summary_note": "",
        "metric_definition": "Combined Accuracy is the average of two scores: the Copilot Studio pass rate (across Capability Use and Compare Meaning test cases) and the external classification accuracy (AI Builder output compared against ground truth labels using Copilot Chat as judge).",
        "eval_rounds": [
            {
                "round_name": "Baseline",
                "dataset": "CS: 40 test cases (Capability Use + Compare Meaning).\nExternal: 25 synthetic emails judged via Copilot Chat against ground truth.",
                "metrics": [
                    {
                        "name": "Combined Accuracy (CS + External)",
                        "value": 0.72,
                        "target": 0.95,
                        "finding": "CS layer scored 76%:\n- Agent failed on indirect triggers ('brief me on my inbox')\n- Time expressions like 'last quarter' not recognised\n\nExternal layer scored 67%:\n- Priority defaulted to Medium for 38% of emails\n- Deadline detection had false positives from signature dates\n- Internal/external flag checked body content instead of sender domain",
                        "action": "- Added trigger phrases for indirect email requests\n- Added priority-signal phrases with few-shot examples per level\n- Changed flagging to sender-domain only",
                        "outcome": "Combined improved to 80%.",
                    },
                ],
            },
            {
                "round_name": "Iteration 1",
                "dataset": "CS: 45 test cases (added time expression edge cases).\nExternal: same 25 emails re-assessed after prompt changes.",
                "metrics": [
                    {
                        "name": "Combined Accuracy (CS + External)",
                        "value": 0.80,
                        "target": 0.95,
                        "finding": "CS layer improved to 88%:\n- Trigger phrases working\n- Time conversion failed for hours ('last 36 hours')\n- No error handling for out-of-range inputs ('200 days')\n\nExternal layer improved to 73%:\n- Priority reached 80% with few-shot examples\n- Flagging issue resolved\n- Deadline detection still poor on implicit and relative dates",
                        "action": "- Added hours-to-days conversion rules\n- Added input validation for empty and out-of-range values\n- Implemented date-context extraction with signature filtering\n- Added relative date resolution using email timestamp",
                        "outcome": "Combined improved to 88%.",
                    },
                ],
            },
            {
                "round_name": "Iteration 2",
                "dataset": "CS: 50 test cases (added misspellings, short phrases).\nExternal: 30 emails (added forwarded chains, calendar invites).",
                "metrics": [
                    {
                        "name": "Combined Accuracy (CS + External)",
                        "value": 0.88,
                        "target": 0.95,
                        "finding": "CS layer reached 94%:\n- Capability Use at 96%, Compare Meaning at 92%\n- All time expressions handled\n- Error messages for edge cases not semantically matching expected responses\n\nExternal layer reached 81%:\n- Deadline F1 improved to 78%\n- Compliance emails still misclassified as high urgency\n- Action items miss secondary requests in long threads",
                        "action": "- Refined error message wording to match expected responses\n- Added domain-specific priority rules for compliance and legal emails\n- Implemented thread decomposition for action item extraction",
                        "outcome": "Combined improved to 94%.",
                    },
                ],
            },
            {
                "round_name": "Final",
                "dataset": "CS: 60 test cases (added adversarial + end-to-end).\nExternal: 30 emails (added ambiguous priority, non-English dates).",
                "metrics": [
                    {
                        "name": "Combined Accuracy (CS + External)",
                        "value": 0.94,
                        "target": 0.95,
                        "finding": "CS layer scored 97%:\n- Capability Use 99%, Compare Meaning 95%\n\nExternal layer scored 90%:\n- Priority 91%, Internal/External 97%, Deadline F1 87%, Action Items 82%\n\nCombined 94%, close to 95% target. Gap driven by action item extraction.\n\nLimitations:\n- Multi-intent messages ignored second intent\n- Small sample (30 emails); expect 5-8% variance on unseen data\n- Same test set reused across iterations",
                        "action": "- Documented known limitations\n- Added locale-aware date parsing\n- Action item extraction identified as priority for next cycle",
                        "outcome": "Final combined accuracy is 94%, close to the 95% target.",
                    },
                ],
            },
        ],
    },

    # ── Agent 2: DBS Email Classifier Agent ──
    {
        "customer": "DBS Bank",
        "agent_name": "Email Classifier Agent",
        "sheet_name": "DBS - Email Classifier",
        "eval_method": "Copilot Studio (Keyword Match)",
        "summary_metric": "Classification Accuracy (Keyword Match)",
        "summary_baseline": 0.78,
        "summary_final": 0.90,
        "summary_target": 0.95,
        "summary_insight": "Clearer classification guidelines eliminated category overlaps and hallucinations. Fallback AI prompt improved borderline case handling, boosting accuracy from 84% to 90%.",
        "summary_learning": "1. Category definitions had more impact than any model-level change. Rewriting the classification guidelines with explicit boundaries improved accuracy by 6 points — more than the AI prompt fallback added later.\n2. The gap between 90% and 95% is disproportionately harder. The remaining 5 errors include 3 where human reviewers themselves disagree, suggesting diminishing returns without rethinking the category taxonomy.",
        "summary_note": "",
        "metric_definition": "Classification Accuracy is measured using Copilot Studio's Keyword Match test method. Each test email has a pre-labeled expected category. The agent's response is checked for the presence of the correct category keyword. Accuracy is the percentage of emails where the correct keyword was matched.",
        "eval_rounds": [
            {
                "round_name": "Baseline",
                "dataset": "50 real emails from customer's shared mailbox, pre-labeled with ground truth classifications.",
                "metrics": [
                    {
                        "name": "Classification Accuracy (Keyword Match)",
                        "value": 0.78,
                        "target": 0.95,
                        "finding": "- Classification criteria had significant overlaps between categories\n- Agent selected wrong label when email content matched multiple categories\n- Agent hallucinated new labels for emails that did not fit any predefined category\n- No fallback to a default category for ambiguous inputs",
                        "action": "- Rewrote each category with explicit inclusion/exclusion criteria\n- Added boundary examples for overlapping categories\n- Added instruction to never generate labels outside predefined set\n- Introduced 'General / Unclassified' fallback category",
                        "outcome": "Accuracy improved to 84%.",
                    },
                ],
            },
            {
                "round_name": "Iteration 1",
                "dataset": "Same 50 customer-provided emails re-evaluated after guideline updates.",
                "metrics": [
                    {
                        "name": "Classification Accuracy (Keyword Match)",
                        "value": 0.84,
                        "target": 0.95,
                        "finding": "- Category overlap largely resolved; hallucination eliminated\n- 16% of emails still misclassified\n- Remaining errors in borderline emails needing deeper contextual understanding\n- Common pattern: emails with vocabulary from multiple categories but clear primary intent that agent failed to prioritise",
                        "action": "- Added fallback AI prompt for borderline cases\n- Secondary prompt re-evaluates against top-2 candidate categories with explicit reasoning\n- Acts as self-correction layer when primary confidence is low",
                        "outcome": "Accuracy improved to 90%.",
                    },
                ],
            },
            {
                "round_name": "Final",
                "dataset": "Same 50 emails re-evaluated with fallback AI prompt enabled.",
                "metrics": [
                    {
                        "name": "Classification Accuracy (Keyword Match)",
                        "value": 0.90,
                        "target": 0.95,
                        "finding": "- 45/50 emails correctly classified\n- Fallback AI prompt resolved 3 additional borderline cases\n- Remaining 5 errors: 3 genuinely ambiguous (human reviewers disagree), 2 fallback prompt selected wrong secondary category\n- Same 50 emails reused across iterations; overfitting risk\n- Recommend validating on fresh batch of 25+ emails before production",
                        "action": "- Documented 5 misclassified cases\n- 3 ambiguous cases accepted as human-disagreement level\n- 2 fallback errors identified for further prompt refinement",
                        "outcome": "Final accuracy is 90%, close to 95% target. Gap is in borderline contextual cases.",
                    },
                    {
                        "name": "Workflow Adherence",
                        "value": 0.96,
                        "target": 0.95,
                        "finding": "- 96% of runs followed desired workflow: receive, classify, persist, confirm\n- 2/50 runs skipped persist step when classification confidence was very low\n- Agent went to fallback path without writing result to table",
                        "action": "- Added explicit persist-first instruction\n- Classification result must be written to table before any fallback logic",
                        "outcome": "Final workflow adherence is 96%, exceeds 95% target.",
                    },
                ],
            },
        ],
    },

    # ── Agent 3: Rio Tinto Email Writing Assistant ──
    {
        "customer": "Rio Tinto",
        "agent_name": "Email Writing Assistant",
        "sheet_name": "Rio Tinto - Writing Asst",
        "eval_method": "External (LLM-as-Judge: tone, style, language, personality)",
        "summary_metric": "Style Match Score (LLM-assessed)",
        "summary_baseline": 0.72,
        "summary_final": 0.82,
        "summary_target": 0.85,
        "summary_insight": "Personality card optimization gave the model a structured reference for each user's voice. AI prompt fallback caught and corrected tone drift before final output.",
        "summary_learning": "1. Personality modelling has a hard ceiling without HIL. Highly personal or emotionally nuanced writing styles cannot be fully captured in structured personality cards — the model needs the user's live feedback loop to close the last gap.\n2. Tone drift is topic-dependent, not random. The model consistently reverts to cautious/formal language on sensitive topics (HR, complaints, escalations), even with strong personality cards. This suggests the safety training overrides the persona.",
        "summary_note": "",
        "metric_definition": "Style Match Score is assessed by an LLM judge that compares each generated email against the user's historical emails across four dimensions: tone, style, language, and personality. Each dimension is scored 0 to 100 and averaged into a composite score. The judge uses temperature 0 for consistency.",
        "eval_rounds": [
            {
                "round_name": "Baseline",
                "dataset": "50 generated emails evaluated by LLM judge against user's historical emails. Scored on tone, style, language, personality (0-100 each, averaged).",
                "metrics": [
                    {
                        "name": "Style Match Score (LLM-assessed)",
                        "value": 0.72,
                        "target": 0.85,
                        "finding": "- Generated emails captured general tone but lacked personal voice\n- Personality mismatch: generic corporate language instead of individual quirks (casual users got formal openings, punchy writers got verbose paragraphs)\n- Tone drift on complex topics: model reverted to default cautious/formal tone regardless of user's actual style",
                        "action": "- Extracted personality markers from historical emails (greetings, sign-offs, sentence length, vocabulary, formality)\n- Structured into personality card injected into system prompt\n- Added explicit voice examples for common email types (quick reply, formal request, team update)",
                        "outcome": "Style match improved to 80%.",
                    },
                ],
            },
            {
                "round_name": "Iteration 1",
                "dataset": "Same 50 emails re-generated with personality card applied. Same LLM judge evaluation.",
                "metrics": [
                    {
                        "name": "Style Match Score (LLM-assessed)",
                        "value": 0.80,
                        "target": 0.85,
                        "finding": "- Personality cards effective for standard email types\n- Tone drift still occurring on ~18% of emails\n- Complex/sensitive topics override the personality card with default cautious style",
                        "action": "- Added AI prompt fallback: secondary prompt reviews draft against personality card\n- If tone drift detected, rewrites affected sections while preserving content\n- Acts as self-correction layer before presenting to user",
                        "outcome": "Style match improved to 82%.",
                    },
                ],
            },
            {
                "round_name": "Final",
                "dataset": "Same 50 emails re-generated with personality card + AI prompt fallback. Results reviewed prior to HIL stage.",
                "metrics": [
                    {
                        "name": "Style Match Score (LLM-assessed)",
                        "value": 0.82,
                        "target": 0.85,
                        "finding": "- Fallback caught and corrected drift in 7/9 previously drifted emails\n- 41/50 emails scored above 80% style match\n- Remaining 9 involve highly personal or emotionally nuanced styles difficult to capture in structured personality cards\n- Pre-HIL score; emails presented to user for review before sending\n- LLM judge scores vary 3-5% across runs (non-deterministic)\n- Same 50 prompts reused; unseen prompts may differ",
                        "action": "- Documented edge cases\n- Recommended collecting more historical samples for users with distinctive styles",
                        "outcome": "Final style match is 82%, slightly below 85% target. Ready for HIL review stage.",
                    },
                ],
            },
        ],
    },

    # ── Agent 4: Macquarie Finance Statement Analysis (REAL DATA) ──
    {
        "customer": "Macquarie",
        "agent_name": "Finance Statement Analysis Agent",
        "sheet_name": "Macquarie - Finance Extract",
        "eval_method": "External (Manual verification via Claude Code against source PDFs)",
        "summary_metric": "Overall Extraction Accuracy",
        "summary_baseline": 0.54,
        "summary_final": 0.986,
        "summary_target": 0.98,
        "summary_insight": "Three-step LLM reconciler fixed page-boundary label drops. Subtotal-constrained column alignment solved value misplacement. Schema mapper with enrichment achieved full compliance.",
        "summary_learning": "1. Azure Document Intelligence introduces systematic artifacts (ghost rows, nil-dash drops) that require domain-specific post-processing. Generic OCR cleanup does not work — you need financial-statement-aware reconciliation.\n2. Extraction accuracy is dominated by the weakest statement type. Cash flow improved last because ghost rows and column misalignment compound each other — fixing one without the other yields minimal gain.\n3. Non-English reports expose assumptions baked into every layer. Currency parsing, label matching, and canonical key mapping all assumed English by default and broke independently on Japanese, Chinese, and German reports.",
        "summary_note": "Detailed evaluation is pending. This is based on 5 sample statements shared by the customer.",
        "metric_definition": "Overall Extraction Accuracy is the percentage of extracted rows where both the label and the numerical value are correct, averaged across all three statement types (balance sheet, income statement, cash flow). Each row is verified manually against the source PDF.",
        "eval_rounds": [
            {
                "round_name": "Baseline",
                "dataset": "5 public company reports (Meta, Apple, Rakuten, PetroChina, SAP) evaluated manually using Claude Code. 15 statements, ~500 rows.",
                "metrics": [
                    {
                        "name": "Overall Extraction Accuracy",
                        "value": 0.54,
                        "target": 0.98,
                        "finding": "Balance Sheet (49%):\n- Second comparison period column completely missing\n- Parser only captured first column from multi-period tables\n\nIncome Statement (62%):\n- 5% of labels truncated at page boundaries\n- Yen and Euro symbols caused column shifts in non-USD reports\n\nCash Flow (52%):\n- Ghost rows: 17% of labels affected (labels with no values)\n- Column misalignment: 9% of values in wrong column from dropped nil-dash markers\n\nOverall 54%, averaged across three statements.",
                        "action": "- Fixed multi-period column detection for balance sheet\n- Added currency-aware value parser for income statement\n- Built initial LLM reconciler for cash flow label repair",
                        "outcome": "Overall improved to 72%.",
                    },
                ],
            },
            {
                "round_name": "Iteration 1",
                "dataset": "Same 5 reports re-processed with multi-period detection and currency parser.",
                "metrics": [
                    {
                        "name": "Overall Extraction Accuracy",
                        "value": 0.72,
                        "target": 0.98,
                        "finding": "Balance Sheet (85%):\n- Multi-period columns now detected correctly\n- Some non-English labels unmapped to canonical keys\n\nIncome Statement (78%):\n- Currency parser fixed most value errors\n- EPS section still fragments across pages\n\nCash Flow (55%):\n- Initial reconciler fixed obvious ghost rows\n- Struggled with ambiguous multi-ghost scenarios\n- Column alignment still broken\n\nOverall 72%. Cash flow remains the primary bottleneck.",
                        "action": "- Built subtotal-constrained column alignment solver using section subtotals as arithmetic ground truth\n- Added IFRS phrase lookup for label completion\n- Enhanced LLM reconciler with 3-step approach: suppress noise, match ghost/suspect pairs, complete truncated labels",
                        "outcome": "Overall improved to 88%.",
                    },
                ],
            },
            {
                "round_name": "Iteration 2",
                "dataset": "Same 5 reports re-processed with full LLM reconciler and column alignment solver.",
                "metrics": [
                    {
                        "name": "Overall Extraction Accuracy",
                        "value": 0.88,
                        "target": 0.98,
                        "finding": "Balance Sheet (95%):\n- Both comparison periods extracted reliably\n- Minor label issues on 2 non-English reports\n\nIncome Statement (90%):\n- IFRS phrase lookup handles 80% of truncations, LLM fallback completes the rest\n- Remaining issue: dual-currency columns\n\nCash Flow (81%):\n- Labels improved to 95%, values to 97%\n- 5% label errors from ambiguous multi-ghost scenarios\n\nOverall 88%. Major improvement, but cash flow still below target.",
                        "action": "- Added row index validation to reject out-of-range LLM responses\n- Added batch LLM translation for non-English canonical keys\n- Implemented column position fallback when subtotal constraint unavailable\n- Added dual-currency detection heuristic",
                        "outcome": "Overall improved to 98.6%.",
                    },
                ],
            },
            {
                "round_name": "Present",
                "dataset": "Same 5 reports, all fixes applied. Every row cross-referenced against source PDF.",
                "metrics": [
                    {
                        "name": "Overall Extraction Accuracy",
                        "value": 0.986,
                        "target": 0.98,
                        "finding": "Balance Sheet: 99% (labels 99%, values 100%)\nIncome Statement: 98% (labels 98%, values 100%)\nCash Flow: 98% (labels 98%, values 98%)\nSchema Compliance: 100% (all 15 statements conform to v1.2 schema)\n\nOverall: 98.6% (493/500 rows correct)\n- 7 remaining errors: 3 supplemental disclosures, 2 restated columns, 2 ambiguous IFRS mappings\n\nNote: Only 5 reports evaluated. Accuracy may vary on unseen layouts or reports with >4 columns. Same reports reused across iterations. Recommend expanding to 15+ reports for production validation.",
                        "action": "- Added terminator patterns for supplemental disclosure sections\n- Documented remaining edge cases as known limitations",
                        "outcome": "Final accuracy is 98.6%, exceeds 98% target.",
                    },
                    {
                        "name": "Workflow Adherence",
                        "value": 0.93,
                        "target": 0.95,
                        "finding": "- 93% of runs completed full workflow: upload PDF, extract statements, generate Excel, return link\n- 1/15 extractions failed at Excel generation due to Office Script timeout on large cash flow table (40+ rows)\n- Workflow recovered but output was missing formatting for that statement",
                        "action": "- Added chunked processing for large tables in Office Script\n- Documented timeout threshold",
                        "outcome": "Final workflow adherence is 93%, slightly below 95% target.",
                    },
                ],
            },
        ],
    },

    # ── Agent 5: NSW Health OR Genie Agent ──
    {
        "customer": "NSW Health",
        "agent_name": "OR Genie Agent",
        "sheet_name": "NSW Health - OR Genie",
        "eval_method": "Copilot Studio (Compare Meaning)",
        "summary_metric": "Answer Accuracy (Compare Meaning)",
        "summary_baseline": 0.85,
        "summary_final": 0.962,
        "summary_target": 0.98,
        "summary_insight": "Missing citations fixed by adding document references to SharePoint metadata. Prompt tuning improved multi-step reasoning and scenario-based answers.",
        "summary_learning": "1. Document granularity matters more than prompt complexity. Splitting long policy pages into topic-focused sections improved retrieval accuracy more than any amount of prompt tuning. The agent cannot reason over content it fails to retrieve.\n2. Metadata tags on SharePoint pages are the single highest-leverage change for Copilot Studio RAG agents. Adding topic category, employee type, and cross-reference links resolved both citation and multi-step reasoning failures in one pass.",
        "summary_note": "",
        "metric_definition": "Answer Accuracy is measured using Copilot Studio's Compare Meaning test method. Each test query has an SME-written expected response. The agent's actual response is compared semantically against the expected response. A test case passes if the meaning matches, regardless of exact wording.",
        "eval_rounds": [
            {
                "round_name": "Baseline",
                "dataset": "50 test cases using CS Compare Meaning. 20 factual lookups, 15 multi-step reasoning, 15 complex scenarios.",
                "metrics": [
                    {
                        "name": "Answer Accuracy (Compare Meaning)",
                        "value": 0.85,
                        "target": 0.98,
                        "finding": "- Simple factual queries answered correctly almost every time\n- Citations missing: correct answers but no source SharePoint pages listed\n- Multi-step reasoning gaps: queries needing multiple pages returned partial answers\n- Complex scenario failures: generic topic answers instead of specific condition intersections (e.g., 'casual nurse parental leave' got generic leave info)",
                        "action": "- Added document references and metadata tags to SharePoint pages (topic, employee type, cross-references)\n- Tuned instructions to always cite sources and answer the specific scenario\n- Broke up 3 lengthy policy pages into smaller topic-focused sections",
                        "outcome": "Accuracy improved to 96.2%.",
                    },
                ],
            },
            {
                "round_name": "Final",
                "dataset": "Same 50 test cases re-evaluated after metadata enrichment, prompt tuning, and page restructuring.",
                "metrics": [
                    {
                        "name": "Answer Accuracy (Compare Meaning)",
                        "value": 0.962,
                        "target": 0.98,
                        "finding": "- 48/50 test cases passed (96.2%)\n- All citation issues resolved; agent now consistently lists source documents\n- Metadata tagging fixed multi-step retrieval\n- Page restructuring improved scenario-based answers\n- 2 remaining failures: deeply nested policy exception clauses; one scored as near-miss by evaluator\n- 96.2% is close to but below 98% target\n- Same 50 test cases reused; novel queries and new policy pages not yet validated",
                        "action": "- Documented 2 edge cases\n- Recommended further page splitting for remaining lengthy documents\n- Recommended periodic re-evaluation as knowledge base grows",
                        "outcome": "Final accuracy is 96.2%, close to 98% target. Gap is in deeply nested policy interpretation.",
                    },
                ],
            },
        ],
    },

    # ── Agent 6: DBS NPA Assistant Agent ──
    {
        "customer": "DBS Bank",
        "agent_name": "NPA Assistant Agent",
        "sheet_name": "DBS - NPA Assistant",
        "eval_method": "Manual check + Copilot Studio (Capability Use)",
        "summary_metric": "Workflow Adherence",
        "summary_baseline": 0.62,
        "summary_final": 0.87,
        "summary_target": 0.95,
        "summary_insight": "Fixed JSON parsing errors, AI prompt timeouts, and structured output failures. Reached 87% workflow adherence. The remaining gap is due to edge case timeouts on complex inputs.",
        "summary_learning": "1. Schema validity does not equal semantic correctness. Even with strict JSON schemas and response_format=json_object, the model produced structurally valid but factually wrong outputs — highlighting that format enforcement alone is insufficient.\n2. AI prompt reliability in Copilot Studio degrades non-linearly with input complexity. Simple inputs succeed consistently, but inputs beyond ~3000 tokens hit timeout and parsing failures at a much higher rate than expected. The failure mode is not gradual — it is a cliff.",
        "summary_note": "Only workflow consistency was evaluated.",
        "metric_definition": "Workflow Adherence is the percentage of test runs that completed all steps in the desired end-to-end workflow sequence without errors. A run is counted as adherent only if every step executed in the correct order and produced valid output.",
        "eval_rounds": [
            {
                "round_name": "Baseline",
                "dataset": "30 test scenarios covering full NPA workflow. Manual walkthrough + CS Capability Use to verify action invocation.",
                "metrics": [
                    {
                        "name": "Workflow Adherence",
                        "value": 0.62,
                        "target": 0.95,
                        "finding": "Only 62% of runs completed end-to-end. Four issues identified:\n- AI prompt failing: 15% of inputs returned empty or irrelevant response, stalling workflow\n- JSON parsing errors: 10% of cases returned free-text instead of valid JSON\n- AI prompt timeout: 8% of runs exceeded response time limit on complex inputs\n- Unstructured output: 5% of cases had missing fields or wrong data types",
                        "action": "Focused on highest-impact issue (prompt failures at 15%):\n- Simplified prompt to reduce ambiguity\n- Added explicit output format instructions with JSON template\n- Added retry logic: retry once with simplified input on failure",
                        "outcome": "Workflow adherence improved to 73%.",
                    },
                ],
            },
            {
                "round_name": "Iteration 1",
                "dataset": "Same 30 scenarios re-evaluated after prompt simplification and retry logic.",
                "metrics": [
                    {
                        "name": "Workflow Adherence",
                        "value": 0.73,
                        "target": 0.95,
                        "finding": "- Prompt failures reduced from 15% to 4% (retry catches transient failures)\n- JSON parsing still at 10%: prompt wraps JSON in markdown code blocks or adds explanatory text\n- Timeout unchanged at 8%: complex inputs still exceed limits",
                        "action": "Addressed JSON parsing:\n- Set response_format to json_object for structured output\n- Added pattern-matching fallback to extract JSON from wrapped responses\n- Added schema validation to catch missing fields early",
                        "outcome": "Workflow adherence improved to 82%.",
                    },
                ],
            },
            {
                "round_name": "Iteration 2",
                "dataset": "30 original + 10 complex cases targeting timeouts and edge cases. Total: 40 scenarios.",
                "metrics": [
                    {
                        "name": "Workflow Adherence",
                        "value": 0.82,
                        "target": 0.95,
                        "finding": "- JSON parsing reduced from 10% to 2% (response_format resolved most cases)\n- Timeout decreased to 6% but long inputs with nested conditions still slow\n- Unstructured output fully resolved (schema validation triggers clean retry)",
                        "action": "Addressed timeout:\n- Added input chunking for inputs exceeding token threshold\n- Increased timeout from 30s to 60s\n- Added graceful timeout handling: return partial result with warning instead of failing",
                        "outcome": "Workflow adherence improved to 87%.",
                    },
                ],
            },
            {
                "round_name": "Final",
                "dataset": "All 40 scenarios re-evaluated. Each run manually verified end-to-end.",
                "metrics": [
                    {
                        "name": "Workflow Adherence",
                        "value": 0.87,
                        "target": 0.95,
                        "finding": "35/40 runs completed full workflow. 5 failures:\n- 2 timeouts on inputs >4000 tokens despite chunking (need input size limits or model upgrade)\n- 2 valid JSON but semantically incorrect values (correct structure, wrong content; not catchable by schema validation)\n- 1 retry logic exhausted on transient API error\n\nBelow 95% target. Core workflow stable for standard inputs.\n\nNote: 40 scenarios may not cover all production patterns. Timeout failures are input-length dependent.",
                        "action": "Documented edge cases. Recommended:\n- Input size limit (reject >3000 tokens)\n- Semantic validation via secondary LLM check\n- Circuit breaker for transient API errors with exponential backoff",
                        "outcome": "Final workflow adherence is 87%, below 95% target. Improvement roadmap documented.",
                    },
                ],
            },
        ],
    },
]


# ──────────────────────────────────────────────────────────────────────
# Formatting Utilities
# ──────────────────────────────────────────────────────────────────────

def apply_header_row(ws, row_num, headers):
    """Apply header styling to a row."""
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=row_num, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER_ALIGNMENT
        cell.border = THICK_BORDER


def auto_fit_columns(ws, max_width=60, min_width=12):
    """Auto-fit column widths based on content, capped at max_width."""
    for col_cells in ws.columns:
        col_letter = get_column_letter(col_cells[0].column)
        max_len = min_width
        for cell in col_cells:
            if cell.value:
                # For wrapped text, use a reasonable estimate
                val = str(cell.value)
                lines = val.split("\n")
                longest_line = max(len(line) for line in lines) if lines else 0
                max_len = max(max_len, min(longest_line + 2, max_width))
        ws.column_dimensions[col_letter].width = max_len


def format_pct(value):
    """Format a 0-1 float as a percentage string, or return as-is for ratings."""
    if isinstance(value, (int, float)):
        if 0 < value <= 1.0:
            return f"{value * 100:.1f}%"
        elif value > 1.0:
            return f"{value:.1f}"
    return str(value)


# ──────────────────────────────────────────────────────────────────────
# Sheet Builders
# ──────────────────────────────────────────────────────────────────────

def build_summary_sheet(wb):
    """Build a clean, concise Summary Dashboard sheet."""
    ws = wb.active
    ws.title = "Summary Dashboard"

    # Title
    ws.merge_cells("A1:H1")
    title_cell = ws.cell(row=1, column=1, value="AI Agent Evaluation Report - Summary")
    title_cell.font = TITLE_FONT
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 35

    # Headers with Target, Learnings, and Notes columns
    headers = [
        "Customer", "Agent Name", "Eval Method",
        "Baseline", "Target", "Final", "Key Improvement", "Learnings / Observations", "Notes"
    ]
    header_row = 3
    apply_header_row(ws, header_row, headers)

    # Data rows
    for i, agent in enumerate(AGENTS):
        row = header_row + 1 + i

        ws.cell(row=row, column=1, value=agent["customer"])
        ws.cell(row=row, column=2, value=agent["agent_name"])

        # Eval method — derived from the agent's actual approach
        eval_methods = {
            "External Email Analyzer": "CS Evaluation (Capability Use + Compare Meaning) + External",
            "Email Classifier Agent": "CS Evaluation (Keyword Match)",
            "Email Writing Assistant": "External (LLM-as-Judge)",
            "Finance Statement Analysis Agent": "External (Manual via Claude Code)",
            "OR Genie Agent": "CS Evaluation (Compare Meaning)",
            "NPA Assistant Agent": "Manual check + CS (Capability Use)",
        }
        ws.cell(row=row, column=3, value=eval_methods.get(agent["agent_name"], ""))

        baseline = agent["summary_baseline"]
        target = agent.get("summary_target", 0.90)
        final = agent["summary_final"]

        base_cell = ws.cell(row=row, column=4, value=baseline)
        base_cell.number_format = '0.0%'

        tgt_cell = ws.cell(row=row, column=5, value=target)
        tgt_cell.number_format = '0.0%'

        final_cell = ws.cell(row=row, column=6, value=final)
        final_cell.number_format = '0.0%'

        # Color final: green if >= target, orange if close
        if final >= target:
            ws.cell(row=row, column=6).fill = GREEN_FILL
        elif final >= target * 0.95:
            ws.cell(row=row, column=6).fill = ORANGE_FILL

        insight_cell = ws.cell(row=row, column=7, value=agent["summary_insight"])
        insight_cell.alignment = WRAP_ALIGNMENT

        learning_cell = ws.cell(row=row, column=8, value=agent.get("summary_learning", ""))
        learning_cell.alignment = WRAP_ALIGNMENT

        notes_cell = ws.cell(row=row, column=9, value=agent.get("summary_note", ""))
        notes_cell.alignment = WRAP_ALIGNMENT
        notes_cell.font = Font(name="Calibri", size=9, italic=True, color="666666")

        # Alternating row shading
        if i % 2 == 1:
            for col in range(1, 8):
                ws.cell(row=row, column=col).fill = ALT_ROW_FILL
            # Preserve final color
            if final >= target:
                ws.cell(row=row, column=6).fill = GREEN_FILL
            elif final >= target * 0.95:
                ws.cell(row=row, column=6).fill = ORANGE_FILL

        ws.row_dimensions[row].height = 50

    # Center-align columns 1-6
    for row in range(header_row + 1, header_row + 1 + len(AGENTS)):
        for col in [1, 2, 3, 4, 5, 6]:
            ws.cell(row=row, column=col).alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True
            )

    # Column widths
    ws.column_dimensions["A"].width = 15
    ws.column_dimensions["B"].width = 28
    ws.column_dimensions["C"].width = 30
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 12
    ws.column_dimensions["F"].width = 12
    ws.column_dimensions["G"].width = 50
    ws.column_dimensions["H"].width = 55
    ws.column_dimensions["I"].width = 35


SCOPE_INSIDE_FILL = PatternFill(start_color="DAEEF3", end_color="DAEEF3", fill_type="solid")
SCOPE_OUTSIDE_FILL = PatternFill(start_color="FDE9D9", end_color="FDE9D9", fill_type="solid")
SCOPE_HEADER_FONT = Font(name="Calibri", size=10, bold=True, italic=True, color="1F4E79")
METHODOLOGY_FILL = PatternFill(start_color="F5F5F5", end_color="F5F5F5", fill_type="solid")
METHODOLOGY_FONT = Font(name="Calibri", size=10, color="333333")
METHODOLOGY_BORDER = Border(
    left=Side(style="medium", color="1F4E79"),
    right=Side(style="thin", color="B0B0B0"),
    top=Side(style="thin", color="B0B0B0"),
    bottom=Side(style="thin", color="B0B0B0"),
)


def build_enhanced_agent_sheet(wb, agent):
    """Build an enhanced agent sheet with Inside/Outside CS scope sections and methodology."""
    ws = wb.create_sheet(title=agent["sheet_name"])

    # Title
    ws.merge_cells("A1:K1")
    title_cell = ws.cell(
        row=1, column=1,
        value=f"{agent['customer']} - {agent['agent_name']} | Evaluation Results"
    )
    title_cell.font = TITLE_FONT
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 35

    # Method subtitle
    ws.merge_cells("A2:K2")
    method_cell = ws.cell(row=2, column=1, value=f"Method: {agent.get('eval_method', '')}")
    method_cell.font = Font(name="Calibri", size=11, bold=True, italic=True, color="1F4E79")
    method_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[2].height = 22

    meth_end = 3

    # Headers (enhanced: 11 columns)
    headers = [
        "Eval Round", "Eval Scope", "Eval Dataset",
        "How Measured", "Metric Name", "Value",
        "Target", "Finding / Observation", "Action Taken", "Outcome / Impact",
        "CS Test Method"
    ]
    header_row = meth_end + 1
    apply_header_row(ws, header_row, headers)

    current_row = header_row + 1

    for round_data in agent["eval_rounds"]:
        metrics = round_data["metrics"]

        # Group metrics by eval_scope: Inside CS → Outside CS → Summary
        inside_metrics = [m for m in metrics if m.get("eval_scope", "").startswith("Inside")]
        outside_metrics = [m for m in metrics if m.get("eval_scope", "").startswith("Outside")]
        summary_metrics = [m for m in metrics if m.get("eval_scope", "").startswith("—")]
        grouped_metrics = inside_metrics + outside_metrics + summary_metrics

        round_start = current_row

        for j, metric in enumerate(grouped_metrics):
            row = current_row + j
            scope = metric.get("eval_scope", "")
            is_inside = scope.startswith("Inside")

            # Round name (only first row of each round)
            if j == 0:
                round_cell = ws.cell(row=row, column=1, value=round_data["round_name"])
                round_cell.font = ROUND_HEADER_FONT
                round_cell.alignment = CENTER_ALIGNMENT

            is_summary = scope.startswith("—")

            # Eval Scope with color coding
            scope_cell = ws.cell(row=row, column=2, value=scope)
            scope_cell.alignment = Alignment(horizontal="center", vertical="top", wrap_text=True)
            scope_cell.font = Font(name="Calibri", size=9, bold=True)
            if is_summary:
                summary_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
                scope_cell.fill = summary_fill
                # Bold the entire summary row
                for c in range(1, 12):
                    ws.cell(row=row, column=c).fill = summary_fill
                ws.cell(row=row, column=5).font = Font(name="Calibri", size=11, bold=True, color="1F4E79")
                ws.cell(row=row, column=6).font = Font(name="Calibri", size=11, bold=True)
            elif is_inside:
                scope_cell.fill = SCOPE_INSIDE_FILL
            else:
                scope_cell.fill = SCOPE_OUTSIDE_FILL

            # Dataset
            dataset_cell = ws.cell(row=row, column=3, value=metric.get("dataset", ""))
            dataset_cell.alignment = WRAP_ALIGNMENT

            # How Measured
            how_cell = ws.cell(row=row, column=4, value=metric.get("how_measured", ""))
            how_cell.alignment = WRAP_ALIGNMENT
            how_cell.font = Font(name="Calibri", size=9)

            # Metric Name
            ws.cell(row=row, column=5, value=metric["name"])
            ws.cell(row=row, column=5).alignment = WRAP_ALIGNMENT

            # Value
            ws.cell(row=row, column=6, value=format_pct(metric["value"]))
            ws.cell(row=row, column=6).alignment = Alignment(horizontal="center", vertical="top")

            # Target
            ws.cell(row=row, column=7, value=format_pct(metric["target"]))
            ws.cell(row=row, column=7).alignment = Alignment(horizontal="center", vertical="top")

            # Finding
            ws.cell(row=row, column=8, value=metric["finding"])
            ws.cell(row=row, column=8).alignment = WRAP_ALIGNMENT

            # Action
            ws.cell(row=row, column=9, value=metric["action"])
            ws.cell(row=row, column=9).alignment = WRAP_ALIGNMENT

            # Outcome
            ws.cell(row=row, column=10, value=metric["outcome"])
            ws.cell(row=row, column=10).alignment = WRAP_ALIGNMENT

            # CS Test Method (only for Inside CS)
            cs_method = ""
            how = metric.get("how_measured", "")
            if is_inside and "CS Test Method:" in how:
                cs_method = how.split("CS Test Method:")[1].split(".")[0].strip()
            ws.cell(row=row, column=11, value=cs_method)
            ws.cell(row=row, column=11).alignment = Alignment(horizontal="center", vertical="top", wrap_text=True)

            # Row height
            ws.row_dimensions[row].height = 65

            # Color the value cell based on target comparison
            val = metric["value"]
            tgt = metric["target"]
            if isinstance(val, (int, float)) and isinstance(tgt, (int, float)):
                if val >= tgt:
                    ws.cell(row=row, column=6).fill = GREEN_FILL
                elif val >= tgt * 0.95:
                    ws.cell(row=row, column=6).fill = ORANGE_FILL

        round_end = current_row + len(grouped_metrics) - 1

        # Merge round name column
        if len(grouped_metrics) > 1:
            ws.merge_cells(
                start_row=round_start, start_column=1,
                end_row=round_end, end_column=1
            )

        # Merge eval_scope cells for consecutive same-scope rows
        scope_start = round_start
        for k in range(1, len(grouped_metrics)):
            curr_scope = grouped_metrics[k].get("eval_scope", "")
            prev_scope = grouped_metrics[k - 1].get("eval_scope", "")
            if curr_scope != prev_scope:
                if k - (scope_start - round_start) > 1:
                    ws.merge_cells(
                        start_row=scope_start, start_column=2,
                        end_row=round_start + k - 1, end_column=2
                    )
                scope_start = round_start + k
        # Merge last group
        if round_end > scope_start:
            ws.merge_cells(
                start_row=scope_start, start_column=2,
                end_row=round_end, end_column=2
            )

        # Apply round separator border
        for col in range(1, 12):
            ws.cell(row=round_end, column=col).border = THICK_BORDER

        current_row = round_end + 1

    # Column widths
    ws.column_dimensions["A"].width = 13
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 40
    ws.column_dimensions["D"].width = 38
    ws.column_dimensions["E"].width = 26
    ws.column_dimensions["F"].width = 10
    ws.column_dimensions["G"].width = 10
    ws.column_dimensions["H"].width = 45
    ws.column_dimensions["I"].width = 45
    ws.column_dimensions["J"].width = 30
    ws.column_dimensions["K"].width = 16

    # Freeze header row
    ws.freeze_panes = f"A{header_row + 1}"


def build_agent_sheet(wb, agent):
    """Build an individual agent evaluation sheet (standard format) with Method column."""
    ws = wb.create_sheet(title=agent["sheet_name"])

    # Title
    ws.merge_cells("A1:I1")
    title_cell = ws.cell(
        row=1, column=1,
        value=f"{agent['customer']} - {agent['agent_name']} | Evaluation Results"
    )
    title_cell.font = TITLE_FONT
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 35

    # Method subtitle
    ws.merge_cells("A2:I2")
    method_cell = ws.cell(row=2, column=1, value=f"Method: {agent.get('eval_method', '')}")
    method_cell.font = Font(name="Calibri", size=11, bold=True, italic=True, color="1F4E79")
    method_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[2].height = 22

    # Metric definition subtitle
    metric_def = agent.get("metric_definition", "")
    if metric_def:
        ws.merge_cells("A3:I3")
        def_cell = ws.cell(row=3, column=1, value=f"Metric Definition: {metric_def}")
        def_cell.font = Font(name="Calibri", size=10, italic=True, color="404040")
        def_cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.row_dimensions[3].height = 35

    # Headers with Test Method as 3rd column
    headers = [
        "Eval Round", "Eval Dataset", "Test Method", "Metric Name", "Value",
        "Target", "Finding / Observation", "Action Taken", "Outcome / Impact"
    ]
    header_row = 5
    apply_header_row(ws, header_row, headers)

    current_row = header_row + 1
    eval_method = agent.get("eval_method", "")

    for round_data in agent["eval_rounds"]:
        round_start = current_row
        metrics = round_data["metrics"]

        for j, metric in enumerate(metrics):
            row = current_row + j

            # Round name (only first row of each round)
            if j == 0:
                round_cell = ws.cell(row=row, column=1, value=round_data["round_name"])
                round_cell.font = ROUND_HEADER_FONT
                round_cell.alignment = CENTER_ALIGNMENT

                dataset_cell = ws.cell(row=row, column=2, value=round_data.get("dataset", ""))
                dataset_cell.alignment = WRAP_ALIGNMENT

                method_cell = ws.cell(row=row, column=3, value=eval_method)
                method_cell.alignment = WRAP_ALIGNMENT
                method_cell.font = Font(name="Calibri", size=9, bold=True, color="1F4E79")

            ws.cell(row=row, column=4, value=metric["name"])
            ws.cell(row=row, column=4).alignment = WRAP_ALIGNMENT

            val = metric["value"]
            tgt = metric["target"]
            val_cell = ws.cell(row=row, column=5, value=val)
            val_cell.alignment = Alignment(horizontal="center", vertical="top")
            val_cell.number_format = '0.0%'

            tgt_cell = ws.cell(row=row, column=6, value=tgt)
            tgt_cell.alignment = Alignment(horizontal="center", vertical="top")
            tgt_cell.number_format = '0.0%'

            ws.cell(row=row, column=7, value=metric["finding"])
            ws.cell(row=row, column=7).alignment = WRAP_ALIGNMENT

            ws.cell(row=row, column=8, value=metric["action"])
            ws.cell(row=row, column=8).alignment = WRAP_ALIGNMENT

            ws.cell(row=row, column=9, value=metric["outcome"])
            ws.cell(row=row, column=9).alignment = WRAP_ALIGNMENT

            # Row height for readability
            ws.row_dimensions[row].height = 55

            # Color the value cell based on target comparison
            if isinstance(val, (int, float)) and isinstance(tgt, (int, float)):
                if val >= tgt:
                    ws.cell(row=row, column=5).fill = GREEN_FILL
                elif val >= tgt * 0.95:
                    ws.cell(row=row, column=5).fill = ORANGE_FILL

        round_end = current_row + len(metrics) - 1

        # Merge round name, dataset, and test method cells
        if len(metrics) > 1:
            ws.merge_cells(
                start_row=round_start, start_column=1,
                end_row=round_end, end_column=1
            )
            ws.merge_cells(
                start_row=round_start, start_column=2,
                end_row=round_end, end_column=2
            )
            ws.merge_cells(
                start_row=round_start, start_column=3,
                end_row=round_end, end_column=3
            )

        # Apply round separator border on last row
        for col in range(1, 10):
            ws.cell(row=round_end, column=col).border = THICK_BORDER

        current_row = round_end + 1

    # Column widths
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 25
    ws.column_dimensions["D"].width = 26
    ws.column_dimensions["E"].width = 10
    ws.column_dimensions["F"].width = 10
    ws.column_dimensions["G"].width = 50
    ws.column_dimensions["H"].width = 45
    ws.column_dimensions["I"].width = 30

    # Freeze header row
    ws.freeze_panes = "A6"


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main():
    wb = Workbook()

    # Build summary dashboard (uses the default active sheet)
    build_summary_sheet(wb)

    # Build individual agent sheets
    for agent in AGENTS:
        build_agent_sheet(wb, agent)

    # Save
    output_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(output_dir, "ai_agent_evaluation_report_v9.xlsx")
    wb.save(output_path)
    print(f"Report generated: {output_path}")


if __name__ == "__main__":
    main()

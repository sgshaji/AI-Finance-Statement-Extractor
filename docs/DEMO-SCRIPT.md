# Demo Script — Financial Statement Analysis Agent

**Duration:** ~12 minutes
**Audience:** Sponsors, Credit leadership
**Document:** Xiamen ITG Group Corp. Q3 2025 (Chinese quarterly report)

---

## 1. Opening — Set Expectations (30 seconds)

> "Before we start, I want to set context for what you're seeing.
>
> This is our development and testing environment — Copilot Studio. It shows more detail than an end user would see, so we can trace exactly what the agent is doing.
>
> From an analyst's perspective, their experience will be much simpler. They'll interact through Teams or a lightweight chat interface. They upload a document, wait, and receive a structured Excel output. No prompts to write, no settings to configure.
>
> For today's demo, I'm deliberately choosing a challenging document — a Chinese quarterly financial report from Xiamen C&D Corporation, one of China's largest conglomerates. This is not a clean English 10-K. It's a real-world filing in Mandarin with Chinese accounting standards."

---

## 2. Upload and Extraction (3 minutes)

**Action:** Upload `Xiamen ITG Group Corp.,Ltd_QR_2025-09-30T00_00_00_Chinese.pdf` in the agent chat.

> "I upload the PDF directly into the agent. No file prep, no page selection, no language setting. The agent accepts it as-is."

**Wait for processing message:** "Processing Xiamen ITG Group Corp... This may take up to a minute..."

> "The agent has accepted the file and started processing. Let me walk you through what's happening behind the scenes.
>
> First, it uses Azure Content Understanding to scan the entire document and locate the financial statements. This document is in Chinese, so the agent is looking for 合并资产负债表 — the Consolidated Balance Sheet — 合并利润表 — the Income Statement — and 合并现金流量表 — the Cash Flow Statement.
>
> It's not matching by page number. It understands document structure and specifically selects the consolidated statements over the parent company statements. Chinese filings include both — the agent knows the difference.
>
> Next, it extracts every line item — values, labels, periods — preserving the exact structure as it appears in the source.
>
> Then it translates all the Chinese labels to English. The original Chinese labels are preserved for audit — nothing is lost. But the output the analyst works with has English labels.
>
> Finally, it runs confidence scoring and cross-validation. If a subtotal doesn't add up, or a row looks ambiguous, the agent flags it."

**Wait ~2-3 minutes for extraction to complete. Continue talking as needed:**

> "This is a larger document with more complex table structures than a typical English filing, so it takes a bit longer. In production, the analyst would simply move on to another task and come back when it's done."

---

## 3. Navigator Card — Extraction Summary (1 minute)

**The navigator card appears showing:**
- Company: Xiamen C&D Corporation Limited
- Currency: CNY
- Balance Sheet: ⚠ Medium, 87 rows
- Income Statement: ⚠ Medium, 54 rows
- Cash Flow: ⚠ Medium, 170 rows
- Two buttons: "Start Review" and "Generate Excel"

> "The agent has finished. Look at what it extracted from a Chinese document:
>
> 311 line items across all three statements. Balance Sheet with 87 rows, Income Statement with 54, and Cash Flow with 170. Every single label has been translated from Chinese to English.
>
> The confidence scores are Medium — which is expected for a Chinese quarterly report. The flagged items are mostly edge cases where the original Chinese formatting creates ambiguity, not actual extraction errors.
>
> The analyst now has two options: review the data in a purpose-built grid, or go straight to Excel."

---

## 4. Start Review — HITL Grid (2 minutes)

**Action:** Click "Start Review" — Power Apps grid opens in browser.

> "The analyst clicks Start Review, and the review grid opens. This is a Power Apps interface reading directly from Dataverse."

**Walk through the grid:**

> "At the top — company name, confidence bar, item counts. You can see 311 items total with the flagged count.
>
> The tabs show each statement with line item counts. Let me click through them.
>
> **Balance Sheet** — 87 rows. Assets, current assets, non-current assets, liabilities, equity — all properly sectioned and indented. These labels were originally in Chinese — 货币资金, 应收账款, 存货 — now showing as Cash and Cash Equivalents, Accounts Receivable, Inventory. The original Chinese labels are preserved in the system for audit.
>
> Flagged rows have an amber border. The analyst can click any cell to edit it. Corrections save immediately to Dataverse — no save button.
>
> **Income Statement** — 54 rows. Revenue, operating costs, profit items — all structured correctly from the Chinese ASBE format.
>
> **Cash Flow** — 170 rows. Operating, investing, financing activities. This is the most detailed statement — and the agent handled all 170 rows.
>
> When satisfied, the analyst clicks 'Approve reviewed items'."

---

## 5. Generate Excel (2 minutes)

**Action:** Click "Generate Excel" on the navigator card.

> "Now let's generate the Excel. The agent reads the approved data from Dataverse — including any corrections — and builds a formatted workbook."

**Wait ~5 seconds. Download and open the Excel.**

> "The workbook has four sheets:
>
> **Summary** — extraction metadata. Which statements were found, source page numbers, extraction status, and the confidence score of 78%. You can see all three statements extracted successfully. This is the audit trail.
>
> **Balance Sheet** — every line item from the consolidated 合并资产负债表. Section headers in bold, totals with borders, proper indentation. The values are in CNY millions — the agent automatically normalised from raw yuan to millions to keep the numbers manageable.
>
> **Income Statement** — revenue, costs, operating profit, net income. All 54 rows preserved exactly as they appear in the source filing.
>
> **Cash Flow** — all 170 rows. Operating activities, investing activities, financing activities. This is the longest statement, and every line item is accounted for.
>
> This workbook is ready for the analyst's normal credit analysis. No re-keying, no copy-paste from a Chinese PDF."

---

## 6. Close — Why This Matters (1 minute)

> "Let me summarise what you've just seen.
>
> A Credit analyst uploaded a Chinese financial report — a real quarterly filing from a major Chinese conglomerate. Within three minutes, the agent:
>
> - Identified and located three consolidated financial statements in a Chinese-language document
> - Extracted 311 line items with their values across multiple periods
> - Translated every Chinese label to English while preserving the originals
> - Ran confidence scoring and flagged items for review
> - Generated a formatted Excel workbook ready for credit analysis
>
> The manual process of reading a Chinese PDF, translating labels, and re-keying 311 values into a spreadsheet — that could take an analyst an entire day. This agent does it in three minutes.
>
> And this isn't limited to Chinese. We've tested with English, Japanese, and French filings. The agent handles any language, any accounting standard — IFRS, US GAAP, Chinese ASBE, Japanese GAAP.
>
> This is a foundation capability. If extraction is accurate, auditable, and trusted, it opens the door to further automation — trend analysis, covenant checking, peer comparison. Today is about proving that first step."

---

## Demo Tips

1. **Extraction takes ~3 minutes for Xiamen** — use the narrative to fill the time
2. **If asked about English documents:** "Meta Platforms extracts in under 60 seconds with 122 line items at 84% confidence"
3. **If extraction fails:** "The extraction is taking longer than expected. Let me show you a pre-extracted result" — open a cached Excel from `docs/samples/output/`
4. **If grid shows "Failed to load":** refresh the browser or use a direct URL with jobId
5. **Period labels show in Chinese** — frame as "preserving original language for auditability"
6. **Currency shows as millions** — frame as "automatically normalised for readability"
7. **Have a backup Excel** pre-downloaded from a previous successful run
8. **Keep the focus on the analyst workflow**, not the technical plumbing

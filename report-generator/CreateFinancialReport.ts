/**
 * CreateFinancialReport - Office Script for Power Automate
 *
 * Receives 4 JSON strings from the financial statement extractor,
 * creates 4 worksheets (Summary, Balance Sheet, Income Statement, Cash Flow Statement),
 * populates data, and applies formatting.
 *
 * Supports both:
 *   - Simple nested JSON: {"Assets": {"Cash": 1000, ...}}
 *   - v1.2 schema: {document_metadata: {...}, rows: [...], columns: [...]}
 *
 * Usage: Called by Power Automate "Run script" action with 4 string parameters.
 * Paste this into Excel Online > Automate > New Script > Save as "CreateFinancialReport"
 */
function main(
  workbook: ExcelScript.Workbook,
  balanceSheet: string,
  incomeStatement: string,
  cashFlow: string,
  summary: string
) {
  // Safe JSON parse — returns null on failure
  function safeParse(input: string): Record<string, unknown> | null {
    try {
      if (!input || input.trim() === "" || input.trim() === "null") return null;
      let parsed: Record<string, unknown> = JSON.parse(input) as Record<string, unknown>;
      if (!parsed || typeof parsed !== "object") return null;
      return parsed;
    } catch {
      return null;
    }
  }

  let bsData: Record<string, unknown> | null = safeParse(balanceSheet);
  let isData: Record<string, unknown> | null = safeParse(incomeStatement);
  let cfData: Record<string, unknown> | null = safeParse(cashFlow);
  let sumData: Record<string, unknown> | null = safeParse(summary);

  // Helper to ensure a clean worksheet exists
  function ensureWorksheet(name: string): ExcelScript.Worksheet {
    const existing = workbook.getWorksheet(name);
    if (existing) {
      existing.getUsedRange()?.clear(ExcelScript.ClearApplyTo.all);
      return existing;
    }
    return workbook.addWorksheet(name);
  }

  // Create worksheets
  const summarySheet = ensureWorksheet("Summary");
  const bsSheet = ensureWorksheet("Balance Sheet");
  const isSheet = ensureWorksheet("Income Statement");
  const cfSheet = ensureWorksheet("Cash Flow Statement");

  // Delete default Sheet1 if it exists
  const defaultSheet = workbook.getWorksheet("Sheet1");
  if (defaultSheet) {
    defaultSheet.delete();
  }

  // Check if data is v1.2 schema format
  function isV12Schema(data: object): boolean {
    return "document_metadata" in data && "rows" in data && "columns" in data;
  }

  // Populate statement sheets
  if (bsData) {
    if (isV12Schema(bsData)) {
      populateV12Statement(bsSheet, bsData as unknown as StatementDataV12);
    } else {
      populateNestedStatement(bsSheet, "Balance Sheet", bsData);
    }
  } else {
    bsSheet.getRange("A1").setValue("No data extracted for Balance Sheet.");
  }

  if (isData) {
    if (isV12Schema(isData)) {
      populateV12Statement(isSheet, isData as unknown as StatementDataV12);
    } else {
      populateNestedStatement(isSheet, "Income Statement", isData);
    }
  } else {
    isSheet.getRange("A1").setValue("No data extracted for Income Statement.");
  }

  if (cfData) {
    if (isV12Schema(cfData)) {
      populateV12Statement(cfSheet, cfData as unknown as StatementDataV12);
    } else {
      populateNestedStatement(cfSheet, "Cash Flow Statement", cfData);
    }
  } else {
    cfSheet.getRange("A1").setValue("No data extracted for Cash Flow Statement.");
  }

  // Populate summary sheet
  populateSummarySheet(summarySheet, sumData, bsData, isData, cfData);

  // Activate Summary sheet
  summarySheet.activate();
}

// ─── Nested JSON Flattener ──────────────────────────────────────────────────
// Converts {"Assets": {"Cash": 1000, "Sub": {"A": 10, "Total Sub": 10}}, "Total Assets": 5000}
// into flat rows with indent levels and row types.

interface FlatRow {
  label: string;
  value: number | null;
  indent: number;
  rowType: "section_header" | "line_item" | "subtotal" | "total";
}

function flattenNestedJSON(obj: object, indent: number = 0): FlatRow[] {
  const rows: FlatRow[] = [];
  const keys = Object.keys(obj);

  for (const key of keys) {
    const val = (obj as Record<string, unknown>)[key];
    const isTotal = key.toLowerCase().startsWith("total");

    if (typeof val === "number") {
      // Leaf node — it's a number value
      rows.push({
        label: key,
        value: val,
        indent: indent,
        rowType: isTotal ? (indent === 0 ? "total" : "subtotal") : "line_item",
      });
    } else if (typeof val === "object" && val !== null && !Array.isArray(val)) {
      // Section — recurse into children
      rows.push({
        label: key,
        value: null,
        indent: indent,
        rowType: "section_header",
      });
      const childRows = flattenNestedJSON(val as object, indent + 1);
      rows.push(...childRows);
    } else if (typeof val === "string") {
      // String value — treat as a line item with text
      rows.push({
        label: key,
        value: null,
        indent: indent,
        rowType: "line_item",
      });
    }
  }

  return rows;
}

// ─── Populate Nested JSON Statement ─────────────────────────────────────────

function populateNestedStatement(
  sheet: ExcelScript.Worksheet,
  title: string,
  data: object
) {
  const rows = flattenNestedJSON(data);

  if (rows.length === 0) {
    sheet.getRange("A1").setValue(`No data extracted for ${title}.`);
    return;
  }

  // Title
  const titleCell = sheet.getRange("A1");
  titleCell.setValue(title);
  titleCell.getFormat().getFont().setBold(true);
  titleCell.getFormat().getFont().setSize(14);

  // Header row (row 3 in Excel, index 2)
  const headerRowIdx = 2;
  const headers = ["Line Item", "Amount"];
  for (let c = 0; c < headers.length; c++) {
    const cell = sheet.getRangeByIndexes(headerRowIdx, c, 1, 1);
    cell.setValue(headers[c]);
    cell.getFormat().getFont().setBold(true);
    cell.getFormat().getFill().setColor("#4472C4");
    cell.getFormat().getFont().setColor("#FFFFFF");
  }
  sheet.getRangeByIndexes(headerRowIdx, 1, 1, 1)
    .getFormat().setHorizontalAlignment(ExcelScript.HorizontalAlignment.right);

  // Data rows
  const dataStartRow = headerRowIdx + 1;
  let lineItemCount = 0;

  for (let r = 0; r < rows.length; r++) {
    const row = rows[r];
    const excelRow = dataStartRow + r;

    // Label with indentation
    const indentStr = "  ".repeat(row.indent);
    const labelCell = sheet.getRangeByIndexes(excelRow, 0, 1, 1);
    labelCell.setValue(indentStr + row.label);

    // Style based on row type
    if (row.rowType === "section_header") {
      labelCell.getFormat().getFont().setBold(true);
      labelCell.getFormat().getFont().setColor("#2E5090");
    } else if (row.rowType === "total") {
      labelCell.getFormat().getFont().setBold(true);
      const rowRange = sheet.getRangeByIndexes(excelRow, 0, 1, 2);
      rowRange.getFormat().getRangeBorder(ExcelScript.BorderIndex.edgeTop)
        .setStyle(ExcelScript.BorderLineStyle.continuous);
      rowRange.getFormat().getRangeBorder(ExcelScript.BorderIndex.edgeTop)
        .setWeight(ExcelScript.BorderWeight.thin);
      rowRange.getFormat().getRangeBorder(ExcelScript.BorderIndex.edgeBottom)
        .setStyle(ExcelScript.BorderLineStyle.continuous);
      rowRange.getFormat().getRangeBorder(ExcelScript.BorderIndex.edgeBottom)
        .setWeight(ExcelScript.BorderWeight.thick);
    } else if (row.rowType === "subtotal") {
      labelCell.getFormat().getFont().setBold(true);
      const rowRange = sheet.getRangeByIndexes(excelRow, 0, 1, 2);
      rowRange.getFormat().getRangeBorder(ExcelScript.BorderIndex.edgeTop)
        .setStyle(ExcelScript.BorderLineStyle.continuous);
      rowRange.getFormat().getRangeBorder(ExcelScript.BorderIndex.edgeTop)
        .setWeight(ExcelScript.BorderWeight.thin);
    }

    // Value
    if (row.value !== null) {
      const valCell = sheet.getRangeByIndexes(excelRow, 1, 1, 1);
      valCell.setValue(row.value);
      valCell.setNumberFormatLocal("#,##0.00");
      valCell.getFormat().setHorizontalAlignment(ExcelScript.HorizontalAlignment.right);

      if (row.value < 0) {
        valCell.getFormat().getFont().setColor("#D83B01");
      }
    }

    // Alternate row shading for line items
    if (row.rowType === "line_item" && row.value !== null) {
      lineItemCount++;
      if (lineItemCount % 2 === 0) {
        const rowRange = sheet.getRangeByIndexes(excelRow, 0, 1, 2);
        rowRange.getFormat().getFill().setColor("#F2F2F2");
      }
    }
  }

  // Auto-fit columns
  sheet.getRange("A:B").getFormat().autofitColumns();

  // Freeze header row
  sheet.getFreezePanes().freezeRows(headerRowIdx + 1);
}

// ─── Summary Sheet ──────────────────────────────────────────────────────────

function populateSummarySheet(
  sheet: ExcelScript.Worksheet,
  sumData: object | null,
  bsData: object | null,
  isData: object | null,
  cfData: object | null
) {
  // Title
  const titleCell = sheet.getRange("A1");
  titleCell.setValue("Financial Statement Extraction Report");
  titleCell.getFormat().getFont().setBold(true);
  titleCell.getFormat().getFont().setSize(16);

  // Extraction status table
  let currentRow = 3;
  const statusTitle = sheet.getRange(`A${currentRow}`);
  statusTitle.setValue("Extraction Status");
  statusTitle.getFormat().getFont().setBold(true);
  statusTitle.getFormat().getFont().setSize(13);

  currentRow++;
  const statusHeaders = ["Statement", "Status"];
  for (let c = 0; c < statusHeaders.length; c++) {
    const cell = sheet.getRangeByIndexes(currentRow - 1, c, 1, 1);
    cell.setValue(statusHeaders[c]);
    cell.getFormat().getFont().setBold(true);
    cell.getFormat().getFill().setColor("#4472C4");
    cell.getFormat().getFont().setColor("#FFFFFF");
  }

  const statements: [string, object | null][] = [
    ["Balance Sheet", bsData],
    ["Income Statement", isData],
    ["Cash Flow Statement", cfData],
  ];

  for (let i = 0; i < statements.length; i++) {
    const row = currentRow + i;
    sheet.getRangeByIndexes(row, 0, 1, 1).setValue(statements[i][0]);
    const statusCell = sheet.getRangeByIndexes(row, 1, 1, 1);
    const extracted = statements[i][1] !== null;
    statusCell.setValue(extracted ? "Extracted" : "Not Found");
    statusCell.getFormat().getFont().setColor(extracted ? "#107C10" : "#D83B01");
    statusCell.getFormat().getFont().setBold(true);
  }

  currentRow += statements.length + 2;

  // Key figures section — extract headline numbers from the data
  const keyFigures: [string, string][] = [];

  if (isData && typeof isData === "object") {
    const is = isData as Record<string, unknown>;
    if (typeof is["Revenue"] === "number") keyFigures.push(["Revenue", formatNumber(is["Revenue"] as number)]);
    if (typeof is["Net income"] === "number") keyFigures.push(["Net Income", formatNumber(is["Net income"] as number)]);
    if (typeof is["Income from operations"] === "number") keyFigures.push(["Operating Income", formatNumber(is["Income from operations"] as number)]);
  }

  if (bsData && typeof bsData === "object") {
    const bs = bsData as Record<string, unknown>;
    const assets = bs["Assets"] as Record<string, unknown> | undefined;
    if (assets && typeof assets["Total assets"] === "number") {
      keyFigures.push(["Total Assets", formatNumber(assets["Total assets"] as number)]);
    }
    const liabEquity = bs["Liabilities and stockholders' equity"] as Record<string, unknown> | undefined;
    if (liabEquity) {
      if (typeof liabEquity["Total liabilities"] === "number") {
        keyFigures.push(["Total Liabilities", formatNumber(liabEquity["Total liabilities"] as number)]);
      }
      const equity = liabEquity["Stockholders' equity"] as Record<string, unknown> | undefined;
      if (equity && typeof equity["Total stockholders' equity"] === "number") {
        keyFigures.push(["Total Equity", formatNumber(equity["Total stockholders' equity"] as number)]);
      }
    }
  }

  if (keyFigures.length > 0) {
    const kfTitle = sheet.getRange(`A${currentRow}`);
    kfTitle.setValue("Key Financial Highlights");
    kfTitle.getFormat().getFont().setBold(true);
    kfTitle.getFormat().getFont().setSize(13);
    currentRow++;

    for (let i = 0; i < keyFigures.length; i++) {
      sheet.getRange(`A${currentRow + i}`).setValue(keyFigures[i][0]);
      sheet.getRange(`A${currentRow + i}`).getFormat().getFont().setBold(true);
      sheet.getRange(`B${currentRow + i}`).setValue(keyFigures[i][1]);
      sheet.getRange(`B${currentRow + i}`).getFormat()
        .setHorizontalAlignment(ExcelScript.HorizontalAlignment.right);
    }
    currentRow += keyFigures.length + 2;
  }

  // If summary data was provided, display it
  if (sumData) {
    const sumTitle = sheet.getRange(`A${currentRow}`);
    sumTitle.setValue("Extraction Summary");
    sumTitle.getFormat().getFont().setBold(true);
    sumTitle.getFormat().getFont().setSize(13);
    currentRow++;

    const sumRows = flattenNestedJSON(sumData);
    for (let i = 0; i < sumRows.length; i++) {
      const row = sumRows[i];
      const indentStr = "  ".repeat(row.indent);
      sheet.getRange(`A${currentRow + i}`).setValue(indentStr + row.label);
      if (row.value !== null) {
        sheet.getRange(`B${currentRow + i}`).setValue(row.value);
        sheet.getRange(`B${currentRow + i}`).setNumberFormatLocal("#,##0.00");
      }
      if (row.rowType === "section_header") {
        sheet.getRange(`A${currentRow + i}`).getFormat().getFont().setBold(true);
      }
    }
  }

  // Auto-fit columns
  sheet.getRange("A:B").getFormat().autofitColumns();
}

// ─── v1.2 Schema Support (kept for backwards compatibility) ─────────────────

interface StatementDataV12 {
  schema_version: string;
  document_metadata: {
    source_file_name: string;
    company_name: string | null;
    company_name_raw: string | null;
    report_type: string | null;
    report_language: string | null;
    ticker: string | null;
    source_exchange: string | null;
  };
  statement_metadata: {
    statement_type: string;
    statement_title: string | null;
    statement_title_raw: string | null;
    accounting_standard?: string | null;
    currency: string | null;
    currency_symbol: string | null;
    unit: string | null;
    unit_raw: string | null;
    is_consolidated: boolean | null;
    is_audited: boolean | null;
    page_range: { start: number | null; end: number | null } | null;
  };
  columns: {
    column_index: number;
    label: string | null;
    label_raw: string | null;
    period_type: string | null;
    fiscal_year: number | null;
    end_date: string | null;
    is_comparative: boolean;
  }[];
  rows: {
    row_index: number;
    label_raw: string | null;
    label_normalized: string | null;
    canonical_key: string | null;
    canonical_group: string | null;
    row_type: string | null;
    indent_level: number;
    section: string | null;
    values: {
      column_index: number;
      raw: string | null;
      normalized: number | null;
      is_null: boolean;
    }[];
  }[];
  validation?: {
    status: string | null;
    warnings: object[];
    errors: object[];
  };
}

function populateV12Statement(sheet: ExcelScript.Worksheet, data: StatementDataV12) {
  if (!data || !data.rows || data.rows.length === 0) {
    sheet.getRange("A1").setValue("No data extracted for this statement.");
    return;
  }

  const meta = data.statement_metadata;
  const cols = data.columns;

  // Title row
  const titleCell = sheet.getRange("A1");
  titleCell.setValue(meta.statement_title || meta.statement_type || "Financial Statement");
  titleCell.getFormat().getFont().setBold(true);
  titleCell.getFormat().getFont().setSize(14);

  // Subtitle: currency + unit
  const currencyParts: string[] = [];
  if (meta.currency) currencyParts.push(meta.currency);
  if (meta.unit && meta.unit !== "ones") currencyParts.push(`in ${formatLabel(meta.unit)}`);
  if (currencyParts.length > 0) {
    const subtitleCell = sheet.getRange("A2");
    subtitleCell.setValue(currencyParts.join(" "));
    subtitleCell.getFormat().getFont().setItalic(true);
    subtitleCell.getFormat().getFont().setColor("#666666");
  }

  // Use all columns — column_index is 0-based, no filtering needed
  const displayCols = cols;

  // Header row (row 4 in Excel, index 3)
  const headerRowIdx = 3;
  sheet.getRangeByIndexes(headerRowIdx, 0, 1, 1).setValue("Line Item");

  for (let c = 0; c < displayCols.length; c++) {
    const col = displayCols[c];
    const headerLabel = col.fiscal_year
      ? col.end_date || col.label || `Period ${col.column_index}`
      : col.label || `Column ${col.column_index}`;
    const cell = sheet.getRangeByIndexes(headerRowIdx, c + 1, 1, 1);
    cell.setValue(headerLabel);
    cell.getFormat().setHorizontalAlignment(ExcelScript.HorizontalAlignment.right);
  }

  const headerRange = sheet.getRangeByIndexes(headerRowIdx, 0, 1, displayCols.length + 1);
  headerRange.getFormat().getFont().setBold(true);
  headerRange.getFormat().getFill().setColor("#4472C4");
  headerRange.getFormat().getFont().setColor("#FFFFFF");

  // Data rows
  const dataStartRow = headerRowIdx + 1;
  for (let r = 0; r < data.rows.length; r++) {
    const row = data.rows[r];
    const excelRow = dataStartRow + r;
    const indent = "  ".repeat(row.indent_level || 0);
    const label = row.label_normalized || row.label_raw || row.canonical_key || "";
    const labelCell = sheet.getRangeByIndexes(excelRow, 0, 1, 1);
    labelCell.setValue(indent + label);

    if (row.row_type === "section_header") {
      labelCell.getFormat().getFont().setBold(true);
      labelCell.getFormat().getFont().setColor("#2E5090");
    } else if (row.row_type === "total" || row.row_type === "subtotal") {
      labelCell.getFormat().getFont().setBold(true);
      const rowRange = sheet.getRangeByIndexes(excelRow, 0, 1, displayCols.length + 1);
      if (row.row_type === "total") {
        rowRange.getFormat().getRangeBorder(ExcelScript.BorderIndex.edgeBottom)
          .setStyle(ExcelScript.BorderLineStyle.continuous);
        rowRange.getFormat().getRangeBorder(ExcelScript.BorderIndex.edgeBottom)
          .setWeight(ExcelScript.BorderWeight.thick);
        rowRange.getFormat().getRangeBorder(ExcelScript.BorderIndex.edgeTop)
          .setStyle(ExcelScript.BorderLineStyle.continuous);
        rowRange.getFormat().getRangeBorder(ExcelScript.BorderIndex.edgeTop)
          .setWeight(ExcelScript.BorderWeight.thin);
      } else {
        rowRange.getFormat().getRangeBorder(ExcelScript.BorderIndex.edgeTop)
          .setStyle(ExcelScript.BorderLineStyle.continuous);
        rowRange.getFormat().getRangeBorder(ExcelScript.BorderIndex.edgeTop)
          .setWeight(ExcelScript.BorderWeight.thin);
      }
    }

    for (let c = 0; c < displayCols.length; c++) {
      const colIdx = displayCols[c].column_index;
      const val = row.values.find((v) => v.column_index === colIdx);
      if (val && !val.is_null && val.normalized !== null) {
        const valCell = sheet.getRangeByIndexes(excelRow, c + 1, 1, 1);
        valCell.setValue(val.normalized);
        valCell.setNumberFormatLocal("#,##0.00");
        valCell.getFormat().setHorizontalAlignment(ExcelScript.HorizontalAlignment.right);
        if (val.normalized < 0) {
          valCell.getFormat().getFont().setColor("#D83B01");
        }
      }
    }

    if (row.row_type === "line_item" && r % 2 === 0) {
      const rowRange = sheet.getRangeByIndexes(excelRow, 0, 1, displayCols.length + 1);
      rowRange.getFormat().getFill().setColor("#F2F2F2");
    }
  }

  // Auto-fit columns
  const totalCols = displayCols.length + 1;
  const colLetter = String.fromCharCode(64 + totalCols);
  sheet.getRange(`A:${colLetter}`).getFormat().autofitColumns();

  // Freeze header row
  sheet.getFreezePanes().freezeRows(headerRowIdx + 1);
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function formatLabel(value: string | null | undefined): string {
  if (!value) return "N/A";
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatNumber(value: number): string {
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

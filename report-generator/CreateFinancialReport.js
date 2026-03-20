/**
 * CreateFinancialReport - Office Script for Power Automate
 *
 * Receives 4 JSON strings from the financial statement extractor (v1.2 schema),
 * creates 4 worksheets (Summary, Balance Sheet, Income Statement, Cash Flow Statement),
 * populates data, and applies formatting.
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
  // Parse JSON inputs
  const bsData: StatementData = JSON.parse(balanceSheet);
  const isData: StatementData = JSON.parse(incomeStatement);
  const cfData: StatementData = JSON.parse(cashFlow);
  const summaryData: SummaryData = JSON.parse(summary);

  // Delete default Sheet1 if it exists
  const defaultSheet = workbook.getWorksheet("Sheet1");
  if (defaultSheet) {
    defaultSheet.delete();
  }

  // Create worksheets
  const summarySheet = workbook.addWorksheet("Summary");
  const bsSheet = workbook.addWorksheet("Balance Sheet");
  const isSheet = workbook.addWorksheet("Income Statement");
  const cfSheet = workbook.addWorksheet("Cash Flow Statement");

  // Populate each worksheet
  populateSummary(summarySheet, bsData, summaryData);
  populateStatement(bsSheet, bsData);
  populateStatement(isSheet, isData);
  populateStatement(cfSheet, cfData);

  // Activate Summary sheet
  summarySheet.activate();
}

// ─── Types ──────────────────────────────────────────────────────────────────

interface StatementData {
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
  columns: ColumnDef[];
  rows: RowDef[];
  validation?: {
    status: string | null;
    warnings: any[];
    errors: any[];
  };
}

interface ColumnDef {
  column_index: number;
  label: string | null;
  label_raw: string | null;
  period_type: string | null;
  fiscal_year: number | null;
  end_date: string | null;
  is_comparative: boolean;
}

interface RowDef {
  row_index: number;
  label_raw: string | null;
  label_normalized: string | null;
  canonical_key: string | null;
  canonical_group: string | null;
  row_type: string | null;
  indent_level: number;
  section: string | null;
  values: ValueDef[];
}

interface ValueDef {
  column_index: number;
  raw: string | null;
  normalized: number | null;
  is_null: boolean;
}

interface SummaryData {
  summary: {
    statement_type: string;
    status: string;
    page_range: { start: number | null; end: number | null } | null;
  }[];
}

// ─── Summary Sheet ──────────────────────────────────────────────────────────

function populateSummary(
  sheet: ExcelScript.Worksheet,
  bsData: StatementData,
  summaryData: SummaryData
) {
  const meta = bsData.document_metadata;
  const stmtMeta = bsData.statement_metadata;

  // Title
  const titleCell = sheet.getRange("A1");
  titleCell.setValue("Financial Statement Extraction Report");
  titleCell.getFormat().getFont().setBold(true);
  titleCell.getFormat().getFont().setSize(16);

  // Company info section
  const labels: [string, string][] = [
    ["Company Name", meta.company_name || "N/A"],
    ["Company Name (Original)", meta.company_name_raw || "N/A"],
    ["Source File", meta.source_file_name || "N/A"],
    ["Report Type", formatLabel(meta.report_type)],
    ["Report Language", meta.report_language || "N/A"],
    ["Ticker", meta.ticker || "N/A"],
    ["Exchange", meta.source_exchange || "N/A"],
    ["Currency", stmtMeta.currency || "N/A"],
    ["Unit", formatLabel(stmtMeta.unit)],
    ["Accounting Standard", stmtMeta.accounting_standard || "N/A"],
    ["Consolidated", stmtMeta.is_consolidated === true ? "Yes" : stmtMeta.is_consolidated === false ? "No" : "N/A"],
    ["Audited", stmtMeta.is_audited === true ? "Yes" : stmtMeta.is_audited === false ? "No" : "N/A"],
    ["Schema Version", bsData.schema_version || "N/A"],
  ];

  const infoStartRow = 3;
  for (let i = 0; i < labels.length; i++) {
    const row = infoStartRow + i;
    sheet.getRange(`A${row}`).setValue(labels[i][0]);
    sheet.getRange(`A${row}`).getFormat().getFont().setBold(true);
    sheet.getRange(`B${row}`).setValue(labels[i][1]);
  }

  // Extraction status section
  const statusStartRow = infoStartRow + labels.length + 2;
  const statusTitle = sheet.getRange(`A${statusStartRow}`);
  statusTitle.setValue("Extraction Status");
  statusTitle.getFormat().getFont().setBold(true);
  statusTitle.getFormat().getFont().setSize(13);

  // Headers
  const headerRow = statusStartRow + 1;
  const headers = ["Statement", "Status", "Pages"];
  for (let c = 0; c < headers.length; c++) {
    const cell = sheet.getRangeByIndexes(headerRow - 1, c, 1, 1);
    cell.setValue(headers[c]);
    cell.getFormat().getFont().setBold(true);
    cell.getFormat().getFill().setColor("#4472C4");
    cell.getFormat().getFont().setColor("#FFFFFF");
  }

  // Status rows
  const stmtNames: Record<string, string> = {
    balance_sheet: "Balance Sheet",
    income_statement: "Income Statement",
    cash_flow: "Cash Flow Statement",
  };

  for (let i = 0; i < summaryData.summary.length; i++) {
    const entry = summaryData.summary[i];
    const row = headerRow + i;
    sheet.getRangeByIndexes(row, 0, 1, 1).setValue(stmtNames[entry.statement_type] || entry.statement_type);
    const statusCell = sheet.getRangeByIndexes(row, 1, 1, 1);
    const isExtracted = entry.status === "extracted";
    statusCell.setValue(isExtracted ? "Extracted" : "Not Found");
    statusCell.getFormat().getFont().setColor(isExtracted ? "#107C10" : "#D83B01");
    statusCell.getFormat().getFont().setBold(true);
    const pages = entry.page_range
      ? `${entry.page_range.start || "?"}–${entry.page_range.end || "?"}`
      : "N/A";
    sheet.getRangeByIndexes(row, 2, 1, 1).setValue(pages);
  }

  // Auto-fit columns
  sheet.getRange("A:C").getFormat().autofitColumns();
}

// ─── Statement Sheet ────────────────────────────────────────────────────────

function populateStatement(sheet: ExcelScript.Worksheet, data: StatementData) {
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
  const currencyInfo = [meta.currency, meta.unit !== "ones" ? `in ${formatLabel(meta.unit)}` : null]
    .filter(Boolean)
    .join(" ");
  if (currencyInfo) {
    const subtitleCell = sheet.getRange("A2");
    subtitleCell.setValue(currencyInfo);
    subtitleCell.getFormat().getFont().setItalic(true);
    subtitleCell.getFormat().getFont().setColor("#666666");
  }

  // Determine value columns (skip column_index 0 if it's the label column)
  const valueCols = cols.filter(
    (c) => c.label !== "项目" && c.label !== "Item" && c.column_index > 0
  );
  // If no separate value columns found, use all columns that have data
  const displayCols = valueCols.length > 0 ? valueCols : cols.filter((c) => c.column_index > 0);

  // Header row (row 4 in Excel, index 3)
  const headerRowIdx = 3;
  const labelHeaderCell = sheet.getRangeByIndexes(headerRowIdx, 0, 1, 1);
  labelHeaderCell.setValue("Line Item");
  labelHeaderCell.getFormat().getFont().setBold(true);

  for (let c = 0; c < displayCols.length; c++) {
    const col = displayCols[c];
    const headerLabel = col.fiscal_year
      ? col.end_date || col.label || `Period ${col.column_index}`
      : col.label || `Column ${col.column_index}`;
    const cell = sheet.getRangeByIndexes(headerRowIdx, c + 1, 1, 1);
    cell.setValue(headerLabel);
    cell.getFormat().getFont().setBold(true);
    cell.getFormat().setHorizontalAlignment(ExcelScript.HorizontalAlignment.right);
  }

  // Header row styling
  const headerRange = sheet.getRangeByIndexes(headerRowIdx, 0, 1, displayCols.length + 1);
  headerRange.getFormat().getFill().setColor("#4472C4");
  headerRange.getFormat().getFont().setColor("#FFFFFF");

  // Data rows
  const dataStartRow = headerRowIdx + 1;
  for (let r = 0; r < data.rows.length; r++) {
    const row = data.rows[r];
    const excelRow = dataStartRow + r;

    // Label with indentation
    const indent = "  ".repeat(row.indent_level || 0);
    const label = row.label_normalized || row.label_raw || row.canonical_key || "";
    const labelCell = sheet.getRangeByIndexes(excelRow, 0, 1, 1);
    labelCell.setValue(indent + label);

    // Style based on row type
    if (row.row_type === "section_header") {
      labelCell.getFormat().getFont().setBold(true);
      labelCell.getFormat().getFont().setColor("#2E5090");
    } else if (row.row_type === "total" || row.row_type === "subtotal") {
      labelCell.getFormat().getFont().setBold(true);
      const rowRange = sheet.getRangeByIndexes(excelRow, 0, 1, displayCols.length + 1);
      if (row.row_type === "total") {
        rowRange.getFormat().getBorderBottom().setStyle(ExcelScript.BorderLineStyle.double);
        rowRange.getFormat().getBorderTop().setStyle(ExcelScript.BorderLineStyle.thin);
      } else {
        rowRange.getFormat().getBorderTop().setStyle(ExcelScript.BorderLineStyle.thin);
      }
    }

    // Values
    for (let c = 0; c < displayCols.length; c++) {
      const colIdx = displayCols[c].column_index;
      const val = row.values.find((v) => v.column_index === colIdx);
      if (val && !val.is_null && val.normalized !== null) {
        const valCell = sheet.getRangeByIndexes(excelRow, c + 1, 1, 1);
        valCell.setValue(val.normalized);
        valCell.setNumberFormatLocal("#,##0.00");
        valCell.getFormat().setHorizontalAlignment(ExcelScript.HorizontalAlignment.right);

        // Negative values in red
        if (val.normalized < 0) {
          valCell.getFormat().getFont().setColor("#D83B01");
        }
      }
    }

    // Alternate row shading for readability (skip headers and totals)
    if (row.row_type === "line_item" && r % 2 === 0) {
      const rowRange = sheet.getRangeByIndexes(excelRow, 0, 1, displayCols.length + 1);
      rowRange.getFormat().getFill().setColor("#F2F2F2");
    }
  }

  // Validation status footer
  if (data.validation) {
    const footerRow = dataStartRow + data.rows.length + 2;
    const validationCell = sheet.getRangeByIndexes(footerRow, 0, 1, 1);
    validationCell.setValue(`Validation: ${data.validation.status || "N/A"}`);
    validationCell.getFormat().getFont().setItalic(true);

    if (data.validation.warnings && data.validation.warnings.length > 0) {
      const warnCell = sheet.getRangeByIndexes(footerRow + 1, 0, 1, 1);
      warnCell.setValue(`Warnings: ${data.validation.warnings.length}`);
      warnCell.getFormat().getFont().setColor("#D83B01");
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
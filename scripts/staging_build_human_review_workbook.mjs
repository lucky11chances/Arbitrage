import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const payloadPath = process.argv[2] || "data/staging/human_review_payload.json";
const outputPath = process.argv[3] || "data/staging/pm_ks_human_review.xlsx";

const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));

const workbook = Workbook.create();

function columnLetter(indexZeroBased) {
  let n = indexZeroBased + 1;
  let result = "";
  while (n > 0) {
    const rem = (n - 1) % 26;
    result = String.fromCharCode(65 + rem) + result;
    n = Math.floor((n - 1) / 26);
  }
  return result;
}

function matrixFromRows(rows, fields) {
  return [fields, ...rows.map((row) => fields.map((field) => row[field] ?? ""))];
}

function styleSheet(sheet, rowCount, colCount, widths = {}) {
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  const used = sheet.getRangeByIndexes(0, 0, rowCount, colCount);
  used.format.font.name = "Aptos";
  used.format.font.size = 10;
  used.format.wrapText = true;
  used.format.verticalAlignment = "top";
  const header = sheet.getRangeByIndexes(0, 0, 1, colCount);
  header.format.fill.color = "#17324D";
  header.format.font.color = "#FFFFFF";
  header.format.font.bold = true;
  header.format.rowHeight = 26;
  used.format.borders = { preset: "inside", style: "thin", color: "#E2E8F0" };
  for (let col = 0; col < colCount; col += 1) {
    const letter = columnLetter(col);
    const width = widths[letter] || 18;
    sheet.getRangeByIndexes(0, col, rowCount, 1).format.columnWidth = width;
  }
  sheet.getRangeByIndexes(1, 0, Math.max(rowCount - 1, 1), colCount).format.rowHeight = 48;
}

function addTableIfData(sheet, rowCount, colCount, tableName) {
  if (rowCount < 2) return;
  const last = `${columnLetter(colCount - 1)}${rowCount}`;
  const table = sheet.tables.add(`A1:${last}`, true, tableName);
  table.showFilterButton = true;
  table.showBandedRows = true;
}

function addDecisionValidation(sheet, fields, rowCount) {
  const idx = fields.indexOf("Human Decision");
  if (idx < 0 || rowCount < 2) return;
  const letter = columnLetter(idx);
  sheet.getRange(`${letter}2:${letter}${rowCount}`).dataValidation = {
    rule: { type: "list", values: ["ALLOW", "REJECT", "NEEDS_RULE", "OK"] },
  };
}

function addSheet(name, rows, fields, widths, tableName) {
  const sheet = workbook.worksheets.add(name);
  const matrix = matrixFromRows(rows, fields);
  sheet.getRangeByIndexes(0, 0, matrix.length, fields.length).values = matrix;
  styleSheet(sheet, matrix.length, fields.length, widths);
  addTableIfData(sheet, matrix.length, fields.length, tableName);
  addDecisionValidation(sheet, fields, matrix.length);
  return sheet;
}

const summaryFields = ["Metric", "Value", "Plain English"];
const pairedFields = [
  "Review Status",
  "Sport",
  "League / Tour",
  "Gender",
  "Event Date",
  "Market Type",
  "PM Search Text",
  "PM Match",
  "PM Outcomes",
  "Kalshi Search Text",
  "KS Match",
  "KS Outcomes",
  "Why It Paired",
  "What To Verify",
  "Human Decision",
  "Human Notes",
];
const unmatchedFields = [
  "Review Status",
  "Priority",
  "Sport",
  "League / Tour",
  "Gender",
  "Event Date",
  "Market Type",
  "Why Unmatched",
  "PM Search Text",
  "PM Match",
  "PM Outcomes",
  "Kalshi Search Text",
  "KS Match",
  "KS Outcomes",
  "Closest Candidate On Other Site",
  "Closest Candidate Outcomes",
  "Closest Candidate Date",
  "Name Similarity",
  "What To Check On Websites",
  "Human Decision",
  "Human Notes",
];

const howToUseRows = [
  {
    "Step": "1",
    "What to do": "Start with Unmatched Review rows where Priority = 1.",
    "Why it matters": "These are likely aliases: same sport/gender/date window, but the names differ.",
  },
  {
    "Step": "2",
    "What to do": "Open PM and KS and compare PM Match, PM Outcomes, KS Match, and KS Outcomes.",
    "Why it matters": "A safe pair requires the same event and the same yes-outcome direction.",
  },
  {
    "Step": "3",
    "What to do": "Fill Human Decision with ALLOW, REJECT, or NEEDS_RULE.",
    "Why it matters": "ALLOW can become a verified alias; REJECT remains blocked; NEEDS_RULE means only allow under tighter conditions.",
  },
  {
    "Step": "4",
    "What to do": "For PM-only or KS-only rows, search the other site by player/team names and date.",
    "Why it matters": "These rows may indicate missing normalization, missing metadata, or a market that truly exists only on one venue.",
  },
];

addSheet("Summary", payload.summary, summaryFields, { A: 28, B: 24, C: 72 }, "SummaryTable");
addSheet("How To Use", howToUseRows, ["Step", "What to do", "Why it matters"], { A: 10, B: 54, C: 76 }, "HowToUseTable");
addSheet("Paired Events", payload.paired, pairedFields, {
  A: 18, B: 16, C: 28, D: 14, E: 14, F: 16, G: 56, H: 48, I: 34, J: 56,
  K: 48, L: 34, M: 56, N: 58, O: 18, P: 36,
}, "PairedEventsTable");
addSheet("Unmatched Review", payload.unmatched, unmatchedFields, {
  A: 26, B: 10, C: 16, D: 30, E: 14, F: 14, G: 16, H: 58, I: 56, J: 48,
  K: 34, L: 56, M: 48, N: 34, O: 48, P: 34, Q: 14, R: 14, S: 64, T: 18, U: 40,
}, "UnmatchedReviewTable");

const summaryInspect = await workbook.inspect({
  kind: "table",
  range: "Summary!A1:C20",
  include: "values",
  tableMaxRows: 20,
  tableMaxCols: 3,
});
console.log(summaryInspect.ndjson);

const errorScan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errorScan.ndjson);

const preview = await workbook.render({ sheetName: "Unmatched Review", range: "A1:U12", scale: 1, format: "png" });
const previewBytes = new Uint8Array(await preview.arrayBuffer());
const previewPath = outputPath.replace(/\.xlsx$/i, ".preview.png");
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(previewPath, previewBytes);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`saved ${outputPath}`);

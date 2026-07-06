import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workbookPath = process.argv[2] || "data/old/old_pm_ks_human_review_2026-06-28.xlsx";

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

function rowFill(sheet, rowIndexOneBased, colCount, color) {
  const lastCol = columnLetter(colCount - 1);
  sheet.getRange(`A${rowIndexOneBased}:${lastCol}${rowIndexOneBased}`).format.fill.color = color;
}

function styleHeader(sheet, colCount) {
  const lastCol = columnLetter(colCount - 1);
  const header = sheet.getRange(`A1:${lastCol}1`);
  header.format.fill.color = "#17324D";
  header.format.font.color = "#FFFFFF";
  header.format.font.bold = true;
  header.format.rowHeight = 28;
}

function styleByRows(sheet, values, mode) {
  if (!values.length || !values[0].length) return;
  const colCount = values[0].length;
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  styleHeader(sheet, colCount);

  for (let rowIndex = 1; rowIndex < values.length; rowIndex += 1) {
    const excelRow = rowIndex + 1;
    const alternate = rowIndex % 2 === 0;
    let fill = alternate ? "#FFFFFF" : "#F8FAFC";

    if (mode === "paired") {
      fill = alternate ? "#F7FFF9" : "#E8F8EF";
    } else if (mode === "unmatched") {
      const status = String(values[rowIndex][0] || "");
      const priority = String(values[rowIndex][1] || "");
      if (priority === "1" || status.includes("Likely alias")) {
        fill = alternate ? "#FFF8D6" : "#FFEBA3";
      } else if (status.includes("PM only")) {
        fill = alternate ? "#EAF4FF" : "#D5E9FF";
      } else if (status.includes("KS only")) {
        fill = alternate ? "#F1EAFF" : "#E2D6FF";
      }
    } else if (mode === "summary") {
      fill = alternate ? "#FFFFFF" : "#EEF6FF";
    } else if (mode === "howto") {
      fill = alternate ? "#FFFFFF" : "#F1F7ED";
    }
    rowFill(sheet, excelRow, colCount, fill);
  }

  const used = sheet.getRange(`A1:${columnLetter(colCount - 1)}${values.length}`);
  used.format.wrapText = true;
  used.format.verticalAlignment = "top";
  used.format.borders = { preset: "inside", style: "thin", color: "#D6DEE8" };
}

const input = await FileBlob.load(workbookPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const sheetModes = {
  "Summary": "summary",
  "How To Use": "howto",
  "Paired Events": "paired",
  "Unmatched Review": "unmatched",
};

for (const [sheetName, mode] of Object.entries(sheetModes)) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const used = sheet.getUsedRange(true);
  const values = used.values;
  styleByRows(sheet, values, mode);
}

const errorScan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errorScan.ndjson);

const previewRanges = {
  "Summary": "A1:C20",
  "How To Use": "A1:C5",
  "Paired Events": "A1:P14",
  "Unmatched Review": "A1:U14",
};

const fs = await import("node:fs/promises");
for (const [sheetName, range] of Object.entries(previewRanges)) {
  const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  const previewBytes = new Uint8Array(await preview.arrayBuffer());
  const suffix = sheetName.toLowerCase().replaceAll(" ", "-");
  await fs.writeFile(workbookPath.replace(/\.xlsx$/i, `.${suffix}.styled-preview.png`), previewBytes);
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(workbookPath);
console.log(`updated ${workbookPath}`);

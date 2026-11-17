// Browser-only adapter for flat import tables. Product headers and validation
// belong to the generated page, not the shared workbook code.
(() => {
  const {utils, read, writeFile} = window.XLSX;

  async function readTable(file) {
    const data = new Uint8Array(await file.arrayBuffer());
    if (/\.xlsx$/i.test(file.name) && !(data[0] === 0x50 && data[1] === 0x4b)) {
      throw new Error('无法读取 XLSX 工作簿');
    }
    const book = read(data, {type: 'array', sheets: 0, raw: true});
    const sheet = book.Sheets[book.SheetNames[0]];
    if (!sheet || !sheet['!ref']) throw new Error('工作表为空');
    const {s, e} = utils.decode_range(sheet['!ref']);
    if ((e.r - s.r + 1) * (e.c - s.c + 1) > 1000000) throw new Error('工作表范围过大');
    return utils.sheet_to_json(sheet, {header: 1, defval: '', raw: false, blankrows: false});
  }

  function downloadTable(filename, table) {
    const book = utils.book_new();
    const sheet = utils.aoa_to_sheet(table.map(row => row.map(value => String(value ?? ''))));
    sheet['!cols'] = (table[0] || []).map(value => ({wch: Math.max(15, String(value).length * 2)}));
    utils.book_append_sheet(book, sheet, 'Sheet1');
    writeFile(book, filename, {bookType: 'xlsx', compression: true});
  }

  // Return [] from applyRow only after the row is accepted. Validate before
  // committing any row mutation. Keep the original cell strings for export.
  function processTable(table, reasonHeader, applyRow) {
    if (!table.length) throw new Error('缺少表头');
    const columns = table[0].map((name, index) => ({name, index}))
      .filter(({name}) => String(name).trim() !== reasonHeader);
    const headers = columns.map(({name}) => name);
    const failureTable = [[...headers, reasonHeader]];
    let success = 0;
    for (const row of table.slice(1)) {
      const values = columns.map(({index}) => row[index] ?? '');
      if (values.every(value => String(value).trim() === '')) continue;
      const errors = applyRow([...values], [...headers]);
      if (!Array.isArray(errors) || errors.some(error => typeof error !== 'string' || !error.trim())) {
        throw new TypeError('applyRow must return an array of error messages');
      }
      if (errors.length) failureTable.push([...values, errors.join('；')]);
      else success++;
    }
    const failed = failureTable.length - 1;
    if (!success && !failed) throw new Error('文件没有有效数据行');
    return {success, failed, failureTable: failed ? failureTable : null};
  }

  window.PrototypeWorkbook = Object.freeze({readTable, downloadTable, processTable});
})();

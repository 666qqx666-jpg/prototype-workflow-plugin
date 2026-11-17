import {readFileSync, writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';

const asset = name => readFileSync(new URL('../assets/workbook/' + name, import.meta.url), 'utf8');
const marker = '<!-- prototype-workbook-tools -->';

export function embedWorkbookTools(html) {
  const parts = html.split(marker);
  if (parts.length !== 2) throw new Error('Expected one workbook tools marker');
  const license = asset('sheetjs-0.20.3/LICENSE').replaceAll('*/', '* /');
  const sources = [asset('sheetjs-0.20.3/xlsx.full.min.js'), asset('table-io.js')];
  const scripts = sources.map(source => '<script>\n' + source.replace(/<\/script/gi, '<\\/script') + '\n</script>');
  return parts[0] + '<script>/* SheetJS CE 0.20.3\n' + license + '\n*/</script>\n' + scripts.join('\n') + parts[1];
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [input, output] = process.argv.slice(2);
  if (!input || !output) throw new Error('Usage: node embed-workbook-tools.mjs source.html output.html');
  writeFileSync(output, embedWorkbookTools(readFileSync(input, 'utf8')), {flag: 'wx'});
}

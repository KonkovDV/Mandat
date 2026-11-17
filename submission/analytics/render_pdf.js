// Печатает HTML-документы пакета аналитики в PDF через Chromium (Playwright): размер страницы и колонтитулы
// задаёт CSS (@page), PDF получает теговую структуру и закладки по заголовкам.
//
//   node render_pdf.js doc1.html doc1.pdf [doc2.html doc2.pdf ...]
//
// Нужен Playwright с Chromium: npm i -g playwright && npx playwright install chromium.
let chromium;
try {
  ({ chromium } = require("playwright"));
} catch {
  console.error("нужен Playwright: npm i -g playwright && npx playwright install chromium");
  process.exit(1);
}

(async () => {
  const pairs = process.argv.slice(2);
  if (pairs.length === 0 || pairs.length % 2) {
    console.error("использование: node render_pdf.js doc.html doc.pdf [...]");
    process.exit(2);
  }
  const browser = await chromium.launch();
  const page = await browser.newPage();
  for (let i = 0; i < pairs.length; i += 2) {
    await page.goto(`file://${pairs[i]}`, { waitUntil: "load" });
    await page.evaluate(() => document.fonts.ready);
    // Неиспользуемые начертания остаются «unloaded» — это нормально; ошибка — только «error».
    const broken = await page.evaluate(() =>
      [...document.fonts].filter((f) => f.family.includes("Golos") && f.status === "error").map((f) => f.weight),
    );
    if (broken.length) throw new Error(`шрифт Golos Text не загрузился: ${broken.join(", ")}`);
    await page.pdf({ path: pairs[i + 1], preferCSSPageSize: true, printBackground: true, tagged: true, outline: true });
    console.log(`готово: ${pairs[i + 1]}`);
  }
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});

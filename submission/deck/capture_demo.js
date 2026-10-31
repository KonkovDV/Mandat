// Снимает настоящую карточку демо-кейса 14 для слайда «Продукт сегодня» (assets/demo-card-14.jpg).
//
//   ./run-demo.sh                 # из корня репозитория: собирает var/demo/*.html
//   node capture_demo.js          # нужен Playwright с Chromium (npx playwright install chromium)
//
// Кадр — верх карточки: заголовок, вердикт, сильнейшее возражение, сигналы, «Режим стенда» и цепочка полномочий.
const path = require("path");
const sharp = require("sharp");

let chromium;
try {
  ({ chromium } = require("playwright"));
} catch {
  console.error("нужен Playwright: npm i -g playwright && npx playwright install chromium");
  process.exit(1);
}

const CARD = path.join(__dirname, "..", "..", "var", "demo", "14_old_director_charter_cleared.html");
const OUT = path.join(__dirname, "assets", "demo-card-14.jpg");

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1180, height: 900 }, deviceScaleFactor: 2 });
  await page.goto(`file://${CARD}`);
  const shot = await page.screenshot({ fullPage: true });
  await browser.close();
  await sharp(shot).extract({ left: 70, top: 20, width: 2220, height: 1780 }).jpeg({ quality: 90 }).toFile(OUT);
  console.log(`готово: ${OUT}`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});

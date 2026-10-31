// Снимает настоящую карточку демо-кейса 14 для слайда «Продукт сегодня» (assets/demo-card-14.jpg).
//
//   ./run-demo.sh                 # из корня репозитория: собирает var/demo/*.html
//   node capture_demo.js          # нужен Playwright с Chromium (npx playwright install chromium)
//
// Кадр — верх карточки, крупно: вердикт, сильнейшее возражение, операция, организация и сигналы.
// Ширина окна 820 пикселей: текст карточки на слайде читается с экрана (около 9 пунктов).
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
  const page = await browser.newPage({ viewport: { width: 820, height: 900 }, deviceScaleFactor: 2 });
  await page.goto(`file://${CARD}`);
  const box = await page.evaluate(() => {
    const cards = [...document.querySelectorAll("div.card")];
    const first = cards[0].getBoundingClientRect();
    const stand = document.querySelector("div.card.stand");
    const end = (stand ? stand.getBoundingClientRect().top : first.bottom + 220) - 8;
    return { x: first.left - 8, y: first.top - 8, width: first.width + 16, height: end - first.top + 8 };
  });
  const shot = await page.screenshot({ clip: box });
  await browser.close();
  await sharp(shot).flatten({ background: "#F4F6F9" }).jpeg({ quality: 92 }).toFile(OUT);
  const meta = await sharp(OUT).metadata();
  console.log(`готово: ${OUT} — ${meta.width}×${meta.height}`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});

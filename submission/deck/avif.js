// AVIF рядом с WebP для рендера цепочки: тёмный кадр со свечением в AVIF вдвое легче.
//
//   node submission/deck/avif.js          # из корня репозитория; sharp — из submission/deck
//
// Браузер выбирает AVIF через <picture> и image-set() в стилях, а без поддержки берёт прежний WebP.
// Фото Анны, значки и слайды остаются в WebP: выигрыш на них 5–10 %, а повторное сжатие добавляет артефакты.
// «Четыре экрана» пишет в AVIF сам device_art.js — из исходника без потерь.
"use strict";
const fs = require("fs");
const path = require("path");
const sharp = require("sharp");

const IMG = path.join(__dirname, "..", "..", "site", "img");
const FILES = ["chain-768", "chain-1536"];

(async () => {
  let before = 0, after = 0;
  for (const name of FILES) {
    const src = path.join(IMG, `${name}.webp`), out = path.join(IMG, `${name}.avif`);
    if (!fs.existsSync(src)) throw new Error(`нет ${path.relative(process.cwd(), src)}`);
    // Тёмный градиент полосует при сильном сжатии: качество выше обычного, цвет без прореживания.
    await sharp(src).avif({ quality: 62, effort: 7, chromaSubsampling: "4:4:4" }).toFile(out);
    const a = fs.statSync(src).size, b = fs.statSync(out).size;
    before += a;
    after += b;
    console.log(`${name}: ${Math.round(a / 1024)} → ${Math.round(b / 1024)} КБ`);
  }
  console.log(`итого: ${Math.round(before / 1024)} → ${Math.round(after / 1024)} КБ`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});

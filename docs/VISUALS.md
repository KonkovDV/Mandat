# Визуальная система: амбассадор, изображения из Figma, шрифты

Как выглядят колода и сайт и почему так, где лежит каждое изображение, как сделать его заново и какими промтами.
Изображения сгенерированы в Figma (инструмент generate_image, модели gpt-image-2.5); числа и схемы продукта на них не
рисуются, они остаются кодом.

## Правила

1. **Продукт и команда — только настоящие.** Скриншоты карточки, вывод терминала, графики и цифры собираются из кода
   и `evidence.json`. Генерация не рисует ни интерфейс «Мандата», ни числа: модель ошибается в тексте, а числа на
   слайдах сверяются с реестром утверждений.
2. **Анна — вымышленный амбассадор.** Её лица нет у реальных людей, она не член команды и не сотрудник банка.
   Рядом с каждым её фото — подпись «Анна — вымышленный амбассадор «Мандата»» (на сайте это проверяет
   `tests/test_site.py`, на слайдах подпись стоит в углу кадра). На слайде «Команда» только настоящие фото.
3. **Без чужих знаков.** На изображениях нет логотипов, вывесок и надписей, в том числе ВТБ: колода — материал
   команды, а не банка.
4. **Деловой образ.** Тёмно-синий двубортный костюм с золотыми пуговицами, белая шёлковая блузка, жемчужные серьги,
   тонкие золотые часы. Студийный свет, фон в палитре ВТБ. В промтах явно: «elegant and professional, not provocative».
5. **Один персонаж — одно описание.** Внешность Анны в каждом промте описана одними и теми же словами, поэтому кадры
   похожи друг на друга без загрузки референса.

## Где что используется

| Файл | Что на нём | Колода | Сайт |
|---|---|---|---|
| `anna-hero` | Анна в полный рост, тёмно-синий фон | обложка (кадр сливается с фоном) | герой главной, превью ссылки `og.jpg` |
| `anna-final` | Анна с планшетом, улыбается | финальный слайд | шапка страницы «Слайды» |
| `anna-vision` | Анна у голограммы: много источников сходятся в щит | слайд 18 «Банк будущего» | — |
| `anna-office` | Анна в офисе банка вечером, проверяет документ | — | баннер страницы «Пилот» |
| `bg-lux` | атласные волны, голубой свет, золотые нити | фон обложки, оглавления приложения и финала | тёмная полоса «Ценность для банка», `og.jpg` |
| `icons-3d` | девять 3D-значков из стекла и металла | слайды 2 и 21 | шапки страниц, карточки обзора, 404 |

Значки листа по строкам: кейсы (папка с печатью), захват (песочные часы с колоколом), проверка (щит с ключом),
экономика (монеты и столбцы), пилот (календарь с флажком), закон (весы), критерии (планшет с галочками),
поиск (лупа над документом), защита (замок на сервере).

## Шрифты

| Роль | Шрифт | Почему |
|---|---|---|
| Заголовки, крупные числа, бренд | Manrope (переменный 200–800, на сайте 800, в колоде Bold и ExtraBold) | геометрический гротеск с плотным рисунком: заголовок держится даже в две строки, цифры ровные |
| Текст, таблицы, подписи | Golos Text (ParaType) | гуманистический гротеск, сделан для экранного русского текста |
| Одно акцентное слово | Cormorant Garamond SemiBold Italic | курсив антиквы в паре с гротеском — приём редакционной вёрстки; на странице не больше одного-двух акцентов |

Все три — SIL Open Font License, лицензии лежат рядом со шрифтами (`submission/deck/fonts/OFL*.txt`, на сайте —
`assets/fonts/`). Фирменный VTB Group UI не используется: его лицензия — только для Группы ВТБ.

Приёмы набора, как на сильных продуктовых сайтах 2025–2026 годов:

- крупный кегль — с отрицательным трекингом (заголовок −0,03 em) и коротким интерлиньяжем (1,04), текст — 1,6;
- заголовки — `text-wrap: balance`, абзацы — `text-wrap: pretty`; в колоде два последних слова заголовка связаны
  неразрывным пробелом, `qa_deck.py` ловит висячее слово;
- числа в плашках — табличные цифры (`tabular-nums`), чтобы колонки не прыгали;
- надзаголовки — капитель с разрядкой 0,14 em;
- русская типографика на сайте и в колоде одна: тире не начинает строку, предлог не висит в конце, число не
  отрывается от единицы (`_typo` в `scripts/build_site.py`, `typo()` в `submission/deck/build_deck.js`);
- шрифты сайта — WOFF2 с `unicode-range`, `font-display: swap`; заранее грузятся только три файла первого экрана.

## Как сделать заново

1. В Figma — файл-дизайн; инструмент generate_image с промтом из раздела ниже. Размер — как в таблице: вертикальный
   кадр 1024×1536, горизонтальный 1536×1024, фон 2048×1152 (модель сама выбирает ближайший поддерживаемый размер).
2. Скачайте PNG в одну папку под именами из таблицы: `anna-hero.png`, `anna-final.png`, `anna-vision.png`,
   `anna-office.png`, `bg-lux.png`, `icons-3d.png` (лист значков — с прозрачным фоном).
3. `python submission/deck/import_visuals.py ПАПКА` — JPEG для колоды в `submission/deck/assets/visuals/`, WebP двух
   ширин для `srcset` в `site/img/`, значки по клеткам листа, превью ссылки `site/og.jpg`.
4. `make deck` — колода, PDF, проверка вёрстки `qa_deck.py`, слайды для сайта. Затем
   `python scripts/build_site.py --out _site` и `node scripts/check_site_devices.js _site`: девять устройств,
   светлая и тёмная тема, разрывы слов, WCAG 2.2 AA.

## Промты

Общий портрет Анны — в каждом промте одними словами:

> Anna, a fictional brand ambassador for a corporate bank: a strikingly beautiful young woman in her late twenties with
> the poise and bone structure of a top fashion model. Long glossy dark-chestnut hair in soft polished waves past her
> shoulders, hazel-green eyes, high cheekbones, straight defined brows, luminous skin with natural texture, refined
> natural makeup with soft rose-nude lips. She wears an impeccably tailored deep navy blue double-breasted blazer with
> gold buttons, a crisp white silk blouse with a modest open collar, small pearl stud earrings and a slim gold watch.

Окончание каждого промта с Анной:

> High-end editorial fashion photography for a premium private-banking campaign, photorealistic, color graded in navy,
> white and warm skin tones. Elegant and professional, not provocative. No text, no logos, no watermarks.

| Файл | Модель, размер | Сцена (между портретом и окончанием) |
|---|---|---|
| `anna-hero` | gpt-image-2.5-flare, 1024×1536 | Vertical three-quarter-length portrait; matching high-waisted wide-leg trousers. She stands at a slight angle, one hand in her trouser pocket, chin slightly lifted, looking straight into the camera with a subtle, confident, warm smile. Seamless deep navy studio backdrop with a soft cobalt-blue glow behind her shoulders and generous dark negative space above her head and on the left. Large soft key light from front-left, delicate rim light; medium-format camera, 85 mm lens. |
| `anna-final` | gpt-image-2.5-flare, 1024×1536 | Vertical half-length portrait. She holds a slim dark tablet against her forearm, screen turned away from the camera, and smiles openly and warmly at the viewer, as if greeting a client at the end of a meeting. Deep navy backdrop with a cobalt glow and dark negative space on the right; key light from front-right; 85 mm lens. |
| `anna-office` | gpt-image-2.5-flare, 1536×1024 | Wide horizontal photograph. A calm, modern corporate banking office at blue hour, floor-to-ceiling windows with softly blurred city lights. Anna sits at a minimalist white desk on the right third, reviewing a document on a tablet with a stylus; a closed laptop and a neat stack of papers with a fountain pen. The left half is calm and out of focus for headline text. Warm desk light mixed with cool window light; 50 mm lens, shallow depth of field. No readable text on screens or papers. |
| `anna-vision` | gpt-image-2.5-flare, 1024×1536 | Cinematic vertical photograph in a dark, elegant, futuristic bank operations room. Anna stands in three-quarter profile in the lower left, raising her right hand toward a floating hologram of luminous cobalt and sky-blue lines: about a dozen glowing nodes connected by threads of light that converge into one bright node shaped like a rounded shield — many sources converge into one verified answer. Purely abstract hologram, no letters or numbers. Glossy dark floor, volumetric haze, warm rim light on her hair; 35 mm lens. |
| `bg-lux` | gpt-image-2.5-sunburst, 2048×1152 | Ultra-premium abstract background for a private-banking keynote, 16:9. Deep midnight navy (#061A4A) melting into sapphire (#0A2973). Translucent glass ribbons and satin-like waves of light sweep from the lower left to the upper right, with electric sky-blue (#00AAFF) rim light and a few very fine champagne-gold filaments. Soft bokeh, a gentle glow in the upper right; the left half stays calm and dark for white headline text. Photorealistic 3D render, no text, logos, people or symbols. |
| `icons-3d` | gpt-image-2.5-flare, 2048×2048 | A 3 by 3 sprite sheet of nine premium 3D icons for a corporate banking compliance product, each centered in its own invisible square cell with wide margins. One seamless light background. Frosted translucent glass with cobalt (#1E4FD8) and sky-blue (#46B8F0) inner glow, white satin and brushed silver details, rounded bevelled geometry, the same isometric three-quarter angle and scale, one soft light from the top left, a soft contact shadow under each object. Objects row by row: folder with documents and a seal; hourglass with a bell; shield with a check mark and a key; coins with rising bars; desk calendar with a flag; balance scales; clipboard with three checks; magnifying glass over a document; padlock on a server block. No text, letters, numbers or logos. |

Промт кадра с Анной собирается так: портрет, сцена из таблицы, окончание. Для фона и значков сцена из таблицы —
весь промт.

# Визуальная система: изображения и шрифты

Как выглядят колода и сайт и почему так, где лежит каждое изображение и как его заменить. Изображения сделаны
в Figma; числа и схемы продукта на них не рисуются, они остаются кодом.

## Правила

1. **Продукт и команда — только настоящие.** Скриншоты карточки, вывод терминала, графики и цифры собираются из кода
   и `evidence.json`. На иллюстрациях нет ни интерфейса «Мандата», ни чисел: числа на слайдах сверяются
   с реестром утверждений.
2. **Анна — образ бренда, а не участник проекта.** Она не член команды и не сотрудник банка, её лица нет у
   реальных людей. На слайде «Команда» только настоящие фото.
3. **Без чужих знаков.** На изображениях нет логотипов, вывесок и надписей, в том числе ВТБ: колода — материал
   команды, а не банка.
4. **Деловой образ.** Тёмно-синий двубортный костюм с золотыми пуговицами, белая шёлковая блузка, жемчужные серьги,
   тонкие золотые часы. Студийный свет, фон в палитре ВТБ; образ строгий и деловой.
5. **Один персонаж — один облик.** На всех кадрах у Анны одна и та же внешность и один костюм.

## Где что используется

| Файл | Что на нём | Колода | Сайт |
|---|---|---|---|
| `anna-hero` | Анна в полный рост, тёмно-синий фон | обложка (кадр сливается с фоном) | герой главной, превью ссылки `og.jpg` |
| `anna-final` | Анна с планшетом, улыбается | финальный слайд | шапка страницы «Слайды» |
| `anna-vision` | Анна у голограммы: много источников сходятся в щит | слайд 22 «Банк будущего» | — |
| `anna-office` | Анна в офисе банка вечером, проверяет документ | — | баннер страницы «Пилот» |
| `bg-lux` | атласные волны, голубой свет, золотые нити | фон обложки, оглавления приложения и финала | тёмная полоса «Ценность для банка», `og.jpg` |
| `icons-3d` | девять 3D-значков из стекла и металла | слайды 2 и 23 | шапки страниц, карточки обзора, 404 |
| `chain` | три стеклянных листа — доверенность, реестр, устав — на световой линии даты, в конце печать с галочкой | — | тёмная полоса «Цепочка полномочий» на главной |
| `devices` | сайт на большом экране, мониторе компьютера, ноутбуке, планшете, iPhone и Android: снимки настоящих страниц сборки | слайд 13 «Демо в браузере» | полоса «Шесть экранов» на главной |

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

## Цепочка и шесть экранов

`chain` сделан генерацией изображения в Figma (модель gpt-image-2.5-flare, 1536×864), исходник лежит в файле
«Мандат — схемы». Запрос: тёмно-синий, почти чёрный фон, пустая левая половина под текст; справа три тонких листа
матового стекла с одним рельефным значком на каждом (документ с подписью, фасад с колоннами, свиток с печатью);
сквозь них идёт тонкий луч `#1A59EA`→`#56B8FF` и заканчивается светящейся печатью с галочкой; студийный контровой свет,
отражения на глянцевом полу, без текста, букв и логотипов. На сайте кадр лежит отдельным слоем с растворёнными краями,
подложка повторяет край кадра, поэтому на мониторе 2560 px нет шва.

`devices` собирает `submission/deck/device_art.js` из собранного сайта: Playwright снимает шесть страниц — захват по
шагам на большом экране 2560 px, главная на мониторе компьютера 1920 px, разбор кейса в тёмной теме на ноутбуке,
проверка подписи с печатью на планшете, ось дат в тёмной теме на iPhone, план пилота на Android. Рамки устройств
нарисованы векторами в том же скрипте, без чужих макетов и логотипов производителей. Устройства стоят так, чтобы ни
одно не закрывало текст на экране другого, а в просвете между ними не оказалось объёмного предмета со страницы: он
читается как вещь между устройствами, а не как часть экрана. После правки страниц:

```bash
python scripts/build_site.py --out _site
```

```bash
node submission/deck/device_art.js _site
```

Скрипт пишет `site/img/devices-1200` и `devices-2400` в WebP и AVIF с прозрачным фоном — одна картинка для
светлой и тёмной темы. Если после пересборки поменялась высота кадра, обновите `height` у картинки в
`site/index.html`. Слайд 13 презентации берёт ту же `devices-2400.webp`, поэтому после новой картинки пересоберите
и колоду: `make deck`. На экранах видны числа сборки — автотесты и скорость проверки, — так что картинку снимают
заново и после пересборки `evidence.json`, иначе подпись «снимки настоящих страниц этой сборки» перестаёт быть правдой.

## Форматы и загрузка

- Фото, значки и слайды — WebP. Цепочка и «шесть экранов» — ещё и AVIF: тёмный кадр со свечением и снимки экранов в
  AVIF почти вдвое легче (цепочка 57 → 27 КБ, шесть экранов 190 → 111 КБ). Браузер выбирает формат сам: `<picture>`
  для картинок, `image-set()` для фона. На фото Анны AVIF выигрывает 5–10 %, а повторное сжатие добавляет
  артефакты, поэтому фото остаются в WebP. Цепочку пересжимает `node submission/deck/avif.js`.
- Значки карточек обзора показываются в 76 px и грузятся в 160 px, а не всей клеткой листа (341 px).
- Миниатюры слайдов — 400 px (`export_slides.py`): лента из 43 слайдов весит в десятки раз меньше полных кадров.
- Главная картинка страницы грузится сразу и с высоким приоритетом (`fetchpriority="high"`), без `loading="lazy"`;
  первый слайд стоит в разметке, а не только в скрипте. Шрифты первого экрана предзагружаются на каждой странице.
- Переход между страницами — View Transitions: содержимое плавно сменяется, шапка стоит на месте; при «уменьшить
  движение» переходов нет. Страница, на ссылку которой навели курсор, готовится заранее (Speculation Rules) —
  только страницы сайта, без файлов для скачивания.

## Как заменить изображения

1. Подготовьте кадры в Figma: вертикальный 1024×1536, горизонтальный 1536×1024, фон 2048×1152.
2. Выгрузите PNG в одну папку под именами из таблицы: `anna-hero.png`, `anna-final.png`, `anna-vision.png`,
   `anna-office.png`, `bg-lux.png`, `icons-3d.png` (лист значков — с прозрачным фоном).
3. `python submission/deck/import_visuals.py ПАПКА` — JPEG для колоды в `submission/deck/assets/visuals/`, WebP двух
   ширин для `srcset` в `site/img/`, значки по клеткам листа, превью ссылки `site/og.jpg`.
4. `make deck` — колода, PDF, проверка вёрстки `qa_deck.py`, слайды для сайта. Затем
   `python scripts/build_site.py --out _site`, `(cd scripts && npm ci)` и `node scripts/check_site_devices.js _site`: девять устройств,
   светлая и тёмная тема, разрывы слов, WCAG 2.2 AA.

## Предметы у разделов: стиль Apple

Язык — как у страниц продуктов Apple: один предмет крупно, стекло и металл, много воздуха, ни одной надписи на
картинке; текст и числа набирает страница. Предметы сгенерированы в Figma моделью gpt-image-2.5-sunburst сразу на
прозрачном фоне, поэтому один файл годится для светлой и тёмной темы. Каждый предмет повторяет мысль своего раздела.

| Файл | Где на сайте | Что значит | Промт |
|---|---|---|---|
| `obj-card-960` | «Проверка», рядом с заголовком | удостоверение, которое проверяет сам браузер | Apple product keynote render of a single premium credential card made of thick frosted glass, floating at a gentle three-quarter angle. Inside the glass a soft cobalt-to-sky-blue glow (#004CBC to #00AAFF) gradually fades; one corner carries a small embossed circular seal with a check mark, cut into the glass like jewellery. Crisp polished bevelled edges catch a thin white rim light, subtle refraction and caustics, a faint soft contact shadow below. Isolated object on a fully transparent background, centered with generous empty space around it. Minimal, precise, calm, ultra high-end product photography, photorealistic 3D render. No text, letters, numbers, logos, UI or people anywhere. |
| `obj-dial-640` | главная, «Сдвиньте дату операции» | ответ зависит от дня операции | Apple product render: a minimalist dial made of frosted glass and brushed aluminium, like a precision instrument, seen at a three-quarter angle. Around the dial runs a thin ring of tick marks without any numbers; exactly one short segment of the ring glows bright cobalt-to-sky-blue, marking a single chosen day, while the rest stays matte. Soft studio light from the top left, crisp bevelled edges, a faint soft contact shadow. Isolated object on a fully transparent background with generous empty space. Photorealistic 3D render, premium product photography. No text, letters, numbers, logos or UI. |
| `obj-zero-640` | главная, «Каждое число можно повторить» | 288 проверок, где «да» было бы ошибкой: 16 × 18 зелёных шаров | Apple product render: a perfectly regular square grid of small polished glass spheres, sixteen by eighteen, resting on an invisible plane and seen from a low three-quarter angle with shallow depth of field. Every sphere glows a soft calm green from within, the front rows in crisp focus, the back rows softly blurred. Precise, exact, scientific mood, delicate reflections and a faint soft shadow under the grid. Isolated on a fully transparent background with empty space around. Photorealistic 3D render, premium product photography. No text, numbers, logos or UI. |
| `obj-perimeter-640` | главная, «Данные клиента не покидают банк» | ядро закрыто в контуре банка со всех сторон | Apple product render: a compact brushed-aluminium server block with a few thin blue light lines on its front, enclosed inside a perfectly clear closed glass cube with crisp polished edges; the cube is sealed on all six sides, symbolising data that never leaves the perimeter. Soft cobalt glow inside the cube, gentle refraction, one large soft studio light from the top left, a faint soft contact shadow. Isolated on a fully transparent background with generous empty space. Photorealistic 3D render, premium product photography. No text, logos, numbers or UI. |
| `obj-weeks-640` | главная, «Пилот: шесть недель в тени» | шесть недель, луч проходит через все | Apple product render: six thin vertical panes of frosted glass standing upright in a row with even spacing, seen at a three-quarter angle, like a timeline of six weeks. A soft beam of cobalt-to-sky-blue light passes through all of them from left to right, and the last pane glows slightly brighter. Crisp polished edges, subtle refraction, a faint soft contact shadow. Isolated on a fully transparent background with generous empty space. Photorealistic 3D render, premium product photography. No text, numbers, logos or UI. |

Кадр 1024×1024, у карточки 1536×1024. PNG из Figma переводит

```bash
python submission/deck/import_visuals.py --objects ПАПКА
```

Скрипт срезает почти прозрачную дымку, обрезает кадр по предмету и пишет WebP с прозрачностью: 640 px по длинной
стороне, карточка — 960 px. На странице предмет стоит справа от заголовка раздела (`.sec-art`): текст его обтекает,
таблицы и схемы начинаются ниже и не сжимаются. На узком телефоне предмет уменьшается до 112 px и встаёт над заголовком
раздела, в печати скрыт. Текст `alt` пустой: предмет оформляет раздел, а смысл несут заголовок и текст рядом.

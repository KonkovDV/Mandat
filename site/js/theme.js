// Тема ставится до первой отрисовки: выбор из кнопки в шапке хранится в localStorage, иначе — настройка устройства.
// Класс js говорит стилям, что интерактивные части страницы будут работать.
(function () {
  var root = document.documentElement;
  root.classList.add("js");
  try {
    var saved = localStorage.getItem("mandat-theme");
    if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);
  } catch (e) {
    /* хранилище закрыто настройками браузера: остаётся тема устройства */
  }
})();

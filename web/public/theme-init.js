// Runs before the first paint (an external file: the CSP forbids inline scripts) so there is no flash of the wrong theme.
(function () {
  try {
    var t = localStorage.getItem("coach.theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) {}
})();

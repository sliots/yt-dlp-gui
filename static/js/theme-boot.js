(function () {
  var saved = localStorage.getItem("ytdlp.theme") || "system";
  var resolved = saved;
  if (saved === "system") {
    resolved = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themePreference = saved;
})();

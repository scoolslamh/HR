(() => {
  "use strict";

  window.lucide?.createIcons({
    attrs: {
      "aria-hidden": "true",
      "stroke-width": 1.8,
    },
  });

  document.querySelectorAll("#sidebar-menu .nav-link").forEach((link) => {
    link.addEventListener("click", () => {
      const menu = document.getElementById("sidebar-menu");
      if (menu && window.innerWidth < 992 && menu.classList.contains("show")) {
        window.bootstrap?.Collapse.getOrCreateInstance(menu).hide();
      }
    });
  });
})();

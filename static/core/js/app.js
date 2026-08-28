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

  const bulkDepartmentForm = document.getElementById("bulk-department-form");
  if (bulkDepartmentForm) {
    const selectAll = document.getElementById("select-all-employees");
    const selectors = Array.from(document.querySelectorAll(".employee-selector"));
    const commonDepartment = document.getElementById("common-department");
    document.getElementById("apply-common-department")?.addEventListener("click", () => {
      if (!commonDepartment.value) return;
      selectors.filter((box) => box.checked).forEach((box) => {
        box.closest("tr").querySelector(".employee-department").value = commonDepartment.value;
      });
    });
    selectAll?.addEventListener("change", () => {
      selectors.forEach((box) => { box.checked = selectAll.checked; });
    });
    bulkDepartmentForm.addEventListener("submit", (event) => {
      const selectedCount = selectors.filter((box) => box.checked).length;
      if (selectedCount && !window.confirm(`سيتم حفظ إسنادات ${selectedCount} موظف. هل تريد المتابعة؟`)) {
        event.preventDefault();
      }
    });
  }
})();

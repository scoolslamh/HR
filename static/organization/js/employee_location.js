(() => {
  "use strict";

  const departmentField = document.getElementById("id_department");
  const locationField = document.getElementById("id_location");
  const managerField = document.getElementById("id_manager_employee");
  const mappingElement = document.getElementById("department-signing-locations");
  const headsElement = document.getElementById("department-heads");

  if (!departmentField || !locationField || !managerField || !mappingElement || !headsElement) {
    return;
  }

  const signingLocations = JSON.parse(mappingElement.textContent);
  const departmentHeads = JSON.parse(headsElement.textContent);

  departmentField.addEventListener("change", () => {
    const signingLocationId = signingLocations[departmentField.value];
    if (
      signingLocationId &&
      locationField.querySelector(`option[value="${CSS.escape(signingLocationId)}"]`)
    ) {
      locationField.value = signingLocationId;
      locationField.dispatchEvent(new Event("change", { bubbles: true }));
    }

    const departmentHeadId = departmentHeads[departmentField.value];
    if (
      departmentHeadId &&
      managerField.querySelector(`option[value="${CSS.escape(departmentHeadId)}"]`)
    ) {
      managerField.value = departmentHeadId;
      managerField.dispatchEvent(new Event("change", { bubbles: true }));
    } else {
      managerField.value = "";
      managerField.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });
})();

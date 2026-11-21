document.addEventListener("DOMContentLoaded", () => {
  const loginForm = document.querySelector(".login-form");
  const loginRoles = loginForm?.querySelectorAll('input[name="login_role"]');
  if (loginForm && loginRoles?.length) {
    const usernameLabel = document.getElementById("username-label");
    const usernameIcon = document.getElementById("username-icon");
    const usernameInput = document.getElementById("username");
    const usernameHint = document.getElementById("username-hint");
    const passwordLabel = document.getElementById("password-label");
    const updateLoginFields = () => {
      const isAdmin = loginForm.querySelector('input[name="login_role"]:checked')?.value === "admin";
      if (usernameLabel) usernameLabel.textContent = isAdmin ? "Admin username" : "Aadhaar number";
      if (usernameIcon) usernameIcon.className = `bi ${isAdmin ? "bi-person-badge" : "bi-person-vcard"}`;
      if (usernameHint) {
        usernameHint.textContent = isAdmin
          ? "Use your admin username to sign in."
          : "Use the Aadhaar number linked to your approved account.";
      }
      if (usernameInput) {
        usernameInput.placeholder = isAdmin ? "Enter your admin username" : "Enter your Aadhaar number";
        usernameInput.inputMode = isAdmin ? "text" : "numeric";
        usernameInput.maxLength = isAdmin ? 80 : 14;
        if (isAdmin) {
          usernameInput.removeAttribute("pattern");
        } else {
          usernameInput.setAttribute("pattern", "[0-9\\s-]{12,14}");
        }
      }
      if (passwordLabel) passwordLabel.textContent = isAdmin ? "Admin password" : "Password";
    };
    loginRoles.forEach((radio) => radio.addEventListener("change", updateLoginFields));
    updateLoginFields();
  }

  const sidebar = document.getElementById("sidebar");
  document.getElementById("menuToggle")?.addEventListener("click", () => {
    sidebar?.classList.toggle("open");
  });

  const modalElement = document.getElementById("confirmationModal");
  const confirmModal = modalElement && window.bootstrap
    ? window.bootstrap.Modal.getOrCreateInstance(modalElement)
    : null;
  let pendingForm = null;
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.confirmed === "true") return;
      if (!confirmModal) {
        if (!window.confirm(form.dataset.confirm)) event.preventDefault();
        return;
      }
      event.preventDefault();
      pendingForm = form;
      document.getElementById("confirmationMessage").textContent = form.dataset.confirm;
      confirmModal.show();
    });
  });
  document.getElementById("confirmAction")?.addEventListener("click", () => {
    if (!pendingForm) return;
    pendingForm.dataset.confirmed = "true";
    pendingForm.requestSubmit();
    pendingForm = null;
    confirmModal?.hide();
  });

  document.querySelectorAll(".toast-stack .alert").forEach((alert) => {
    window.setTimeout(() => {
      if (window.bootstrap) window.bootstrap.Alert.getOrCreateInstance(alert).close();
    }, 6500);
  });

  const fileInput = document.getElementById("reportFile");
  fileInput?.addEventListener("change", () => {
    const fileName = document.getElementById("fileName");
    if (fileName && fileInput.files?.length) fileName.textContent = fileInput.files[0].name;
  });
  document.getElementById("uploadForm")?.addEventListener("submit", () => {
    const button = document.getElementById("uploadButton");
    if (button) {
      button.disabled = true;
      button.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Parsing report…';
    }
  });

  const type = document.getElementById("categoryType");
  const mandatoryField = document.getElementById("mandatoryField");
  const categoryForm = document.getElementById("categoryForm");
  const resetCategory = () => {
    categoryForm?.reset();
    const id = document.getElementById("categoryId");
    if (id) id.value = "";
    const title = document.getElementById("categoryFormTitle");
    if (title) title.textContent = "Add a category";
    if (mandatoryField) mandatoryField.hidden = type?.value === "E";
  };
  type?.addEventListener("change", () => {
    if (mandatoryField) mandatoryField.hidden = type.value === "E";
  });
  document.querySelectorAll("[data-edit-category]").forEach((button) => {
    button.addEventListener("click", () => {
      document.getElementById("categoryId").value = button.dataset.id;
      document.getElementById("categoryName").value = button.dataset.name;
      document.getElementById("categoryType").value = button.dataset.type;
      document.getElementById("categoryMandatory").value = button.dataset.mandatory || "";
      document.getElementById("categoryFormTitle").textContent = "Edit category";
      if (mandatoryField) mandatoryField.hidden = button.dataset.type === "E";
      categoryForm?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });
  document.getElementById("resetCategory")?.addEventListener("click", resetCategory);
});

(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    const form = document.querySelector("[data-attendance-upload-form]");
    if (!form || !window.XMLHttpRequest || !window.FormData) return;

    const fileInput = form.querySelector('input[type="file"]');
    const submitButton = form.querySelector("[data-upload-submit]");
    const buttonLabel = form.querySelector("[data-upload-button-label]");
    const progressPanel = form.querySelector("[data-upload-progress]");
    const progress = progressPanel.querySelector('[role="progressbar"]');
    const progressBar = form.querySelector("[data-upload-bar]");
    const percentLabel = form.querySelector("[data-upload-percent]");
    const statusLabel = form.querySelector("[data-upload-status]");
    const errorAlert = form.querySelector("[data-upload-error]");

    function setProgress(value) {
      const percent = Math.max(0, Math.min(100, Math.round(value)));
      progressBar.style.width = percent + "%";
      percentLabel.textContent = percent + "%";
      progress.setAttribute("aria-valuenow", String(percent));
    }

    function restoreForm(message) {
      submitButton.disabled = false;
      buttonLabel.textContent = "رفع وإنشاء المعاينة";
      progressBar.classList.remove("progress-bar-striped", "progress-bar-animated");
      errorAlert.textContent = message;
      errorAlert.hidden = false;
    }

    form.addEventListener("submit", function (event) {
      if (!fileInput || !fileInput.files.length || submitButton.disabled) return;
      event.preventDefault();

      const request = new XMLHttpRequest();
      progressPanel.hidden = false;
      errorAlert.hidden = true;
      submitButton.disabled = true;
      buttonLabel.textContent = "جارٍ الرفع…";
      statusLabel.textContent = "جارٍ رفع الملف…";
      setProgress(0);

      request.open("POST", form.action || window.location.href, true);
      request.setRequestHeader("X-Requested-With", "XMLHttpRequest");

      request.upload.addEventListener("progress", function (uploadEvent) {
        if (uploadEvent.lengthComputable) {
          setProgress((uploadEvent.loaded / uploadEvent.total) * 100);
        }
      });

      request.upload.addEventListener("load", function () {
        setProgress(100);
        statusLabel.textContent = "اكتمل الرفع، جارٍ قراءة الملف وإنشاء المعاينة…";
        buttonLabel.textContent = "جارٍ فحص الملف…";
        progressBar.classList.add("progress-bar-striped", "progress-bar-animated");
      });

      request.addEventListener("load", function () {
        if (request.status >= 200 && request.status < 400) {
          if (request.responseURL && request.responseURL !== window.location.href) {
            window.location.assign(request.responseURL);
            return;
          }
          document.open();
          document.write(request.responseText);
          document.close();
          return;
        }
        restoreForm("تعذر إكمال الرفع بسبب استجابة غير متوقعة من الخادم. حاول مرة أخرى.");
      });

      request.addEventListener("error", function () {
        restoreForm("تعذر رفع الملف. تحقق من الاتصال ثم حاول مرة أخرى.");
      });
      request.addEventListener("abort", function () {
        restoreForm("تم إيقاف رفع الملف قبل اكتماله.");
      });

      request.send(new FormData(form));
    });
  });
})();

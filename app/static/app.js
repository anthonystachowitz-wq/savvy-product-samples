/* Transition Copilot. Progressive enhancement only. */

(function () {
  "use strict";

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  function numberList(value) {
    return String(value || "")
      .split(",")
      .map(function (item) { return item.trim(); })
      .filter(function (item) { return item.length > 0; });
  }

  /* ------------------------------------------------------------------ */
  /* Intake: map a field row to its source line and back.               */
  /* ------------------------------------------------------------------ */

  function initIntake() {
    var fieldRows = Array.prototype.slice.call(document.querySelectorAll("[data-field-index]"));
    var lines = Array.prototype.slice.call(document.querySelectorAll(".doc-line"));
    var clearButton = document.getElementById("clear-highlight");
    if (!fieldRows.length || !lines.length) {
      return;
    }

    var pinned = new Set();

    function lineIds(line) {
      return numberList(line.getAttribute("data-fields"));
    }

    function fieldFor(index) {
      return document.querySelector('[data-field-index="' + index + '"]');
    }

    function clearHot() {
      fieldRows.forEach(function (row) { row.classList.remove("is-hot"); });
      lines.forEach(function (line) { line.classList.remove("is-hot"); });
    }

    function paintPinned() {
      fieldRows.forEach(function (row) {
        row.classList.toggle("is-pinned", pinned.has(row.getAttribute("data-field-index")));
      });
      lines.forEach(function (line) {
        line.classList.toggle(
          "is-pinned",
          lineIds(line).some(function (index) { return pinned.has(index); })
        );
      });
      if (clearButton) {
        clearButton.hidden = pinned.size === 0;
      }
    }

    fieldRows.forEach(function (row) {
      var index = row.getAttribute("data-field-index");

      row.addEventListener("mouseenter", function () {
        clearHot();
        row.classList.add("is-hot");
        lines.forEach(function (line) {
          if (lineIds(line).indexOf(index) !== -1) {
            line.classList.add("is-hot");
          }
        });
      });

      row.addEventListener("mouseleave", clearHot);

      row.addEventListener("click", function () {
        if (pinned.has(index)) {
          pinned.delete(index);
        } else {
          pinned.add(index);
        }
        paintPinned();
        var target = lines.find(function (line) {
          return lineIds(line).indexOf(index) !== -1;
        });
        if (target) {
          target.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
      });

      row.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          row.click();
        }
      });
    });

    lines.forEach(function (line) {
      var ids = lineIds(line);
      if (!ids.length) {
        return;
      }

      line.addEventListener("mouseenter", function () {
        clearHot();
        ids.forEach(function (index) {
          var row = fieldFor(index);
          if (row) { row.classList.add("is-hot"); }
        });
      });

      line.addEventListener("mouseleave", clearHot);

      line.addEventListener("click", function () {
        pinned.clear();
        ids.forEach(function (index) { pinned.add(index); });
        paintPinned();
      });
    });

    if (clearButton) {
      clearButton.addEventListener("click", function () {
        pinned.clear();
        paintPinned();
      });
    }
  }

  /* ------------------------------------------------------------------ */
  /* Intake: filter the field list.                                     */
  /* ------------------------------------------------------------------ */

  function initFieldSearch() {
    var input = document.getElementById("field-search");
    if (!input) {
      return;
    }
    var groups = Array.prototype.slice.call(document.querySelectorAll("[data-group]"));
    var rows = Array.prototype.slice.call(document.querySelectorAll("[data-field-index]"));
    var count = document.getElementById("field-count-shown");

    function apply() {
      var query = input.value.trim().toLowerCase();
      var visible = 0;
      rows.forEach(function (row) {
        var match = !query || row.textContent.toLowerCase().indexOf(query) !== -1;
        row.classList.toggle("is-hidden", !match);
        if (match) { visible += 1; }
      });
      groups.forEach(function (group) {
        var any = group.querySelectorAll("[data-field-index]:not(.is-hidden)").length > 0;
        group.classList.toggle("is-hidden", !any);
      });
      if (count) {
        count.textContent = String(visible);
      }
    }

    input.addEventListener("input", apply);
    input.addEventListener("search", apply);
    apply();
  }

  /* ------------------------------------------------------------------ */
  /* Plan: click a risk to mark its account row.                        */
  /* ------------------------------------------------------------------ */

  function initRisks() {
    var risks = Array.prototype.slice.call(document.querySelectorAll("[data-risk-account]"));
    if (!risks.length) {
      return;
    }
    var allRows = Array.prototype.slice.call(document.querySelectorAll("tr[data-account]"));
    var toast = document.getElementById("plan-toast");
    var toastTimer = null;

    function showToast(message) {
      if (!toast) { return; }
      toast.textContent = message;
      toast.hidden = false;
      window.clearTimeout(toastTimer);
      toastTimer = window.setTimeout(function () {
        toast.hidden = true;
      }, 3200);
    }

    function highlight(accountId) {
      allRows.forEach(function (row) { row.classList.remove("row-highlight"); });
      risks.forEach(function (risk) { risk.classList.remove("is-active"); });

      var target = document.getElementById("acct-" + accountId);
      if (!target) {
        showToast("Account " + accountId + " is not in the current filter.");
        return;
      }
      target.classList.add("row-highlight");
      var risk = risks.find(function (item) {
        return item.getAttribute("data-risk-account") === accountId;
      });
      if (risk) { risk.classList.add("is-active"); }
      target.scrollIntoView({ block: "center", behavior: "smooth" });
    }

    risks.forEach(function (risk) {
      risk.addEventListener("click", function () {
        highlight(risk.getAttribute("data-risk-account"));
      });
      risk.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          highlight(risk.getAttribute("data-risk-account"));
        }
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Forms: confirm reset, warn on a missing role.                      */
  /* ------------------------------------------------------------------ */

  function initForms() {
    Array.prototype.slice.call(document.querySelectorAll("form[data-confirm]")).forEach(function (form) {
      form.addEventListener("submit", function (event) {
        var message = form.getAttribute("data-confirm");
        if (message && !window.confirm(message)) {
          event.preventDefault();
        }
      });
    });

    Array.prototype.slice.call(document.querySelectorAll("form.approval-form")).forEach(function (form) {
      form.addEventListener("submit", function (event) {
        var select = form.querySelector("select[name='role']");
        if (select && !select.value) {
          event.preventDefault();
          select.focus();
          if (select.reportValidity) {
            select.reportValidity();
          }
        }
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Guided tour: hide on request, remembered for the tab session.      */
  /* ------------------------------------------------------------------ */

  function initTour() {
    var tour = document.querySelector(".tour");
    if (!tour) { return; }
    var close = tour.querySelector("[data-tour-close]");
    try {
      if (window.sessionStorage.getItem("savvy-tour-hidden") === "1") {
        tour.classList.add("is-hidden");
      }
    } catch (err) { /* storage can be blocked; the tour still works */ }
    if (!close) { return; }
    close.addEventListener("click", function () {
      tour.classList.add("is-hidden");
      try {
        window.sessionStorage.setItem("savvy-tour-hidden", "1");
      } catch (err) { /* ignore */ }
    });
  }

  onReady(function () {
    initIntake();
    initFieldSearch();
    initRisks();
    initForms();
    initTour();
  });
})();

/* Book IQ: open the household detail inline under its queue row, without
   moving the page. Falls back to the server-rendered #detail section without JS. */
(function () {
  "use strict";
  var currentRow = null;

  function table() { return document.querySelector(".biq-table"); }

  function closeInline() {
    var open = document.querySelector(".biq-expand-row");
    if (open) { open.remove(); }
    if (currentRow) { currentRow.classList.remove("is-expanded"); currentRow = null; }
  }

  function setUrl(rowId) {
    if (!window.history || !window.history.replaceState) { return; }
    var url = new URL(window.location.href);
    if (rowId) { url.searchParams.set("h", rowId); } else { url.searchParams.delete("h"); }
    url.hash = "";
    window.history.replaceState(null, "", url.toString());
  }

  function insertDetail(row, detail) {
    detail.removeAttribute("id");
    var tr = document.createElement("tr");
    tr.className = "biq-expand-row";
    var td = document.createElement("td");
    td.colSpan = 6;
    td.appendChild(detail);
    tr.appendChild(td);
    row.after(tr);
    row.classList.add("is-expanded");
    currentRow = row;
  }

  function openInline(link) {
    var row = link.closest("tr[data-biq-row]");
    if (!row) { return; }
    if (currentRow === row) {
      closeInline();
      setUrl("");
      return;
    }
    var href = link.getAttribute("href");
    fetch(href, { credentials: "same-origin" })
      .then(function (response) { return response.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var detail = doc.getElementById("detail");
        if (!detail) { window.location.href = href; return; }
        closeInline();
        insertDetail(row, document.importNode(detail, true));
        setUrl(row.getAttribute("data-biq-row"));
      })
      .catch(function () { window.location.href = href; });
  }

  function loadFilter(link) {
    var href = link.getAttribute("href");
    var panel = document.getElementById("book");
    fetch(href, { credentials: "same-origin" })
      .then(function (response) { return response.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = doc.getElementById("book");
        if (!fresh || !panel) { window.location.href = href; return; }
        panel.innerHTML = fresh.innerHTML;
        currentRow = null;
        var url = new URL(window.location.href);
        url.search = new URL(href, window.location.href).search;
        url.hash = "";
        window.history.replaceState(null, "", url.toString());
      })
      .catch(function () { window.location.href = href; });
  }

  function init() {
    var tbl = table();
    if (!tbl) { return; }
    document.addEventListener("click", function (event) {
      var closeLink = event.target.closest(".biq-close");
      if (closeLink && document.querySelector(".biq-expand-row")) {
        event.preventDefault();
        closeInline();
        setUrl("");
        return;
      }
      var filter = event.target.closest("a[data-biq-filter]");
      if (filter) {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) { return; }
        event.preventDefault();
        loadFilter(filter);
        return;
      }
      var link = event.target.closest("a[data-biq-open]");
      if (!link) { return; }
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) { return; }
      event.preventDefault();
      openInline(link);
    });
    // A deep link renders the detail below the table. Move it inline under its row.
    var bottom = document.getElementById("detail");
    var rowId = new URLSearchParams(window.location.search).get("h");
    if (bottom && rowId) {
      var row = tbl.querySelector('tr[data-biq-row="' + rowId.replace(/"/g, '\\"') + '"]');
      if (row) {
        var clone = document.importNode(bottom, true);
        bottom.remove();
        insertDetail(row, clone);
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
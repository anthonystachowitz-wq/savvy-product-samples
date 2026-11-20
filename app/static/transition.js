/* Transition pages: switch source documents and plan filters in place,
   without reloading or jumping the page. Progressive enhancement: without JS the
   links still navigate, with their anchors. */
(function () {
  "use strict";

  function loadInPlace(url) {
    var target = new URL(url, window.location.href);
    target.hash = "";
    var requestUrl = target.pathname + target.search;
    fetch(requestUrl, { credentials: "same-origin" })
      .then(function (response) { return response.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = doc.querySelector(".content");
        var current = document.querySelector(".content");
        if (!fresh || !current) { window.location.href = target.toString(); return; }
        current.innerHTML = fresh.innerHTML;
        document.title = doc.title || document.title;
        window.history.replaceState(null, "", target.toString());
        if (typeof window.transitionInitPage === "function") {
          window.transitionInitPage();
        }
      })
      .catch(function () { window.location.href = target.toString(); });
  }

  document.addEventListener("click", function (event) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) { return; }
    var link = event.target.closest("a");
    if (!link) { return; }
    var isDocTab = link.classList.contains("doc-tab");
    var isFilter = !!link.closest(".filters");
    if (!isDocTab && !isFilter) { return; }
    var href = link.getAttribute("href") || "";
    if (!href || href.charAt(0) === "#") { return; }
    event.preventDefault();
    loadInPlace(href);
  });
})();

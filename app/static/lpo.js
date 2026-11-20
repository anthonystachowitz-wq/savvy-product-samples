/* LP Ops: packet tabs, stage filters, and packet links update the page in
   place instead of reloading and jumping to an anchor. Without JS they still
   navigate normally. */
(function () {
  "use strict";

  function findLink(href) {
    var links = document.querySelectorAll("a[href]");
    for (var i = 0; i < links.length; i += 1) {
      if (links[i].getAttribute("href") === href) { return links[i]; }
    }
    return null;
  }

  function loadInPlace(url, clickedHref, clickedTop) {
    var target = new URL(url, window.location.href);
    target.hash = "";
    var requestUrl = target.pathname + target.search;
    fetch(requestUrl, { credentials: "same-origin" })
      .then(function (response) { return response.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = doc.querySelector(".suite-main");
        var current = document.querySelector(".suite-main");
        if (!fresh || !current) { window.location.href = target.toString(); return; }
        current.innerHTML = fresh.innerHTML;
        document.title = doc.title || document.title;
        window.history.replaceState(null, "", target.toString());
        if (typeof window.focusInit === "function") {
          window.focusInit();
        }
        // Keep the control the user clicked at exactly the same viewport spot.
        var next = findLink(clickedHref);
        if (next && typeof clickedTop === "number") {
          var delta = next.getBoundingClientRect().top - clickedTop;
          if (Math.abs(delta) > 0.5) { window.scrollBy(0, delta); }
        }
      })
      .catch(function () { window.location.href = target.toString(); });
  }

  document.addEventListener("click", function (event) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) { return; }
    var link = event.target.closest("a");
    if (!link) { return; }
    var href = link.getAttribute("href") || "";
    if (!href || href.charAt(0) === "#") { return; }
    var target;
    try { target = new URL(href, window.location.href); } catch (err) { return; }
    if (target.origin !== window.location.origin || target.pathname !== "/lpo") { return; }
    event.preventDefault();
    loadInPlace(href, href, link.getBoundingClientRect().top);
  });
})();
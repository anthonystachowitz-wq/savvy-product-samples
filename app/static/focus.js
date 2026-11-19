/* Focus list: one record opens in place. Works without JS because links carry
   the query param and the server renders the open card. */
(function () {
  "use strict";

  function setOpen(list, card) {
    var cards = Array.prototype.slice.call(list.querySelectorAll("[data-focus-card]"));
    var opening = card && !card.classList.contains("is-open");
    cards.forEach(function (item) {
      var isTarget = opening && item === card;
      item.classList.toggle("is-open", isTarget);
      var toggle = item.querySelector("[data-focus-toggle]");
      if (toggle) { toggle.setAttribute("aria-expanded", isTarget ? "true" : "false"); }
      var body = item.querySelector("[data-focus-body]");
      if (body) {
        if (isTarget) { body.removeAttribute("hidden"); } else { body.setAttribute("hidden", ""); }
      }
    });
    list.classList.toggle("has-open", !!opening);
    if (opening) {
      var param = list.getAttribute("data-focus-param") || "h";
      var value = card.getAttribute("data-focus-id");
      if (value && window.history && window.history.replaceState) {
        var url = new URL(window.location.href);
        url.searchParams.delete("q");
        url.searchParams.delete("msg");
        url.searchParams.delete("error");
        url.searchParams.set(param, value);
        window.history.replaceState(null, "", url.toString());
      }
      var scrollTarget = card.querySelector("[data-focus-scroll]") || card;
      if (scrollTarget && scrollTarget.scrollIntoView) {
        scrollTarget.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
  }

  function init() {
    Array.prototype.slice.call(document.querySelectorAll("[data-focus-list]")).forEach(function (list) {
      var cards = Array.prototype.slice.call(list.querySelectorAll("[data-focus-card]"));
      if (!cards.length) { return; }
      cards.forEach(function (card) {
        var toggle = card.querySelector("[data-focus-toggle]");
        if (!toggle) { return; }
        toggle.addEventListener("click", function (event) {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) { return; }
          event.preventDefault();
          setOpen(list, card.classList.contains("is-open") ? null : card);
        });
      });
      var openCard = list.querySelector("[data-focus-card].is-open");
      if (openCard) {
        // Server already rendered one open. Set the surrounding state without animating.
        list.classList.add("has-open");
        var openToggle = openCard.querySelector("[data-focus-toggle]");
        if (openToggle) { openToggle.setAttribute("aria-expanded", "true"); }
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
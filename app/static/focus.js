/* Focus list: one record opens in place. Works without JS because links carry
   the query param and the server renders the open card. */
(function () {
  "use strict";

  var DURATION = 520;
  var EASE = "cubic-bezier(.2,.7,.2,1)";
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function bodyOf(card) { return card.querySelector("[data-focus-body]"); }
  function toggleOf(card) { return card.querySelector("[data-focus-toggle]"); }

  function setExpanded(card, open) {
    var toggle = toggleOf(card);
    if (toggle) { toggle.setAttribute("aria-expanded", open ? "true" : "false"); }
    card.classList.toggle("is-open", open);
  }

  function collapse(card, animate) {
    var body = bodyOf(card);
    setExpanded(card, false);
    if (!body) { return; }
    if (!animate || reduceMotion) { body.hidden = true; return; }
    body.style.overflow = "hidden";
    body.style.transition = "none";
    body.style.maxHeight = body.scrollHeight + "px";
    body.style.opacity = "1";
    void body.offsetHeight;
    body.style.transition = "max-height " + DURATION + "ms " + EASE + ", opacity " + Math.round(DURATION * 0.7) + "ms ease";
    body.style.maxHeight = "0px";
    body.style.opacity = "0";
    window.setTimeout(function () {
      if (card.classList.contains("is-open")) { return; }
      body.hidden = true;
      body.style.cssText = "";
    }, DURATION + 40);
  }

  function expand(card) {
    var body = bodyOf(card);
    if (!body) { setExpanded(card, true); return; }
    body.hidden = false;
    body.style.overflow = "hidden";
    body.style.transition = "none";
    body.style.maxHeight = "0px";
    body.style.opacity = "0";
    void body.offsetHeight;
    setExpanded(card, true);
    body.style.transition = "max-height " + DURATION + "ms " + EASE + ", opacity 240ms ease";
    window.requestAnimationFrame(function () {
      body.style.maxHeight = body.scrollHeight + "px";
      body.style.opacity = "1";
    });
    window.setTimeout(function () {
      if (!card.classList.contains("is-open")) { return; }
      body.style.maxHeight = "none";
      body.style.overflow = "";
      body.style.transition = "";
      body.style.opacity = "";
    }, DURATION + 60);
  }

  function flash(card) {
    card.classList.remove("is-fresh");
    void card.offsetHeight;
    card.classList.add("is-fresh");
    window.setTimeout(function () { card.classList.remove("is-fresh"); }, 1400);
  }

  function updateUrl(list, card) {
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
  }

  function toggleCard(list, card) {
    if (card.classList.contains("is-open")) {
      collapse(card, !reduceMotion);
    } else {
      expand(card);
      flash(card);
    }
    updateUrl(list, card);
  }

  function init() {
    Array.prototype.slice.call(document.querySelectorAll("[data-focus-list]")).forEach(function (list) {
      var cards = Array.prototype.slice.call(list.querySelectorAll("[data-focus-card]"));
      if (!cards.length) { return; }
      cards.forEach(function (card) {
        var toggle = toggleOf(card);
        if (!toggle) { return; }
        toggle.addEventListener("click", function (event) {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) { return; }
          event.preventDefault();
          toggleCard(list, card);
        });
      });
      var serverOpen = list.querySelector("[data-focus-card].is-open");
      if (serverOpen) {
        var openToggle = toggleOf(serverOpen);
        if (openToggle) { openToggle.setAttribute("aria-expanded", "true"); }
      }
    });
  }

  window.focusInit = init;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
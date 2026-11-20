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

  function pinSummary(card) {
    var summary = toggleOf(card);
    if (!summary || reduceMotion) { return; }
    var target = window.innerWidth <= 980 ? 12 : 96;
    var start = window.performance.now();
    function tick(now) {
      var top = summary.getBoundingClientRect().top;
      var delta = top - target;
      if (Math.abs(delta) > 1) { window.scrollBy(0, delta); }
      if (now - start < DURATION + 140) { window.requestAnimationFrame(tick); }
    }
    window.requestAnimationFrame(tick);
  }

  function openCard(list, card) {
    var cards = Array.prototype.slice.call(list.querySelectorAll("[data-focus-card]"));
    var current = cards.filter(function (item) { return item.classList.contains("is-open"); })[0] || null;
    if (current && current !== card) { collapse(current, !reduceMotion); }
    if (current !== card) {
      expand(card);
      flash(card);
      list.classList.add("has-open");
      pinSummary(card);
    } else {
      collapse(card, !reduceMotion);
      list.classList.remove("has-open");
    }
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
          openCard(list, card);
        });
      });
      var serverOpen = list.querySelector("[data-focus-card].is-open");
      if (serverOpen) {
        list.classList.add("has-open");
        var openToggle = toggleOf(serverOpen);
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
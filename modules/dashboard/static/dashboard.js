// commissioning-node dashboard — the one first-party script (ADR-0006).
//
// Progressive enhancement only: every page works without this file. The server never
// trusts it; a may-disturb action still needs confirm=yes in the form and passes
// authorize(). Served from /static/ under `script-src 'self'`; no inline script, no eval.
//
// 1. Slide to confirm: a form marked data-slide keeps its required confirm checkbox and
//    submit button for no-script browsers. Here they are replaced by a track whose knob must
//    be dragged to the far end; let go anywhere short of it and the knob snaps back.
// 2. Auto-refresh: <body data-refresh="N"> refreshes the page every N seconds (the
//    <noscript> meta refresh covers no-script browsers). It fetches the same URL and swaps
//    in the new banner and <main>, so scroll and focus stay put (ADR-0006 amendment). It
//    waits while the operator is mid-action: a slide moved or a confirm box ticked. Any
//    surprise (an error, a redirect such as an expired fallback session) falls back to a
//    full reload.
// 3. Day/night: a click on the banner's slider sets the theme cookie in the background and
//    switches the theme in place, instead of navigating (which would scroll to the top).
//    If that fails, the link is followed as without script.
"use strict";

(function () {
  // Fraction of the track the knob must reach to count as confirmed.
  var CONFIRM_AT = 0.97;
  var busy = 0; // slides in progress; refresh waits while > 0
  var slides = []; // redraw functions of the slides on the page now

  function enhance(form) {
    var box = form.querySelector('input[type="checkbox"][name="confirm"]');
    var button = form.querySelector('button[type="submit"]');
    if (!box || !button) return;
    var label = button.textContent.trim();

    var track = document.createElement("div");
    track.className = "slide";
    track.tabIndex = 0;
    track.setAttribute("role", "slider");
    track.setAttribute("aria-label", "Slide to " + label.toLowerCase());
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.setAttribute("aria-valuenow", "0");
    var fill = document.createElement("span");
    fill.className = "slide-fill";
    var text = document.createElement("span");
    text.className = "slide-text";
    text.textContent = "Slide to " + label.toLowerCase();
    var knob = document.createElement("span");
    knob.className = "slide-knob";
    knob.setAttribute("aria-hidden", "true");
    knob.textContent = "››";
    track.append(fill, text, knob);
    button.after(track);
    form.classList.add("js-slide");

    var dragging = false, startX = 0, startPos = 0, pos = 0, sent = false;

    function room() {
      return Math.max(1, track.clientWidth - knob.offsetWidth - 4);
    }
    function show(p, animate) {
      pos = Math.min(1, Math.max(0, p));
      track.classList.toggle("snap", !!animate);
      knob.style.transform = "translateX(" + pos * room() + "px)";
      fill.style.width = (pos * room() + knob.offsetWidth / 2) + "px";
      track.setAttribute("aria-valuenow", String(Math.round(pos * 100)));
    }
    function confirm() {
      if (sent) return;
      sent = true;
      show(1, true);
      track.classList.add("done");
      text.textContent = label + "…";
      box.checked = true;
      form.requestSubmit ? form.requestSubmit(button) : form.submit();
    }
    function release() {
      if (!dragging) return;
      dragging = false;
      busy = Math.max(0, busy - 1);
      if (pos >= CONFIRM_AT) confirm();
      else show(0, true);
    }

    knob.addEventListener("pointerdown", function (e) {
      if (sent) return;
      dragging = true;
      busy += 1;
      startX = e.clientX;
      startPos = pos;
      knob.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    knob.addEventListener("pointermove", function (e) {
      if (dragging) show(startPos + (e.clientX - startX) / room(), false);
    });
    knob.addEventListener("pointerup", release);
    knob.addEventListener("pointercancel", function () { if (dragging) { pos = 0; release(); } });
    knob.addEventListener("lostpointercapture", release);

    // Keyboard: arrows move the knob, Enter confirms only at the far end, Escape resets.
    track.addEventListener("keydown", function (e) {
      if (sent) return;
      var step = { ArrowRight: 0.1, ArrowUp: 0.1, ArrowLeft: -0.1, ArrowDown: -0.1 }[e.key];
      if (step) { show(pos + step, true); e.preventDefault(); }
      else if (e.key === "Home" || e.key === "Escape") { show(0, true); e.preventDefault(); }
      else if (e.key === "End") { show(1, true); e.preventDefault(); }
      else if (e.key === "Enter" && pos >= CONFIRM_AT) { confirm(); e.preventDefault(); }
    });
    track.addEventListener("blur", function () { if (!sent && !dragging) show(0, true); });
    slides.push(function () { show(pos, false); });
    show(0, false);
  }

  function enhanceAll() {
    slides = [];
    document.querySelectorAll("form[data-slide]").forEach(enhance);
  }
  window.addEventListener("resize", function () { slides.forEach(function (f) { f(); }); });

  // The operator is in the middle of something the refresh would undo.
  function held() {
    return busy > 0 ||
      Array.prototype.some.call(document.querySelectorAll(".slide"),
        function (t) { return t.getAttribute("aria-valuenow") !== "0"; }) ||
      document.querySelector('main input[type="checkbox"][name="confirm"]:checked') !== null;
  }

  function focusables() {
    return document.querySelectorAll("header.banner a, header.banner button, main a, " +
                                      "main button, main input, main [tabindex]");
  }

  function swap(html) {
    var next = new DOMParser().parseFromString(html, "text/html");
    var pairs = [["header.banner", next.querySelector("header.banner")],
                 ["main", next.querySelector("main")]];
    if (pairs.some(function (p) { return !p[1] || !document.querySelector(p[0]); })) {
      return false;
    }
    // Controls carry no ids, so focus is kept by position among them; the layout is stable
    // between refreshes, and a control that moved only loses focus, nothing else.
    var at = Array.prototype.indexOf.call(focusables(), document.activeElement);
    pairs.forEach(function (p) { document.querySelector(p[0]).replaceWith(p[1]); });
    document.title = next.title;
    enhanceAll();
    // A theme chosen while the fetch ran is newer than the fetched banner.
    var theme = document.documentElement.getAttribute("data-theme");
    if (theme) markTheme(theme);
    if (at >= 0 && focusables()[at]) focusables()[at].focus({ preventScroll: true });
    return true;
  }

  function autoRefresh() {
    var seconds = Number(document.body.dataset.refresh);
    if (!(seconds > 0)) return;
    function later(s) { setTimeout(tick, s * 1000); }
    function tick() {
      if (held()) return later(1);
      fetch(location.href, { credentials: "same-origin", headers: { "Accept": "text/html" } })
        .then(function (res) {
          if (!res.ok || res.redirected) throw new Error("refresh: " + res.status);
          return res.text();
        })
        .then(function (html) {
          // Re-check: the operator may have started something while the fetch ran.
          if (held()) return later(1);
          if (!swap(html)) throw new Error("refresh: unexpected page");
          later(seconds);
        })
        .catch(function () { location.reload(); });
    }
    later(seconds);
  }

  function themeOf(a) { return new URL(a.href).pathname.split("/").pop(); }
  function markTheme(theme) {
    document.querySelectorAll(".theme a[href]").forEach(function (a) {
      if (themeOf(a) === theme) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
  }

  // Delegated, so it survives the refresh replacing the banner.
  document.addEventListener("click", function (e) {
    var link = e.target.closest && e.target.closest(".theme a[href]");
    if (!link || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var theme = themeOf(link);
    e.preventDefault();
    // The cookie is HttpOnly, so the server sets it; its redirect is not followed.
    fetch(link.href, { credentials: "same-origin", redirect: "manual" })
      .then(function (res) {
        if (res.type !== "opaqueredirect" && !res.ok) throw new Error("theme: " + res.status);
        document.documentElement.setAttribute("data-theme", theme);
        markTheme(theme);
      })
      .catch(function () { location.href = link.href; });
  });

  enhanceAll();
  autoRefresh();
})();

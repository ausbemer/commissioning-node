// commissioning-node dashboard — the one first-party script (ADR-0006).
//
// Progressive enhancement only: every page works without this file. The server never
// trusts it; a may-disturb action still needs confirm=yes in the form and passes
// authorize(). Served from /static/ under `script-src 'self'`; no inline script, no eval.
//
// 1. Slide to confirm: a form marked data-slide keeps its required confirm checkbox and
//    submit button for no-script browsers. Here they are replaced by a track whose knob must
//    be dragged to the far end; let go anywhere short of it and the knob snaps back.
// 2. Auto-refresh: <body data-refresh="N"> reloads the page every N seconds (the
//    <noscript> meta refresh covers no-script browsers), but never during a slide.
"use strict";

(function () {
  // Fraction of the track the knob must reach to count as confirmed.
  var CONFIRM_AT = 0.97;
  var busy = 0; // slides in progress; refresh waits while > 0

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
    window.addEventListener("resize", function () { show(pos, false); });
    show(0, false);
  }

  function autoRefresh() {
    var seconds = Number(document.body.dataset.refresh);
    if (!(seconds > 0)) return;
    function tick() {
      var slideHeld = busy > 0 || Array.prototype.some.call(
        document.querySelectorAll(".slide"),
        function (t) { return t.getAttribute("aria-valuenow") !== "0"; });
      if (slideHeld) setTimeout(tick, 1000);
      else location.reload();
    }
    setTimeout(tick, seconds * 1000);
  }

  document.querySelectorAll("form[data-slide]").forEach(enhance);
  autoRefresh();
})();

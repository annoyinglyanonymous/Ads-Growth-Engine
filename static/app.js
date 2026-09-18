/* The only script in this app.
 *
 * Every chart already carries native <title> elements, so hovering a bar or a
 * point gives a tooltip with no JavaScript at all. This adds the one thing SVG
 * cannot do natively: a crosshair on a line chart, where the useful hit target
 * is the whole vertical band and not the 2.5px dot.
 *
 * If this file fails to load, every chart stays readable and inspectable. That
 * is the point of rendering them server-side.
 */
(function () {
  "use strict";

  var tip = null;

  function show(html, x, y) {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "chart-tip";
      document.body.appendChild(tip);
    }
    tip.innerHTML = html;
    tip.hidden = false;
    var r = tip.getBoundingClientRect();
    // Flip before the edge rather than after it: a tooltip clipped by the
    // viewport is worse than no tooltip, because the number is the half that
    // gets cut off.
    var left = x + 14;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
    var top = y + 14;
    if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
    tip.style.left = Math.max(8, left) + "px";
    tip.style.top = Math.max(8, top) + "px";
  }

  function hide() { if (tip) tip.hidden = true; }

  document.addEventListener("mousemove", function (e) {
    var chart = e.target.closest ? e.target.closest('[data-chart="line"]') : null;
    if (!chart) { hide(); return; }

    var dots = chart.querySelectorAll(".dot");
    if (!dots.length) { hide(); return; }

    var box = chart.getBoundingClientRect();
    var vb = chart.viewBox.baseVal;
    var scale = box.width / (vb.width || 1);
    var localX = (e.clientX - box.left) / scale;

    var best = null, bestD = Infinity;
    for (var i = 0; i < dots.length; i++) {
      var cx = parseFloat(dots[i].getAttribute("cx"));
      var d = Math.abs(cx - localX);
      if (d < bestD) { bestD = d; best = dots[i]; }
    }
    if (!best) { hide(); return; }

    var line = chart.querySelector(".cross");
    if (!line) {
      line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("class", "cross");
      chart.insertBefore(line, chart.firstChild);
    }
    var cx = best.getAttribute("cx");
    line.setAttribute("x1", cx); line.setAttribute("x2", cx);
    line.setAttribute("y1", 0);  line.setAttribute("y2", vb.height);

    var text = best.querySelector("title");
    if (!text) { hide(); return; }
    var parts = text.textContent.split(": ");
    show('<div class="t">' + parts[1] + "</div>" + parts[0], e.clientX, e.clientY);
  });

  document.addEventListener("mouseleave", hide);
  window.addEventListener("blur", hide);
})();

/* ------------------------------------------------------------------ ask box
 *
 * Progressive enhancement, and the word is load-bearing: #ask-form is a real
 * GET form pointed at /ask, so with this file blocked the box still answers --
 * it just answers on its own page. What this adds is the answer arriving in
 * place. A console that needs a script to work at all is a console that is
 * blank exactly when something is already going wrong.
 *
 * It renders the verb's JSON and writes no prose. There is no sentence here
 * for a number to disagree with.
 */
(function () {
  "use strict";

  var form = document.getElementById("ask-form");
  var log = document.getElementById("ask-log");
  if (!form || !log || !window.fetch) return;

  var input = document.getElementById("ask-q");
  var brand = form.querySelector('input[name="brand"]');
  var busy = false;

  function bubble(cls, node) {
    var row = document.createElement("div");
    row.className = "ask-row" + (cls ? " " + cls : "");
    if (cls !== "me") {
      var orb = document.createElement("span");
      orb.className = "ask-orb";
      orb.setAttribute("aria-hidden", "true");
      row.appendChild(orb);
    }
    var b = document.createElement("div");
    b.className = "ask-bubble";
    b.appendChild(node);
    row.appendChild(b);
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
    return b;
  }

  /* textContent, never innerHTML: every string below is either a question a
   * person typed or a value that came back from the database. */
  function text(s) { return document.createTextNode(s); }

  function answer(data, into) {
    into.textContent = "";
    if (data.error) {
      into.appendChild(text(data.error));
      return;
    }
    var head = document.createElement("div");
    var verb = document.createElement("strong");
    verb.textContent = data.verb;
    head.appendChild(verb);
    head.appendChild(text(" \u2014 " + data.says));
    into.appendChild(head);

    var pre = document.createElement("pre");
    pre.textContent = JSON.stringify(data.result, null, 2);
    into.appendChild(pre);
  }

  function send(q) {
    if (busy || !q.trim()) return;
    busy = true;
    bubble("me", text(q));
    var pending = bubble("", text("Reading\u2026"));
    input.value = "";

    var url = "/ask.json?q=" + encodeURIComponent(q) +
              "&brand=" + encodeURIComponent(brand ? brand.value : "");
    fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) { answer(d, pending); })
      .catch(function () {
        /* The server is the thing that failed, so say that rather than
         * inventing a reason the question was bad. */
        pending.textContent = "Could not reach the server. The form still " +
          "works on its own page.";
      })
      .then(function () { busy = false; log.scrollTop = log.scrollHeight; });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    send(input.value);
  });

  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-ask]") : null;
    if (!b) return;
    e.preventDefault();
    send(b.getAttribute("data-ask"));
  });
})();

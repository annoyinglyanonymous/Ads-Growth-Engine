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

  /* The prose answer, written by a Claude Code session that ran the verbs.
   * Replaces the routed JSON in the bubble when it lands; the JSON stays
   * underneath it, collapsed, as the receipt for the figures in the prose. */
  function prose(data, into, routed) {
    into.className = "ask-bubble";
    into.textContent = "";
    if (!data.ok) {
      /* The session failed but the router may have answered. Keep whatever
       * was already drawn rather than replacing a good answer with an error. */
      if (routed && !routed.error) { answer(routed, into); return; }
      into.appendChild(text(data.error || "No answer."));
      return;
    }
    var p = document.createElement("div");
    p.className = "ask-prose";
    p.textContent = data.answer;
    into.appendChild(p);

    var note = document.createElement("div");
    note.className = "n";
    note.style.marginTop = "8px";
    /* Just the cost. The longer disclaimer that used to sit here was the same
     * kind of noise the answers themselves were trimmed of -- what it can and
     * cannot do belongs in the panel's header, once, not under every reply. */
    note.textContent = data.cost_usd
      ? "$" + data.cost_usd.toFixed(2)
      : "";
    into.appendChild(note);

    /* The numbers behind the sentence, one click away. */
    if (routed && !routed.error) {
      var d = document.createElement("details");
      d.style.marginTop = "8px";
      var sm = document.createElement("summary");
      sm.className = "n";
      sm.textContent = "the " + routed.verb + " output this came from";
      d.appendChild(sm);
      var pre = document.createElement("pre");
      pre.textContent = JSON.stringify(routed.result, null, 2);
      d.appendChild(pre);
      into.appendChild(d);
    }
  }

  function send(q) {
    if (busy || !q.trim()) return;
    busy = true;
    bubble("me", text(q));
    var pending = bubble("", text("Thinking…"));
    pending.className = "ask-bubble ask-thinking";
    input.value = "";

    var qs = "?q=" + encodeURIComponent(q) +
             "&brand=" + encodeURIComponent(brand ? brand.value : "");
    var routed = null;

    /* Two fetches, and only ONE of them is ever drawn on its own.
     *
     * The router returns in about a second and the session takes the better
     * part of two minutes, so the obvious design was to draw the JSON
     * immediately and swap it for the sentence later. That read badly: a wall
     * of raw keys appears, you start reading it, and it vanishes. The JSON was
     * never the answer to the question -- it is the receipt for the answer --
     * so it now waits and arrives underneath the prose, collapsed.
     *
     * The fast fetch still runs, for two reasons: the receipt needs it, and it
     * is the fallback if the session fails. It just does not render. */
    var fast = fetch("/ask.json" + qs, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) { routed = d; })
      .catch(function () { /* the session may still answer */ });

    var slow = fetch("/ask/prose.json" + qs, { headers: { "Accept": "application/json" } })
      .then(function (r) {
        /* A 404 here means the running server predates this endpoint, which
         * is a stale process rather than a broken feature -- and the two look
         * identical if the catch below just redraws the JSON. It did, once,
         * and cost a round of "why is the chatbot not working". Say which. */
        if (r.status === 404) {
          throw new Error("The dashboard is running an older version of "
            + "itself and has no /ask/prose.json. Restart it: Ctrl+C, then "
            + "python -m main.");
        }
        if (!r.ok) { throw new Error("The server answered " + r.status + "."); }
        return r.json();
      })
      .then(function (d) { prose(d, pending, routed); })
      .catch(function (e) {
        /* Something is better than nothing: if the router managed an answer,
         * show it, and say why the written one is missing. */
        pending.className = "ask-bubble";
        pending.textContent = "";
        if (routed && !routed.error) { answer(routed, pending); }
        var w = document.createElement("div");
        w.className = "n";
        w.style.marginTop = routed && !routed.error ? "8px" : "0";
        w.textContent = (e && e.message)
          ? e.message
          : "Could not reach the server for the written answer.";
        pending.appendChild(w);
      });

    Promise.all([fast, slow]).then(function () {
      busy = false;
      log.scrollTop = log.scrollHeight;
    });
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


/* ---------------------------------------------------------------------------
 * Import freshness, and the one button in this app.
 *
 * POST /refresh starts a pull in a SEPARATE process and returns at once, so
 * there is nothing to await here -- the button fires and the poll below is
 * what tells you how it went. That is also why closing the tab is harmless:
 * the run is not attached to this page, or to this request, or to this
 * browser. ui.py's block above the endpoints has the long version.
 *
 * The poll does not reload the page on its own. New numbers arriving under a
 * reader mid-sentence is the same rudeness as the /ask JSON that used to
 * appear and vanish; when a run finishes, the button offers a reload and the
 * reader decides.
 * ------------------------------------------------------------------------ */
(function () {
  var box = document.getElementById("import-freshness");
  if (!box) { return; }

  var brand  = box.getAttribute("data-brand") || "";
  var ageEl  = box.querySelector("[data-fresh-age]");
  var noteEl = box.querySelector("[data-fresh-note]");
  var btn    = box.querySelector("[data-refresh]");

  var timer = null;
  var wasRunning = false;
  var finished = false;

  function qs() { return "?brand=" + encodeURIComponent(brand); }

  function ago(hours) {
    if (hours === null || hours === undefined) { return "never"; }
    if (hours < 1)  { return Math.max(1, Math.round(hours * 60)) + " min ago"; }
    if (hours < 48) { return Math.round(hours) + "h ago"; }
    return Math.round(hours / 24) + " days ago";
  }

  /* Meta's messages are one long sentence plus our own advice appended. The
   * first sentence is the part that says what is actually wrong, and the rail
   * is 180px wide -- the old slice(0,140) cut mid-word ("...ads_read p"). The
   * whole text goes in the title attribute, where it costs nothing. */
  function shortErr(text) {
    var t = String(text || "no reason recorded").replace(/\s+/g, " ").trim();
    /* Two tails worth dropping. Meta appends a docs URL ("..., refer to
     * https://developers.facebook.com/...") and client.py appends its own
     * advice after a double full stop ("...for details.. Usually the account
     * is not assigned to the System User."). Both earn their place in the
     * tooltip and neither belongs in a 180px rail. */
    t = t.split(", refer to ")[0].split(".. ")[0];
    if (t.length <= 150) { return t.replace(/[.,;:]+$/, "") + "."; }
    var cut = t.lastIndexOf(" ", 150);
    return t.slice(0, cut > 40 ? cut : 150) + "\u2026";
  }

  function state(cls) {
    box.className = "freshness" + (cls ? " " + cls : "");
  }

  function note(text, isError) {
    noteEl.className = isError ? "n err" : "n";
    noteEl.textContent = text;
  }

  function render(d) {
    box.hidden = false;

    if (d.running) {
      state("");
      ageEl.textContent = "pulling…";
      note("An insights pull is running. This takes a few seconds.");
      btn.disabled = true;
      btn.textContent = "Refreshing…";
      wasRunning = true;
      return;
    }

    /* A run that just finished under this page. Say so, and offer the reload
     * rather than taking it -- the numbers on screen are still the old ones
     * and pretending otherwise is worse than saying they are stale. */
    if (wasRunning && !finished) {
      finished = true;
      btn.disabled = false;
      btn.textContent = "Reload for new numbers";
      btn.setAttribute("data-reload", "1");
    } else if (!finished) {
      btn.disabled = false;
      btn.textContent = "Refresh now";
    }

    ageEl.textContent = ago(d.age_hours);

    var through = d.insights_through
      ? "Insights through " + d.insights_through + "."
      : "No successful insights pull yet.";

    var failed = (d.accounts || []).filter(function (a) {
      return a.status === "failed";
    });
    var stuck = (d.accounts || []).filter(function (a) {
      return a.status === "stalled";
    });

    /* `stalled` is a row that still says `running` long after anything could
     * still be running -- an interrupted pull that nothing closed. Reporting
     * it as "pulling" is how a button greys itself out forever, so it gets its
     * own sentence and does not block the button. */
    if (stuck.length) {
      state("broken");
      note(through + " A previous run was interrupted and never closed (run "
           + stuck[0].run_id + "). Nothing is running now.", true);
      return;
    }

    /* One account failing is not the brand failing. The importer isolates per
     * account on purpose; saying "the last run failed" because the account
     * nobody reads went down is how a working import gets reported as broken.
     * Red only when NOTHING imported. */
    if (failed.length) {
      var who = failed.map(function (a) {
        return a.label || a.platform_account_id;
      }).join(", ");
      var everything = d.accounts_ok === 0;
      state(everything ? "broken" : "stale");
      noteEl.title = failed[0].error || "";
      note(through + " " + failed.length + " of " + d.accounts.length
           + " account(s) failed \u2014 " + who + ": "
           + shortErr(failed[0].error), everything);
      return;
    }

    noteEl.title = "";
    state(d.age_hours === null || d.age_hours > 24 ? "stale" : "");
    note(through);
  }

  function poll() {
    return fetch("/refresh/status" + qs(),
                 { headers: { "Accept": "application/json" } })
      .then(function (r) {
        /* Same failure the ask box learned to name: a 404 here is a dashboard
         * process older than this endpoint, not a broken feature, and the two
         * look identical if the catch just hides the card. */
        if (r.status === 404) {
          throw new Error("The dashboard is running an older version of "
            + "itself and has no /refresh/status. Restart it: Ctrl+C, then "
            + "python -m main.");
        }
        return r.json();
      })
      .then(function (d) {
        if (!d.ok) { throw new Error(d.error || "Could not read import status."); }
        render(d);
        clearTimeout(timer);
        /* Two seconds while something is running, a minute otherwise. The
         * query is two indexed reads of ads.pull; the cost of the slow poll is
         * that a pull started in another window shows up within a minute. */
        timer = setTimeout(poll, d.running ? 2000 : 60000);
      })
      .catch(function (e) {
        box.hidden = false;
        state("broken");
        ageEl.textContent = "unknown";
        note((e && e.message) || "Could not read import status.", true);
        btn.disabled = false;
      });
  }

  btn.addEventListener("click", function () {
    if (btn.getAttribute("data-reload")) { window.location.reload(); return; }

    btn.disabled = true;
    btn.textContent = "Starting…";
    fetch("/refresh" + qs(), { method: "POST",
                               headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json().then(function (d) {
        d._status = r.status; return d; }); })
      .then(function (d) {
        /* 409 is not an error to apologise for: something else is already
         * doing the thing you asked for. Fall straight through to the poll,
         * which will show it running. */
        if (!d.ok && d._status !== 409) {
          throw new Error(d.error || "Could not start the pull.");
        }
        wasRunning = true;
        return poll();
      })
      .catch(function (e) {
        state("broken");
        note((e && e.message) || "Could not start the pull.", true);
        btn.disabled = false;
        btn.textContent = "Refresh now";
      });
  });

  poll();
})();


/* ---------------------------------------------------------------------------
 * The "ask" buttons: one handler, any page.
 *
 * A button carries data-ask-url (where to POST) and data-ask-target (the id of
 * the hidden section that receives the answer). The section carries three
 * hooks: [data-analysis-state], [data-analysis-body], [data-analysis-foot].
 * /ad/<key> and /brief both use this; a third page would add two attributes
 * and no script.
 *
 * One POST, one Claude Code session, the better part of a minute. The button
 * says so while it waits rather than spinning silently: a page that looks
 * frozen gets clicked again, and every click is another session.
 *
 * POST, not GET, and not on page load. The answer is work with a cost, so it
 * happens when somebody asks for it. ui.py's endpoints make the same argument.
 * ------------------------------------------------------------------------ */
(function () {
  var buttons = document.querySelectorAll("[data-ask-url]");
  if (!buttons.length) { return; }

  Array.prototype.forEach.call(buttons, function (btn) {
    var box = document.getElementById(btn.getAttribute("data-ask-target") || "");
    if (!box) { return; }

    var url     = btn.getAttribute("data-ask-url");
    var idle    = btn.textContent.trim();
    var stateEl = box.querySelector("[data-analysis-state]");
    var bodyEl  = box.querySelector("[data-analysis-body]");
    var footEl  = box.querySelector("[data-analysis-foot]");
    var busy = false;

    function show(text, cls) {
      bodyEl.innerHTML = "";
      /* Plain paragraphs. chat.SYSTEM tells the session to write sentences
       * with no markdown, so anything arriving with asterisks in it is a
       * prompt that drifted -- rendering it as text rather than parsing it
       * keeps that visible instead of quietly formatting it away. */
      String(text).split(/\n{2,}/).forEach(function (para) {
        var p = document.createElement("p");
        p.style.margin = "0 0 10px";
        p.style.maxWidth = "80ch";
        if (cls) { p.className = cls; }
        p.textContent = para.trim();
        if (p.textContent) { bodyEl.appendChild(p); }
      });
    }

    btn.addEventListener("click", function () {
      if (busy) { return; }
      busy = true;

      box.hidden = false;
      if (footEl) { footEl.hidden = true; }
      btn.disabled = true;
      btn.textContent = "Reading\u2026";
      if (stateEl) { stateEl.textContent = "this takes about a minute"; }
      show("Reading the numbers and writing it up\u2026");
      box.scrollIntoView({ behavior: "smooth", block: "nearest" });

      fetch(url, { method: "POST", headers: { "Accept": "application/json" } })
        .then(function (r) {
          /* A 404 here is a dashboard older than this endpoint, not a broken
           * feature, and the two look identical if the catch below just
           * prints "could not reach the server". */
          if (r.status === 404) {
            throw new Error("The dashboard is running an older version of "
              + "itself and has no " + url.split("?")[0] + ". Restart it: "
              + "Ctrl+C, then python -m main.");
          }
          return r.json();
        })
        .then(function (d) {
          if (!d.ok || !d.answer) {
            throw new Error(d.error || "The session returned nothing.");
          }
          show(d.answer);
          if (footEl) { footEl.hidden = false; }
          if (stateEl) { stateEl.textContent = ""; }
        })
        .catch(function (e) {
          show((e && e.message) || "Could not reach the server.", "n err");
          if (stateEl) { stateEl.textContent = "failed"; }
        })
        .then(function () {
          busy = false;
          btn.disabled = false;
          btn.textContent = idle.replace(/^Ask about|^Explain/, function (m) {
            return m === "Explain" ? "Explain again:" : "Ask again about";
          }).replace("Explain again: this brief", "Explain again");
        });
    });
  });
})();

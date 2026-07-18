// randnotiz — home page: read status + "continue reading" card (from localStorage)
(function () {
  const ns = "br:" + window.BR_TOKEN + ":"; // keys per token, see reader.js

  // Seed the server reading state into localStorage (device switch/cache clear) — local state takes precedence
  const sp = window.SERVER_PROGRESS || {};
  (sp.done || []).forEach((id) => {
    if (!localStorage.getItem(ns + "done:" + id)) localStorage.setItem(ns + "done:" + id, "1");
  });
  if (sp.last && sp.last.slug && !localStorage.getItem(ns + "last")) {
    localStorage.setItem(ns + "last", JSON.stringify(sp.last));
  }

  const items = [...document.querySelectorAll(".chapter-list li")];
  let done = 0;
  items.forEach((li) => {
    if (localStorage.getItem(ns + "done:" + li.dataset.chId)) {
      li.classList.add("done");
      li.querySelector("[data-state]").textContent = "✓";
      done++;
    }
  });

  if (done > 0 && items.length) {
    const ps = document.getElementById("progress-summary");
    ps.hidden = false;
    document.getElementById("progress-fill").style.width = Math.round((100 * done) / items.length) + "%";
    document.getElementById("progress-label").textContent = `${done} von ${items.length} Kapiteln gelesen`;
  }

  // A manual bookmark wins over the auto "last read" card and jumps straight to the marked block.
  const bm = window.SERVER_BOOKMARK;
  if (bm && bm.slug) {
    const card = document.getElementById("continue-card");
    card.href = `/r/${window.BR_TOKEN}/k/${bm.slug}#weiterlesen`;
    card.querySelector(".continue-label").textContent = "🔖 Hier weiterlesen";
    document.getElementById("continue-title").textContent = bm.title;
    card.hidden = false;
  } else {
    try {
      const last = JSON.parse(localStorage.getItem(ns + "last") || "null");
      if (last && last.slug) {
        const card = document.getElementById("continue-card");
        card.href = `/r/${window.BR_TOKEN}/k/${last.slug}`;
        document.getElementById("continue-title").textContent = last.title;
        card.hidden = false;
      }
    } catch (e) { /* corrupt localStorage entry — ignore */ }
  }
})();

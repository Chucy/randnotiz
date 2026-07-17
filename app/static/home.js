// randnotiz — Startseite: Gelesen-Status + "Weiterlesen"-Karte (aus localStorage)
(function () {
  const ns = "br:" + window.BR_TOKEN + ":"; // Keys pro Token, siehe reader.js

  // Server-Lesestand in localStorage seeden (Gerätewechsel/Cache-Clear) — lokaler Stand hat Vorrang
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

  try {
    const last = JSON.parse(localStorage.getItem(ns + "last") || "null");
    if (last && last.slug) {
      const card = document.getElementById("continue-card");
      card.href = `/r/${window.BR_TOKEN}/k/${last.slug}`;
      document.getElementById("continue-title").textContent = last.title;
      card.hidden = false;
    }
  } catch (e) { /* kaputter localStorage-Eintrag — ignorieren */ }
})();

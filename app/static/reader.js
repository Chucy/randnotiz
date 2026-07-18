// randnotiz — reader interaction: comments, reactions, progress, convenience
(function () {
  const reader = document.getElementById("reader");
  if (!reader) return;
  const token = reader.dataset.token;
  const chapterId = parseInt(reader.dataset.chapterId, 10);
  // Namespace reading-progress keys per token — otherwise two links
  // (second book or shared device) would overwrite each other. br:font/br:hint stay device-global.
  const ns = "br:" + token + ":";

  const sheet = document.getElementById("sheet");
  const backdrop = document.getElementById("backdrop");
  const sheetQuote = document.getElementById("sheet-quote");
  const sheetComments = document.getElementById("sheet-comments");
  const commentText = document.getElementById("comment-text");
  const bookmarkToggleBtn = document.getElementById("bookmark-toggle");
  const continueBtn = document.getElementById("continue-btn");
  let currentIdx = null;
  // Manual "continue reading" bookmark — block idx if it sits in THIS chapter, else null.
  let bookmarkIdx = reader.dataset.bookmarkIdx !== "" ? parseInt(reader.dataset.bookmarkIdx, 10) : null;

  // ---------- Toast ----------
  const toastEl = document.getElementById("toast");
  let toastTimer = null;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.hidden = false;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastEl.classList.remove("show");
      setTimeout(() => (toastEl.hidden = true), 300);
    }, 2500);
  }

  // ---------- Local state (initialized from the server) ----------
  const comments = {}; // idx -> [{id, text}]
  (window.MY_COMMENTS || []).forEach((c) => {
    (comments[c.block_idx] = comments[c.block_idx] || []).push({ id: c.id, text: c.text });
  });
  const reactions = {}; // idx -> Set(kind)
  (window.MY_REACTIONS || []).forEach((r) => {
    (reactions[r.block_idx] = reactions[r.block_idx] || new Set()).add(r.kind);
  });

  const EMOJI = { herz: "❤️", frage: "❓", gaehn: "😴" };

  function renderMeta(idx) {
    const meta = document.querySelector(`[data-meta="${idx}"]`);
    if (!meta) return;
    const parts = [];
    (reactions[idx] ? [...reactions[idx]] : []).forEach((k) => parts.push(EMOJI[k]));
    if (comments[idx] && comments[idx].length) parts.push(`💬 ${comments[idx].length}`);
    meta.textContent = parts.join("  ");
    meta.closest(".block").classList.toggle("has-note", parts.length > 0);
  }
  Object.keys(comments).forEach(renderMeta);
  Object.keys(reactions).forEach(renderMeta);

  // ---------- Comment sheet ----------
  function openSheet(idx, blockEl) {
    currentIdx = idx;
    const text = blockEl.textContent.trim().replace(/\s+/g, " ");
    const img = blockEl.querySelector("img");
    if (img && !text) {
      const name = img.alt || (img.src.split("/").pop() || "").replace(/\.[a-z]+$/i, "").replace(/-/g, " ");
      sheetQuote.textContent = "📊 Grafik: " + name;
    } else {
      sheetQuote.textContent = "„" + text.slice(0, 140) + (text.length > 140 ? " …" : "") + "“";
    }
    renderSheetComments(idx);
    document.querySelectorAll(".reaction").forEach((btn) => {
      btn.classList.toggle("active", !!(reactions[idx] && reactions[idx].has(btn.dataset.kind)));
    });
    updateBookmarkBtn();
    commentText.value = "";
    sheet.hidden = false;
    backdrop.hidden = false;
    document.querySelectorAll(".block.selected").forEach((b) => b.classList.remove("selected"));
    blockEl.classList.add("selected");
  }

  function renderSheetComments(idx) {
    sheetComments.innerHTML = (comments[idx] || [])
      .map((c) => `<div class="my-comment">💬 <span class="my-comment-text">${escapeHtml(c.text)}</span>
        <span class="my-comment-actions">
          <button class="c-edit" data-id="${c.id}" title="Bearbeiten">✏️</button>
          <button class="c-del" data-id="${c.id}" title="Löschen">🗑</button>
        </span></div>`)
      .join("");
  }

  function closeSheet() {
    sheet.hidden = true;
    backdrop.hidden = true;
    document.querySelectorAll(".block.selected").forEach((b) => b.classList.remove("selected"));
    currentIdx = null;
    resetEditing();
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  async function api(path, body) {
    const res = await fetch(`/api/r/${token}/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error("API-Fehler");
    return res.json();
  }

  reader.querySelectorAll(".block").forEach((blockEl) => {
    blockEl.addEventListener("click", (e) => {
      if (e.target.closest("a")) return; // let links work normally
      openSheet(parseInt(blockEl.dataset.idx, 10), blockEl);
    });
  });

  backdrop.addEventListener("click", closeSheet);
  document.getElementById("sheet-close").addEventListener("click", closeSheet);

  // ---------- Own comments: send, edit, delete ----------
  const sendBtn = document.getElementById("comment-send");
  let editingId = null;

  function resetEditing() {
    editingId = null;
    sendBtn.textContent = "Kommentar senden";
    commentText.value = "";
  }

  // Failed requests must NEVER fail silently (mobile dead zone!):
  // show an error toast, keep the sheet + typed text as-is.
  const FAIL_MSG = "Senden fehlgeschlagen — bitte nochmal versuchen 📶";

  sendBtn.addEventListener("click", async () => {
    const text = commentText.value.trim();
    if (!text || currentIdx === null || sendBtn.disabled) return;
    sendBtn.disabled = true; // double-tap protection: otherwise duplicate comments on slow connections
    try {
      if (editingId !== null) {
        await api(`comment/${editingId}/update`, { text });
        const c = (comments[currentIdx] || []).find((x) => x.id === editingId);
        if (c) c.text = text;
        resetEditing();
        closeSheet();
        toast("Kommentar aktualisiert ✏️");
      } else {
        const r = await api("comment", { chapter_id: chapterId, block_idx: currentIdx, text });
        (comments[currentIdx] = comments[currentIdx] || []).push({ id: r.id, text });
        renderMeta(currentIdx);
        closeSheet();
        toast("Danke für deinen Kommentar! 💙");
      }
    } catch (err) {
      toast(FAIL_MSG);
    } finally {
      sendBtn.disabled = false;
    }
  });

  sheetComments.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn || currentIdx === null) return;
    const id = parseInt(btn.dataset.id, 10);
    const list = comments[currentIdx] || [];
    const c = list.find((x) => x.id === id);
    if (!c) return;
    if (btn.classList.contains("c-edit")) {
      commentText.value = c.text;
      editingId = id;
      sendBtn.textContent = "Änderung speichern";
      commentText.focus();
    } else if (btn.classList.contains("c-del")) {
      if (!confirm("Diesen Kommentar wirklich löschen?")) return;
      try {
        await api(`comment/${id}/delete`, {});
      } catch (err) {
        toast(FAIL_MSG);
        return; // comment stays visible — don't discard locally what the server still has
      }
      comments[currentIdx] = list.filter((x) => x.id !== id);
      if (editingId === id) resetEditing();
      renderMeta(currentIdx);
      renderSheetComments(currentIdx);
      toast("Kommentar gelöscht");
    }
  });

  document.querySelectorAll(".reaction").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (currentIdx === null) return;
      const kind = btn.dataset.kind;
      let r;
      try {
        r = await api("reaction", { chapter_id: chapterId, block_idx: currentIdx, kind });
      } catch (err) {
        toast(FAIL_MSG);
        return; // leave button state unchanged — still reflects the server state
      }
      reactions[currentIdx] = reactions[currentIdx] || new Set();
      r.active ? reactions[currentIdx].add(kind) : reactions[currentIdx].delete(kind);
      btn.classList.toggle("active", r.active);
      renderMeta(currentIdx);
    });
  });

  // ---------- Questionnaire ----------
  const qform = document.getElementById("qform");
  if (qform) {
    qform.querySelectorAll(".scale").forEach((scale) => {
      scale.querySelectorAll(".scale-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          scale.querySelectorAll(".scale-btn").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
        });
      });
    });
    const qsubmit = qform.querySelector('button[type="submit"]');
    qform.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (qsubmit && qsubmit.disabled) return;
      const answers = {};
      qform.querySelectorAll(".scale").forEach((scale) => {
        const active = scale.querySelector(".scale-btn.active");
        if (active) answers[scale.dataset.qid] = active.dataset.val;
      });
      qform.querySelectorAll("textarea[data-qid]").forEach((ta) => {
        if (ta.value.trim()) answers[ta.dataset.qid] = ta.value.trim();
      });
      if (qsubmit) qsubmit.disabled = true;
      try {
        await api("answers", { chapter_id: chapterId, answers });
        toast("Feedback gespeichert — danke! 💙");
      } catch (err) {
        toast(FAIL_MSG); // input stays in the form
      } finally {
        if (qsubmit) qsubmit.disabled = false;
      }
    });
  }

  // ---------- Font size ----------
  const FONT_SIZES = ["0.95rem", "1.02rem", "1.1rem", "1.2rem", "1.32rem", "1.45rem"];
  function fontIdx() {
    const cur = localStorage.getItem("br:font") || "1.1rem";
    const i = FONT_SIZES.indexOf(cur);
    return i === -1 ? 2 : i;
  }
  function setFont(i) {
    const clamped = Math.max(0, Math.min(FONT_SIZES.length - 1, i));
    localStorage.setItem("br:font", FONT_SIZES[clamped]);
    document.documentElement.style.setProperty("--reader-font", FONT_SIZES[clamped]);
  }
  document.getElementById("font-minus").addEventListener("click", () => setFont(fontIdx() - 1));
  document.getElementById("font-plus").addEventListener("click", () => setFont(fontIdx() + 1));

  // ---------- Onboarding hint (only the first time) ----------
  // v2: bumped so readers who dismissed the pre-bookmark hint see the updated one once.
  const onboarding = document.getElementById("onboarding");
  if (onboarding && !localStorage.getItem("br:hint:v2")) {
    onboarding.hidden = false;
    document.getElementById("onboarding-ok").addEventListener("click", () => {
      localStorage.setItem("br:hint:v2", "1");
      onboarding.hidden = true;
    });
  }

  // ---------- Reading progress: bar, remember position, "continue reading" ----------
  const readbar = document.getElementById("readbar-fill");
  const blocks = [...reader.querySelectorAll(".block")];

  localStorage.setItem(ns + "last", JSON.stringify({ slug: reader.dataset.slug, title: reader.dataset.title }));

  function topBlockIdx() {
    const y = 70; // below the topnav
    for (const b of blocks) {
      if (b.getBoundingClientRect().bottom > y) return parseInt(b.dataset.idx, 10);
    }
    return blocks.length ? parseInt(blocks[blocks.length - 1].dataset.idx, 10) : 0;
  }

  // Server sync: persist progress (admin dashboard sees who's gotten how far)
  let maxIdx = 0, done = false, lastSentIdx = -1, doneSent = false, lastPingAt = 0;
  function sendProgress(useBeacon) {
    if (maxIdx <= lastSentIdx && (doneSent || !done)) return; // nothing new
    const payload = { chapter_id: chapterId, max_block_idx: maxIdx, done: done };
    lastSentIdx = maxIdx;
    doneSent = done;
    if (useBeacon && navigator.sendBeacon) {
      navigator.sendBeacon(`/api/r/${token}/progress`, new Blob([JSON.stringify(payload)], { type: "application/json" }));
    } else {
      api("progress", payload).catch(() => {});
    }
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") sendProgress(true); // tab switch/close: flush latest state
  });

  let saveTimer = null;
  window.addEventListener("scroll", () => {
    const max = document.documentElement.scrollHeight - window.innerHeight;
    if (readbar) readbar.style.width = (max > 0 ? (100 * window.scrollY) / max : 100) + "%";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      const idx = topBlockIdx();
      localStorage.setItem(ns + "pos:" + chapterId, String(idx));
      maxIdx = Math.max(maxIdx, idx);
      if (Date.now() - lastPingAt > 15000) { // server ping max. every 15s
        lastPingAt = Date.now();
        sendProgress(false);
      }
    }, 400);
  }, { passive: true });

  // ---------- Manual bookmark ("Hier weiterlesen") ----------
  function bookmarkBlock() {
    return bookmarkIdx === null ? null : reader.querySelector(`.block[data-idx="${bookmarkIdx}"]`);
  }
  function jumpToBookmark() {
    const target = bookmarkBlock();
    if (target) { target.scrollIntoView(); window.scrollBy(0, -70); }
  }
  function renderBookmark() {
    document.querySelectorAll(".block.bookmarked").forEach((b) => b.classList.remove("bookmarked"));
    const target = bookmarkBlock();
    if (target) target.classList.add("bookmarked");
    continueBtn.hidden = target === null; // only offer the jump when the mark is in this chapter
  }
  function updateBookmarkBtn() {
    const isCurrent = bookmarkIdx !== null && bookmarkIdx === currentIdx;
    bookmarkToggleBtn.textContent = isCurrent ? "🔖 Lesezeichen entfernen" : "🔖 Als Lesezeichen merken";
    bookmarkToggleBtn.classList.toggle("active", isCurrent);
  }
  continueBtn.addEventListener("click", jumpToBookmark);
  bookmarkToggleBtn.addEventListener("click", async () => {
    if (currentIdx === null) return;
    const wasCurrent = bookmarkIdx === currentIdx;
    try {
      if (wasCurrent) {
        await api("bookmark/clear", {});
        bookmarkIdx = null;
      } else {
        // One bookmark per book: setting it here clears any previous one server-side.
        await api("bookmark", { chapter_id: chapterId, block_idx: currentIdx });
        bookmarkIdx = currentIdx;
      }
    } catch (err) {
      toast(FAIL_MSG);
      return;
    }
    renderBookmark();
    closeSheet();
    toast(wasCurrent ? "Lesezeichen entfernt" : "Hier machst du weiter 🔖");
  });

  // Restore on load — a manual bookmark takes precedence over the auto scroll-restore
  // (the auto one "kam beim Hoch-/Runterscrollen nicht mit" — that was the reader complaint).
  renderBookmark();
  if (bookmarkIdx !== null) {
    // Arriving via the "Hier weiterlesen" card (landing / other chapter) → jump straight there.
    if (location.hash === "#weiterlesen") jumpToBookmark();
  } else {
    // No bookmark → old behavior: restore last scroll position as a fallback (local, else server).
    const saved = parseInt(localStorage.getItem(ns + "pos:" + chapterId) || reader.dataset.serverPos || "0", 10);
    if (saved > 1) {
      const target = reader.querySelector(`.block[data-idx="${saved}"]`);
      if (target) {
        target.scrollIntoView();
        window.scrollBy(0, -70);
        toast("Du warst hier stehengeblieben 📖");
      }
    }
  }

  // Mark chapter as read once the questionnaire becomes visible
  const questions = document.getElementById("questions");
  if (questions && "IntersectionObserver" in window) {
    new IntersectionObserver((entries, obs) => {
      if (entries.some((e) => e.isIntersecting)) {
        localStorage.setItem(ns + "done:" + chapterId, "1");
        done = true;
        sendProgress(false);
        obs.disconnect();
      }
    }, { threshold: 0.1 }).observe(questions);
  }
})();

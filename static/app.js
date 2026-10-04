"use strict";

const form = document.getElementById("cull-form");
const pathInput = document.getElementById("path");
const recursive = document.getElementById("recursive");
const runBtn = document.getElementById("run-btn");
const statusEl = document.getElementById("status");
const statsEl = document.getElementById("stats");
const sheetWrap = document.getElementById("sheet-wrap");
const sheetImg = document.getElementById("sheet");
const resultsEl = document.getElementById("results");
const gridEl = document.getElementById("grid");
const filtersEl = document.getElementById("filters");

let allPhotos = [];
let activeFilter = "all";

const VERDICT_LABEL = {
  "burst-winner": "KEEP",
  "keep": "KEEP",
  "soft": "SOFT",
  "alt": "ALT",
};

function setStatus(msg, isErr) {
  statusEl.textContent = msg;
  statusEl.className = "status" + (isErr ? " err" : "");
}

function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }

function renderStats(stats) {
  const items = [
    ["total", stats.total],
    ["bursts", stats.bursts],
    ["keepers", stats.keepers],
    ["soft dupes", stats.soft_dupes],
    ["exposure flagged", stats.exposure_flagged],
    ["unreadable", stats.unreadable],
  ];
  statsEl.innerHTML = items.map(([k, v]) =>
    `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("");
  show(statsEl);
}

function renderFilters() {
  const counts = { all: allPhotos.length };
  for (const p of allPhotos) {
    const key = p.verdict === "burst-winner" || p.verdict === "keep" ? "keep" : p.verdict;
    counts[key] = (counts[key] || 0) + 1;
  }
  const order = [["all", "All"], ["keep", "Keep"], ["soft", "Soft"], ["alt", "Alt"]];
  filtersEl.innerHTML = order.map(([key, label]) =>
    `<button data-f="${key}" class="${key === activeFilter ? "active" : ""}">${label} (${counts[key] || 0})</button>`
  ).join("");
  filtersEl.querySelectorAll("button").forEach((b) => {
    b.addEventListener("click", () => {
      activeFilter = b.dataset.f;
      renderFilters();
      renderGrid();
    });
  });
}

function verdictClass(v) {
  if (v === "burst-winner" || v === "keep") return "v-keep";
  if (v === "soft") return "v-soft";
  return "v-alt";
}

function renderGrid() {
  const filtered = allPhotos.filter((p) => {
    if (activeFilter === "all") return true;
    const key = p.verdict === "burst-winner" || p.verdict === "keep" ? "keep" : p.verdict;
    return key === activeFilter;
  });
  gridEl.innerHTML = filtered.map((p) => {
    const label = VERDICT_LABEL[p.verdict] || p.verdict;
    const metrics = `focus ${p.focus_ratio ?? "-"} · aniso ${p.aniso ?? "-"}`;
    return `
      <div class="card" data-name="${p.name}">
        ${p.thumb ? `<img src="${p.thumb}" loading="lazy" alt="">` : ""}
        <div class="meta">
          <div class="name">${p.name}</div>
          <div class="verdict ${verdictClass(p.verdict)}">${label}</div>
          <div class="metrics">${metrics}</div>
        </div>
      </div>`;
  }).join("");

  gridEl.querySelectorAll(".card").forEach((card) => {
    card.addEventListener("click", () => {
      const p = allPhotos.find((x) => x.name === card.dataset.name);
      if (p && p.thumb) openLightbox(p);
    });
  });
}

function openLightbox(p) {
  const lb = document.getElementById("lightbox");
  const img = lb.querySelector("img");
  const meta = lb.querySelector(".lb-meta");
  img.src = p.thumb;
  meta.textContent = `${p.name} — ${VERDICT_LABEL[p.verdict] || p.verdict} · focus ${p.focus_ratio} · aniso ${p.aniso}`;
  lb.classList.add("open");
}
document.getElementById("lightbox").addEventListener("click", (e) => {
  if (e.target.id === "lightbox") e.target.classList.remove("open");
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const path = pathInput.value.trim();
  if (!path) return;

  runBtn.disabled = true;
  setStatus("Scanning and culling…");
  hide(statsEl);
  hide(sheetWrap);
  hide(resultsEl);

  try {
    const res = await fetch("/api/cull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, recursive: recursive.checked }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");

    allPhotos = data.groups.flat();
    renderStats(data.stats);
    if (data.sheet_url) {
      sheetImg.src = data.sheet_url;
      show(sheetWrap);
    }
    renderFilters();
    renderGrid();
    show(resultsEl);
    setStatus(`Done — ${data.stats.total} photos, ${data.stats.keepers} keepers.`);
  } catch (err) {
    setStatus("Error: " + err.message, true);
  } finally {
    runBtn.disabled = false;
  }
});

/* Filters the generated reading list locally; no browser API calls are made. */
(function (root) {
  "use strict";
  function normalize(value) {
    return String(value || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase().trim();
  }
  function matches(paper, { query = "", researcher = "all", year = "all" } = {}) {
    const terms = normalize(query).split(/\s+/).filter(Boolean);
    const search = normalize(paper.search);
    const identifiers = String(paper.researchers || "").split(/\s+/);
    return terms.every(term => search.includes(term)) &&
      (researcher === "all" || identifiers.includes(researcher)) &&
      (year === "all" || String(paper.year) === year);
  }
  function setup(document) {
    const list = document.getElementById("theory-papers");
    const tools = document.getElementById("theory-tools");
    if (!list || !tools) return;
    const papers = Array.from(list.querySelectorAll(".theory-paper"));
    const search = document.getElementById("theory-search");
    const researcher = document.getElementById("theory-researcher");
    const year = document.getElementById("theory-year");
    const count = document.getElementById("theory-count");
    const empty = document.getElementById("theory-no-results");
    const reset = document.getElementById("theory-reset");
    if (!search || !researcher || !year || !count || !empty || !reset || !papers.length) return;
    function update() {
      let visible = 0;
      const filters = { query: search.value, researcher: researcher.value, year: year.value };
      for (const paper of papers) {
        const keep = matches(paper.dataset, filters);
        paper.hidden = !keep;
        if (keep) visible += 1;
      }
      count.textContent = `${visible} of ${papers.length} ${papers.length === 1 ? "paper" : "papers"}`;
      empty.hidden = visible !== 0;
    }
    search.addEventListener("input", update);
    researcher.addEventListener("change", update);
    year.addEventListener("change", update);
    reset.addEventListener("click", () => {
      search.value = ""; researcher.value = "all"; year.value = "all";
      update(); search.focus();
    });
    tools.hidden = false;
    update();
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { matches, normalize, setup };
  if (root.document) setup(root.document);
})(typeof globalThis !== "undefined" ? globalThis : this);

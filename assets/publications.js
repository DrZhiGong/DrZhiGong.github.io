(function () {
  "use strict";

  const ORCID = "0000-0002-1316-4675";
  const API_ROOT = "https://pub.orcid.org/v3.0";
  const MAX_RESPONSE_BYTES = 10 * 1024 * 1024;

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function textValue(value) {
    if (isObject(value)) value = value.value;
    return typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  }

  function normalizeDoi(value) {
    let doi = textValue(value);
    try { doi = decodeURIComponent(doi); } catch (_) { return ""; }
    doi = doi.trim().replace(/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)/i, "").trim().toLowerCase();
    return /^10\.\d{4,9}\/[^\s<>"']+$/.test(doi) ? doi : "";
  }

  function doiUrl(doi) {
    return "https://doi.org/" + encodeURI(doi).replace(/[?#]/g, char => encodeURIComponent(char));
  }

  function getDoi(record) {
    const ids = record["external-ids"] || {};
    if (!isObject(ids)) throw new Error("Invalid ORCID identifiers.");
    const entries = ids["external-id"] || [];
    if (!Array.isArray(entries)) throw new Error("Invalid ORCID identifier list.");
    for (const identifier of entries) {
      if (!isObject(identifier)) throw new Error("Invalid ORCID identifier.");
      if (identifier["external-id-type"] === "doi" && (identifier["external-id-relationship"] || "self") === "self") {
        const doi = normalizeDoi(identifier["external-id-value"]);
        if (doi) return doi;
      }
    }
    return "";
  }

  function safeUrl(value) {
    const source = textValue(value);
    if (/[\s<>"']/.test(source)) return "";
    try {
      const url = new URL(source);
      if (["https:", "http:"].includes(url.protocol) && url.hostname && !url.username && !url.password) return url.href;
    } catch (_) { /* A malformed URL is never used as a link. */ }
    return "";
  }

  function publicationUrl(work) {
    for (const candidate of [work.url, work.orcid_url]) {
      const url = safeUrl(candidate);
      if (!url) continue;
      const hostname = new URL(url).hostname.replace(/\.+$/, "");
      if (hostname !== "orcid.org" && !hostname.endsWith(".orcid.org")) return url;
    }
    return "";
  }

  function publicationDate(record) {
    const raw = record["publication-date"];
    if (raw == null) return { year: null, date: "" };
    if (!isObject(raw)) throw new Error("Invalid ORCID date.");
    const yearText = textValue(raw.year);
    const monthText = textValue(raw.month);
    const dayText = textValue(raw.day);
    if (!yearText) {
      if (monthText || dayText) throw new Error("ORCID date is missing a year.");
      return { year: null, date: "" };
    }
    if (!/^\d+$/.test(yearText) || (monthText && !/^\d+$/.test(monthText)) || (dayText && !/^\d+$/.test(dayText))) {
      throw new Error("Invalid ORCID date components.");
    }
    const year = Number(yearText), month = Number(monthText || 1), day = Number(dayText || 1);
    const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if (year < 1 || year > 9999 || month < 1 || month > 12 || day < 1 || day > days[month - 1] || (dayText && !monthText)) {
      throw new Error("Invalid ORCID publication date.");
    }
    let date = String(year).padStart(4, "0");
    if (monthText) date += "-" + String(month).padStart(2, "0");
    if (dayText) date += "-" + String(day).padStart(2, "0");
    return { year, date };
  }

  function putCode(record) {
    const code = String(record["put-code"] ?? "");
    if (!/^[1-9]\d*$/.test(code)) throw new Error("Invalid ORCID put-code.");
    return code;
  }

  function compareCodes(a, b) {
    return a.length - b.length || (a < b ? -1 : a > b ? 1 : 0);
  }

  function preferredSummaries(group) {
    if (!isObject(group) || !Array.isArray(group["work-summary"]) || !group["work-summary"].length) {
      throw new Error("Incomplete ORCID work group.");
    }
    const ranked = group["work-summary"].map(summary => {
      if (!isObject(summary)) throw new Error("Invalid ORCID summary.");
      const display = Number(summary["display-index"] || 0);
      return { summary, code: putCode(summary), length: publicationDate(summary).date.length, display: Number.isInteger(display) ? display : 0 };
    });
    ranked.sort((a, b) => b.display - a.display || b.length - a.length || compareCodes(a.code, b.code));
    return ranked.map(item => item.summary);
  }

  function normalizedWork(summary, detail, group, orcid = ORCID) {
    const code = putCode(summary);
    if (!isObject(detail) || putCode(detail) !== code || detail.path !== `/${orcid}/work/${code}` || detail.visibility !== "public") {
      throw new Error("Unexpected ORCID work detail.");
    }
    const record = { ...summary };
    for (const [key, value] of Object.entries(detail)) if (value !== null) record[key] = value;
    const title = isObject(record.title) ? textValue(record.title.title) : "";
    if (!title) throw new Error("An ORCID work has no title.");
    const doi = getDoi(record) || getDoi(group);
    const contributors = record.contributors || {};
    if (!isObject(contributors) || !Array.isArray(contributors.contributor || [])) throw new Error("Invalid ORCID contributors.");
    const authors = (contributors.contributor || []).map(contributor => {
      if (!isObject(contributor)) throw new Error("Invalid ORCID contributor.");
      return textValue(contributor["credit-name"]);
    }).filter(Boolean);
    const profile = `https://orcid.org/${orcid}`;
    return {
      id: doi ? `doi:${doi}` : `orcid:${code}`,
      title, authors, journal: textValue(record["journal-title"]), ...publicationDate(record),
      type: textValue(record.type), doi,
      url: doi ? doiUrl(doi) : safeUrl(record.url) || profile,
      put_code: code,
      put_codes: [...new Set(group["work-summary"].map(putCode))].sort(compareCodes),
      orcid_url: profile
    };
  }

  function deduplicate(works) {
    const result = [];
    for (const original of works) {
      const work = { ...original, put_codes: [...original.put_codes], authors: [...original.authors] };
      const matches = [];
      for (let index = 0; index < result.length; index++) {
        const previous = result[index];
        if ((work.doi && work.doi === previous.doi) || work.put_codes.some(code => previous.put_codes.includes(code))) matches.push(index);
      }
      if (!matches.length) { result.push(work); continue; }
      const merged = result[matches[0]];
      for (const other of [work, ...matches.slice(1).map(index => result[index])]) {
        for (const key of ["authors", "journal", "date", "year", "doi", "type"]) {
          if ((!merged[key] || (Array.isArray(merged[key]) && !merged[key].length)) && other[key]) merged[key] = other[key];
        }
        merged.put_codes = [...new Set([...merged.put_codes, ...other.put_codes])].sort(compareCodes);
      }
      if (merged.doi) { merged.id = `doi:${merged.doi}`; merged.url = doiUrl(merged.doi); }
      for (const index of matches.slice(1).reverse()) result.splice(index, 1);
    }
    return result.sort((a, b) => b.date.localeCompare(a.date) || a.title.toLowerCase().localeCompare(b.title.toLowerCase()) || a.id.localeCompare(b.id));
  }

  async function fetchJson(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeout ?? 12000);
    const fetcher = options.fetcher || globalThis.fetch;
    try {
      const response = await fetcher(url, { headers: { Accept: "application/json" }, credentials: "omit", mode: "cors", cache: "no-cache", signal: controller.signal });
      if (!response.ok) throw new Error(`ORCID HTTP ${response.status}.`);
      const contentType = (response.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
      if (!(contentType === "application/json" || /^application\/[a-z0-9.+-]+\+json$/.test(contentType))) throw new Error("ORCID returned non-JSON content.");
      if (Number(response.headers.get("content-length")) > MAX_RESPONSE_BYTES) throw new Error("ORCID response is too large.");
      const text = await response.text();
      if (text.length > MAX_RESPONSE_BYTES) throw new Error("ORCID response is too large.");
      const payload = JSON.parse(text);
      if (!isObject(payload) || Object.prototype.hasOwnProperty.call(payload, "error-code")) throw new Error("ORCID returned an API error.");
      return payload;
    } finally {
      clearTimeout(timer);
    }
  }

  async function collectWorks(fetcher = fetchJson, orcid = ORCID) {
    const payload = await fetcher(`${API_ROOT}/${orcid}/works`);
    if (!isObject(payload) || payload.path !== `/${orcid}/works` || !Array.isArray(payload.group) || payload.group.length > 10000) {
      throw new Error("Incomplete ORCID works response.");
    }
    // Validate every group before requesting details. Repeated put-codes share a request.
    const groups = payload.group.map(group => ({ group, ordered: preferredSummaries(group) }));
    const details = new Map();
    function getDetail(summary) {
      const code = putCode(summary);
      if (!details.has(code)) details.set(code, fetcher(`${API_ROOT}/${orcid}/work/${code}`));
      return details.get(code);
    }
    const works = new Array(groups.length);
    let next = 0;
    async function worker() {
      while (next < groups.length) {
        const index = next++;
        const { group, ordered } = groups[index];
        const work = normalizedWork(ordered[0], await getDetail(ordered[0]), group, orcid);
        if (!work.authors.length) {
          for (const alternate of ordered.slice(1)) {
            const candidate = normalizedWork(alternate, await getDetail(alternate), group, orcid);
            if (candidate.authors.length) { work.authors = candidate.authors; break; }
          }
        }
        works[index] = work;
      }
    }
    // Keep request concurrency modest; no part of a refresh is rendered early.
    await Promise.all(Array.from({ length: Math.min(3, groups.length) }, worker));
    return deduplicate(works);
  }

  function createPublication(work, doc) {
    function element(tag, className, text) {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    }
    const article = element("article", "publication");
    article.dataset.year = String(work.year || "Undated");
    article.dataset.search = [work.title, work.journal, ...work.authors, work.doi].join(" ").toLowerCase();
    article.append(element("div", "pub-year", String(work.year || "—")));
    const body = element("div");
    body.append(element("p", "eyebrow pub-journal", work.journal || work.type.replace(/-/g, " ") || "Research output"));
    const heading = element("h3");
    const url = publicationUrl(work);
    if (url) {
      const link = element("a", "", work.title + " ");
      link.href = url;
      const arrow = element("span", "", "↗");
      arrow.setAttribute("aria-hidden", "true");
      link.append(arrow);
      heading.append(link);
    } else heading.textContent = work.title;
    body.append(heading);
    if (work.authors.length) {
      const authors = element("p", "pub-authors");
      work.authors.forEach((name, index) => {
        if (index) authors.append(doc.createTextNode(", "));
        const normalized = name.toLowerCase().replace(/\./g, "").trim();
        authors.append(["zhi gong", "gong zhi", "z gong"].includes(normalized) ? element("strong", "", name) : doc.createTextNode(name));
      });
      body.append(authors);
    }
    body.append(element("p", "pub-doi", work.doi));
    article.append(body);
    return article;
  }

  function matchesFilter(searchText, publicationYear, query, selectedYear) {
    const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
    return (selectedYear === "all" || publicationYear === selectedYear) && terms.every(term => searchText.toLowerCase().includes(term));
  }

  function initialize(doc, fetcher = fetchJson) {
    const ids = ["publication-tools", "publication-search", "publication-year", "results-count", "no-results", "publication-list", "sync-status"];
    const nodes = ids.map(id => doc.getElementById(id));
    if (nodes.some(node => !node)) return null;
    const [tools, search, year, count, empty, list, status] = nodes;
    const button = doc.getElementById("refresh-publications");
    let articles = Array.from(list.querySelectorAll(".publication"));
    let lastStatus = status.textContent;
    let refreshing = false;

    function applyFilters() {
      let visible = 0;
      for (const article of articles) {
        article.hidden = !matchesFilter(article.dataset.search || "", article.dataset.year || "Undated", search.value, year.value || "all");
        if (!article.hidden) visible++;
      }
      count.textContent = `${visible} of ${articles.length} publication${articles.length === 1 ? "" : "s"}`;
      empty.hidden = visible !== 0;
    }

    function yearOptions(items, selected) {
      const values = [...new Set(items.map(article => article.dataset.year || "Undated"))];
      if (selected !== "all" && !values.includes(selected)) values.push(selected);
      values.sort((a, b) => a === "Undated" ? 1 : b === "Undated" ? -1 : Number(b) - Number(a));
      return ["all", ...values].map(value => {
        const option = doc.createElement("option");
        option.value = value;
        option.textContent = value === "all" ? "All years" : value;
        return option;
      });
    }

    async function refresh() {
      if (refreshing) return;
      refreshing = true;
      if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
      }
      status.textContent = "Updating publication record… " + lastStatus;
      try {
        const works = await collectWorks(fetcher);
        if (!works.length && articles.length) throw new Error("Empty response cannot replace existing publications.");
        const replacements = works.map(work => createPublication(work, doc));
        const selected = year.value || "all";
        const options = yearOptions(replacements, selected);
        // Prepare the complete list first so a failed request or normalization leaves the old list intact.
        list.replaceChildren(...replacements);
        articles = replacements;
        year.replaceChildren(...options);
        year.value = selected;
        applyFilters();
        lastStatus = "Updated " + new Date().toISOString().replace("T", " ").replace(/\.\d{3}Z$/, " UTC");
        status.textContent = lastStatus;
      } catch (_) {
        status.textContent = "Showing the latest available publication record. " + lastStatus;
      } finally {
        refreshing = false;
        if (button) {
          button.disabled = false;
          button.removeAttribute("aria-busy");
        }
      }
    }

    const selected = year.value || "all";
    year.replaceChildren(...yearOptions(articles, selected));
    year.value = selected;
    search.addEventListener("input", applyFilters);
    year.addEventListener("change", applyFilters);
    if (button) button.addEventListener("click", refresh);
    tools.hidden = false;
    applyFilters();
    const ready = refresh();
    return { refresh, applyFilters, ready };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { normalizeDoi, safeUrl, publicationDate, preferredSummaries, normalizedWork, deduplicate, fetchJson, collectWorks, matchesFilter, createPublication, initialize };
  }
  if (typeof document !== "undefined") initialize(document);
})();

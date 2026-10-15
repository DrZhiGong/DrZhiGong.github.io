"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const api = require("../assets/publications.js");
const ORCID = "0000-0002-1316-4675";

function summary(code, overrides = {}) {
  return { "put-code": code, "display-index": "0", title: { title: { value: "A paper" } }, "publication-date": { year: { value: "2026" } }, ...overrides };
}
function detail(code, overrides = {}) {
  return { ...summary(code), path: `/${ORCID}/work/${code}`, visibility: "public", contributors: { contributor: [{ "credit-name": { value: "Zhi Gong" } }] }, "journal-title": { value: "Journal" }, ...overrides };
}
function work(code, doi = "", extras = {}) {
  return { id: doi ? `doi:${doi}` : `orcid:${code}`, put_code: code, put_codes: [code], doi, title: "Paper", date: "2026", year: 2026, authors: [], journal: "", type: "journal-article", url: "", ...extras };
}

test("DOIs normalize while malformed identifiers and unsafe links are rejected", () => {
  assert.equal(api.normalizeDoi("https://doi.org/10.1234%2FABC"), "10.1234/abc");
  assert.equal(api.normalizeDoi("doi: 10.1234/ABC"), "10.1234/abc");
  assert.equal(api.normalizeDoi("10.1234/a<script>"), "");
  assert.equal(api.safeUrl("javascript:alert(1)"), "");
  assert.equal(api.safeUrl("https://user:pass@example.com/paper"), "");
  assert.equal(api.safeUrl("https://example.com/paper"), "https://example.com/paper");
});

test("date parsing supports partial dates and checks leap days", () => {
  assert.deepEqual(api.publicationDate(summary(1)), { year: 2026, date: "2026" });
  assert.deepEqual(api.publicationDate({ "publication-date": null }), { year: null, date: "" });
  assert.throws(() => api.publicationDate({ "publication-date": { year: { value: "2025" }, month: { value: "02" }, day: { value: "29" } } }));
  assert.equal(api.publicationDate({ "publication-date": { year: { value: "2024" }, month: { value: "02" }, day: { value: "29" } } }).date, "2024-02-29");
  assert.throws(() => api.publicationDate({ "publication-date": { year: { value: "2026" }, day: { value: "1" } } }));
});

test("preferred summaries prioritize display index then date precision", () => {
  const first = summary(1), preferred = summary(2, { "display-index": "1" });
  assert.equal(api.preferredSummaries({ "work-summary": [first, preferred] })[0]["put-code"], 2);
  assert.throws(() => api.preferredSummaries({ "work-summary": [] }));
});

test("normalization validates returned identity and retains metadata as literal text", () => {
  const source = summary(1), group = { "work-summary": [source] };
  const result = api.normalizedWork(source, detail(1, { title: { title: { value: "<script>alert(1)</script>" } } }), group);
  assert.equal(result.title, "<script>alert(1)</script>");
  assert.deepEqual(result.authors, ["Zhi Gong"]);
  assert.equal(result.journal, "Journal");
  assert.throws(() => api.normalizedWork(source, detail(2), group));
  assert.throws(() => api.normalizedWork(source, detail(1, { visibility: "limited" }), group));
});

test("deduplication merges transitive DOI and put-code matches without changing originals", () => {
  const inputs = [work("1", "10.1234/a"), work("2", "10.1234/b", { authors: ["Zhi Gong"] }), work("3", "10.1234/a", { put_codes: ["3", "2"] })];
  const outputs = api.deduplicate(inputs);
  assert.equal(outputs.length, 1);
  assert.deepEqual(outputs[0].put_codes, ["1", "2", "3"]);
  assert.deepEqual(outputs[0].authors, ["Zhi Gong"]);
  assert.deepEqual(inputs[0].put_codes, ["1"]);
});

test("collection reuses details, falls back for authors, and rejects partial refreshes", async () => {
  const first = summary(1, { "display-index": "1" }), alternate = summary(2);
  const calls = [];
  const fetcher = async url => {
    calls.push(url);
    if (url.endsWith("/works")) return { path: `/${ORCID}/works`, group: [{ "work-summary": [first, alternate] }, { "work-summary": [first] }] };
    return url.endsWith("/1") ? detail(1, { contributors: null }) : detail(2);
  };
  const works = await api.collectWorks(fetcher);
  assert.equal(works.length, 1);
  assert.deepEqual(works[0].authors, ["Zhi Gong"]);
  assert.equal(calls.filter(url => url.endsWith("/1")).length, 1);
  await assert.rejects(api.collectWorks(async url => url.endsWith("/works") ? { path: `/${ORCID}/works`, group: [{ "work-summary": [first] }] } : Promise.reject(new Error("offline"))));
  await assert.rejects(api.collectWorks(async () => ({ group: [] })));
});

test("fetch rejects HTML and API errors, sends no credentials, and aborts hung requests", async () => {
  const response = (type, body) => ({ ok: true, headers: new Headers({ "content-type": type }), text: async () => body });
  let options;
  assert.deepEqual(await api.fetchJson("https://example.com", { fetcher: async (_url, opts) => { options = opts; return response("application/json", "{}"); } }), {});
  assert.equal(options.credentials, "omit");
  assert.equal(options.headers.Accept, "application/json");
  await assert.rejects(api.fetchJson("https://example.com", { fetcher: async () => response("text/html", "<h1>Unavailable</h1>") }));
  await assert.rejects(api.fetchJson("https://example.com", { fetcher: async () => response("application/json", '{"error-code":500}') }));
  await assert.rejects(api.fetchJson("https://example.com", { timeout: 5, fetcher: (_url, opts) => new Promise((_resolve, reject) => opts.signal.addEventListener("abort", () => reject(new Error("aborted")))) }));
});

test("filters match title, author, journal, and DOI text together with the year", () => {
  const search = "Paper Zhi Gong Journal 10.1234/abc";
  assert.equal(api.matchesFilter(search, "2026", "GONG 10.1234", "2026"), true);
  assert.equal(api.matchesFilter(search, "2026", "Journal", "2025"), false);
  assert.equal(api.matchesFilter(search, "2026", "missing", "all"), false);
});

// A small DOM stand-in tests the actual refresh lifecycle without browser dependencies.
class Node {
  constructor(tag = "div") { this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.value = ""; this.hidden = false; this.textContent = ""; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll() { return this.children; }
}
function documentFixture() {
  const ids = ["publication-tools", "publication-search", "publication-year", "results-count", "no-results", "publication-list", "sync-status", "refresh-publications"];
  const nodes = Object.fromEntries(ids.map(id => [id, new Node()]));
  const article = new Node("article");
  article.dataset = { year: "2025", search: "saved zhi gong" };
  nodes["publication-list"].append(article);
  nodes["publication-year"].value = "2025";
  nodes["publication-search"].value = "Gong";
  nodes["sync-status"].textContent = "Updated 2026-10-15 00:00:00 UTC";
  const document = { getElementById: id => nodes[id], createElement: tag => new Node(tag), createTextNode: text => Object.assign(new Node("#text"), { textContent: text }) };
  return { document, nodes, article };
}

test("publication titles hide ORCID destinations while preserving paper links", () => {
  const { document } = documentFixture();
  const title = "Paper without a publisher link";
  for (const url of [
    `https://orcid.org/${ORCID}`,
    `https://www.orcid.org/${ORCID}`,
    `https://pub.orcid.org/v3.0/${ORCID}/work/1`,
    `http://ORCID.ORG./${ORCID}`
  ]) {
    for (const links of [{ url }, { url: "", orcid_url: url }]) {
      const article = api.createPublication(work("1", "", { title, ...links }), document);
      const heading = article.children[1].children[1];
      assert.equal(heading.textContent, title);
      assert.equal(heading.children.length, 0);
    }
  }
  for (const url of ["https://doi.org/10.1234/paper", "https://publisher.example/paper"]) {
    const article = api.createPublication(work("1", "", { url, orcid_url: `https://orcid.org/${ORCID}` }), document);
    const link = article.children[1].children[1].children[0];
    assert.equal(link.tagName, "a");
    assert.equal(link.href, url);
  }
});

test("failed or empty live refresh retains the saved list and honest snapshot timestamp", async () => {
  for (const fetcher of [async () => { throw new Error("offline"); }, async () => ({ path: `/${ORCID}/works`, group: [] })]) {
    const fixture = documentFixture();
    const ui = api.initialize(fixture.document, fetcher);
    await ui.ready;
    assert.equal(fixture.nodes["publication-list"].children[0], fixture.article);
    assert.match(fixture.nodes["sync-status"].textContent, /Updated 2026-10-15 00:00:00 UTC/);
    assert.match(fixture.nodes["sync-status"].textContent, /Showing the latest available publication record\./);
    assert.equal(fixture.nodes["refresh-publications"].disabled, false);
    assert.equal(fixture.nodes["publication-tools"].hidden, false);
  }
});

test("successful refresh preserves filters, replaces all works, and renders titles as text", async () => {
  const fixture = documentFixture();
  const fetcher = async url => url.endsWith("/works") ? { path: `/${ORCID}/works`, group: [{ "work-summary": [summary(1)] }] } : detail(1, { title: { title: { value: "<img src=x onerror=alert(1)>" } }, url: { value: "https://publisher.example/paper" } });
  const ui = api.initialize(fixture.document, fetcher);
  await ui.ready;
  const article = fixture.nodes["publication-list"].children[0];
  assert.notEqual(article, fixture.article);
  assert.equal(article.hidden, true); // The selected year remains 2025, even though the new paper is from 2026.
  assert.equal(fixture.nodes["publication-year"].value, "2025");
  assert.equal(fixture.nodes["publication-search"].value, "Gong");
  assert.match(fixture.nodes["sync-status"].textContent, /^Updated /);
  const link = article.children[1].children[1].children[0];
  assert.equal(link.tagName, "a");
  assert.equal(link.textContent, "<img src=x onerror=alert(1)> ");
  assert.equal(article.children[1].children[2].children[0].tagName, "strong");
});

test("automatic updates and search work without a visitor refresh button", async () => {
  const fixture = documentFixture();
  delete fixture.nodes["refresh-publications"];
  let offline = false;
  const fetcher = async url => {
    if (offline) throw new Error("offline");
    return url.endsWith("/works") ? { path: `/${ORCID}/works`, group: [{ "work-summary": [summary(1)] }] } : detail(1);
  };
  const ui = api.initialize(fixture.document, fetcher);
  assert.ok(ui);
  assert.match(fixture.nodes["sync-status"].textContent, /^Updating publication record…/);
  await ui.ready;
  const updatedStatus = fixture.nodes["sync-status"].textContent;
  assert.match(updatedStatus, /^Updated \d{4}-\d{2}-\d{2} /);
  assert.notEqual(fixture.nodes["publication-list"].children[0], fixture.article);
  fixture.nodes["publication-year"].value = "all";
  fixture.nodes["publication-year"].listeners.change();
  assert.equal(fixture.nodes["results-count"].textContent, "1 of 1 publication");
  offline = true;
  await ui.refresh();
  assert.equal(fixture.nodes["sync-status"].textContent, "Showing the latest available publication record. " + updatedStatus);
  assert.equal(fixture.nodes["publication-list"].children.length, 1);
});

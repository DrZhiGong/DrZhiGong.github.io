"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { matches, setup } = require("../assets/theory.js");

test("filters combine normalized search, exact researcher membership, and year", () => {
  const paper = { search: "René Smith: Quantum Monte Carlo 10.1234/QMC", researchers: "chan zhang", year: "2026" };
  assert.equal(matches(paper, { query: "RENE quantum", researcher: "chan", year: "2026" }), true);
  assert.equal(matches(paper, { query: "quantum missing" }), false);
  assert.equal(matches(paper, { researcher: "cha" }), false);
  assert.equal(matches(paper, { year: "2025" }), false);
  assert.equal(matches(paper, { query: "10.1234/qmc" }), true);
});

function node(overrides = {}) {
  return { value: "", hidden: false, listeners: {}, addEventListener(event, callback) { this.listeners[event] = callback; },
    focus() { this.focused = true; }, ...overrides };
}

test("DOM enhancement updates cards, result count, and the clear-filters control", () => {
  const papers = [node({ dataset: { search: "Quantum methods", researchers: "one", year: "2026" } }),
    node({ dataset: { search: "Density functional", researchers: "two", year: "2025" } })];
  const nodes = Object.fromEntries(["theory-tools", "theory-search", "theory-researcher", "theory-year", "theory-count", "theory-no-results", "theory-reset"].map(id => [id, node()]));
  nodes["theory-papers"] = node({ querySelectorAll: () => papers });
  nodes["theory-researcher"].value = "all"; nodes["theory-year"].value = "all";
  setup({ getElementById: id => nodes[id] });
  assert.equal(nodes["theory-count"].textContent, "2 of 2 papers");
  nodes["theory-researcher"].value = "two";
  nodes["theory-researcher"].listeners.change();
  assert.deepEqual(papers.map(paper => paper.hidden), [true, false]);
  nodes["theory-search"].value = "missing";
  nodes["theory-search"].listeners.input();
  assert.equal(nodes["theory-no-results"].hidden, false);
  assert.equal(nodes["theory-count"].textContent, "0 of 2 papers");
  nodes["theory-reset"].listeners.click();
  assert.deepEqual(papers.map(paper => paper.hidden), [false, false]);
  assert.equal(nodes["theory-no-results"].hidden, true);
  assert.equal(nodes["theory-search"].focused, true);
});

test("empty snapshots keep nonfunctional filters hidden", () => {
  const tools = node({ hidden: true });
  setup({ getElementById: id => id === "theory-tools" ? tools : id === "theory-papers" ? node({ querySelectorAll: () => [] }) : null });
  assert.equal(tools.hidden, true);
});

/* This interface only edits local Markdown and JSON through the local editor server. */
"use strict";

const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
const token = $('meta[name="editor-token"]').content;
const clone = value => JSON.parse(JSON.stringify(value));
const state = {
  tab: "notes", posts: [], post: null, baseline: "", data: null, revision: null,
  dirty: false, saving: false, loading: false, slugEdited: false, removed: [],
  previewVersion: 0, previewTimer: null, previewAbort: null, navigationVersion: 0,
};

function element(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(options)) {
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "value") node.value = value;
    else if (key === "checked") node.checked = value;
    else if (key === "readOnly") node.readOnly = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

async function api(path, options = {}) {
  const headers = { "X-Editor-Token": token, ...(options.body ? { "Content-Type": "application/json" } : {}) };
  const response = await fetch(`/api/${path}`, { ...options, headers, cache: "no-store" });
  let payload;
  try { payload = await response.json(); }
  catch (_) { throw new Error("The editor server returned an unreadable response. Check its terminal and try again."); }
  if (!response.ok) {
    const error = new Error(payload.error || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function status(message, tone = "", { reload = false, retry = false } = {}) {
  $("#status-text").textContent = message;
  $("#status-panel").dataset.tone = tone;
  $("#reload-button").hidden = !reload;
  $("#retry-button").hidden = !retry;
}

function currentSnapshot() {
  return JSON.stringify(state.tab === "notes" ? state.post : state.data);
}

function updateDirty() {
  state.dirty = currentSnapshot() !== state.baseline;
  $("#dirty-badge").hidden = !state.dirty;
  $("#save-button").disabled = state.saving || state.loading || !state.dirty || !(state.tab === "notes" ? state.post : state.data);
  document.title = `${state.dirty ? "• " : ""}Local editor · Zhi Gong`;
}

function markClean() {
  state.baseline = currentSnapshot();
  updateDirty();
}

function busy(value) {
  state.saving = value;
  $("#save-button").firstChild.textContent = value ? "Saving… " : "Save locally ";
  $("#note-form").inert = value || state.loading;
  $("#data-form").inert = value || state.loading;
  $("#new-note").disabled = value;
  $("#empty-new-note").disabled = value;
  $("#undo-removal").disabled = value || state.loading;
  updateDirty();
}

function loading(value) {
  state.loading = value;
  $("#note-form").inert = value || state.saving;
  $("#data-form").inert = value || state.saving;
  $("#undo-removal").disabled = value || state.saving;
  updateDirty();
}

async function mayLeave() {
  if (state.saving) return false;
  if (!state.dirty) return true;
  const dialog = $("#discard-dialog");
  if (dialog.open) return false;
  return new Promise(resolve => {
    dialog.returnValue = "cancel";
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "discard"), { once: true });
    dialog.showModal();
  });
}

function showError(error) {
  const conflict = error.status === 409;
  const message = error.status === 403
    ? "The editor session is no longer available. Your unsaved changes are still here. Copy them somewhere safe before reopening the editor."
    : error instanceof TypeError
      ? "Cannot reach the local editor. Your unsaved changes are still here. Check the editor terminal; if you restart it, copy your changes before reopening the page."
      : error.message;
  status(conflict ? `${message} Your changes are still here. Copy anything you want to keep before reloading.` : message, "error", { reload: conflict });
}

function postFields(post) {
  return {
    filename: post.filename || null,
    revision: post.revision || null,
    title: post.title || "",
    date: post.date || "",
    description: post.description || "",
    tags: Array.isArray(post.tags) ? post.tags : [],
    draft: Boolean(post.draft),
    slug: post.slug || "",
    body: post.body || "",
  };
}

function today() {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function slugify(text) {
  return text.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

function listNotes() {
  const query = $("#note-search").value.trim().toLowerCase();
  const notes = state.posts.filter(post => [post.title, post.description, post.slug, ...(post.tags || [])].join(" ").toLowerCase().includes(query));
  $("#note-count").textContent = `${notes.length} ${notes.length === 1 ? "note" : "notes"}${query ? " found" : " in this folder"}`;
  const list = $("#note-list");
  list.replaceChildren();
  if (state.post && !state.post.filename) {
    const item = noteItem({ title: state.post.title || "Untitled note", date: state.post.date, draft: state.post.draft }, true);
    item.addEventListener("click", () => $("#note-title").focus());
    list.append(item);
  }
  for (const post of notes) {
    const selected = state.post && state.post.filename === post.filename;
    const item = noteItem(post, selected);
    item.addEventListener("click", async () => {
      if (selected || !(await mayLeave())) return;
      await loadPost(post.filename);
    });
    list.append(item);
  }
  if (!notes.length && !(state.post && !state.post.filename)) list.append(element("p", { class: "field-help", text: query ? "No notes match. Try another word." : "Your first note can start here." }));
}

function noteItem(post, selected) {
  const node = element("button", { type: "button", class: "note-item" }, [
    element("span", { class: "note-item-title", text: post.title || "Untitled note" }),
    element("span", { class: "note-item-meta" }, [
      element("span", { text: post.date || "No date" }),
      element("span", { class: `note-state${post.draft ? " draft" : ""}`, text: post.draft ? "Draft" : "Ready" }),
    ]),
  ]);
  if (selected) node.setAttribute("aria-current", "true");
  return node;
}

async function refreshNotes() {
  const result = await api("posts");
  state.posts = result.posts;
  listNotes();
}

function displayPost() {
  const post = state.post;
  $("#note-editor").hidden = !post;
  $("#notes-empty").hidden = Boolean(post);
  if (!post) return;
  for (const name of ["title", "date", "slug", "description", "body"]) $(`#note-${name}`).value = post[name];
  $("#note-tags").value = post.tags.join(", ");
  $("#note-draft").checked = post.draft;
  $("#note-slug").readOnly = Boolean(post.filename);
  $("#slug-help").textContent = post.filename ? "This URL name stays fixed so existing links keep working." : "Lowercase English words, numbers, and hyphens. For a non-English title, enter a URL name here.";
  $("#note-mode").textContent = post.filename ? "EDIT NOTE" : "NEW NOTE";
  $("#post-preview").hidden = !post.filename || post.draft || post.date > new Date().toISOString().slice(0, 10);
  $("#post-preview").href = `/blog/${encodeURIComponent(post.slug)}/`;
  state.slugEdited = Boolean(post.filename);
  listNotes();
  schedulePreview(0);
}

async function loadPost(filename) {
  const version = ++state.navigationVersion;
  loading(true);
  status("Opening note…");
  try {
    const result = await api(`posts?filename=${encodeURIComponent(filename)}`);
    if (version !== state.navigationVersion) return;
    state.post = postFields(result.post);
    displayPost();
    markClean();
    status("Loaded from your local Markdown file. Save here, then use update-site.ps1 to publish.");
  } catch (error) { if (version === state.navigationVersion) showError(error); }
  finally { if (version === state.navigationVersion) loading(false); }
}

async function newNote() {
  if (!(await mayLeave())) return;
  ++state.navigationVersion;
  loading(false);
  state.post = postFields({ date: today(), draft: true });
  displayPost();
  markClean();
  status("New draft. Give it a title and save when you are ready.");
  $("#note-title").focus();
}

function syncNote(event) {
  if (!state.post) return;
  const input = event.target;
  const field = input.name;
  if (!field) return;
  if (field === "draft") state.post.draft = input.checked;
  else if (field === "tags") state.post.tags = input.value.split(",").map(tag => tag.trim()).filter(Boolean);
  else state.post[field] = input.value;
  if (field === "slug") state.slugEdited = true;
  if (field === "title" && !state.post.filename && !state.slugEdited) {
    state.post.slug = slugify(input.value);
    $("#note-slug").value = state.post.slug;
  }
  updateDirty();
  if (["title", "body", "date", "description"].includes(field)) schedulePreview();
  if (["title", "date", "draft"].includes(field)) listNotes();
}

function schedulePreview(delay = 350) {
  clearTimeout(state.previewTimer);
  state.previewVersion += 1;
  if (state.previewAbort) state.previewAbort.abort();
  const body = state.post ? state.post.body.trim() : "";
  const words = body ? body.split(/\s+/u).length : 0;
  $("#word-count").textContent = `${words} ${words === 1 ? "word" : "words"}`;
  $("#preview-status").textContent = "Updating…";
  const version = state.previewVersion;
  state.previewTimer = setTimeout(() => renderPreview(version), delay);
}

async function renderPreview(version) {
  if (!state.post || state.tab !== "notes" || version !== state.previewVersion) return;
  const controller = new AbortController();
  state.previewAbort = controller;
  try {
    const { title, date, description, body } = state.post;
    const result = await api("render", { method: "POST", body: JSON.stringify({ title, date, description, body }), signal: controller.signal });
    if (version !== state.previewVersion || state.tab !== "notes") return;
    // A fresh frame avoids a Chromium layout failure after a previously rendered
    // srcdoc frame has been hidden by a tab change. The empty sandbox is retained.
    const previous = $("#markdown-preview");
    const preview = previous.cloneNode(false);
    preview.srcdoc = result.html;
    previous.replaceWith(preview);
    $("#preview-status").textContent = "Up to date";
  } catch (error) {
    if (error.name === "AbortError" || version !== state.previewVersion) return;
    $("#preview-status").textContent = "Preview unavailable; your text is kept";
  }
}

function insertMarkdown(format) {
  const area = $("#note-body");
  const start = area.selectionStart;
  const end = area.selectionEnd;
  const selection = area.value.slice(start, end);
  let text, offset = 0, length = 0;
  if (format === "bold" || format === "italic") {
    const wrapper = format === "bold" ? "**" : "*";
    const content = selection || "text";
    text = `${wrapper}${content}${wrapper}`; offset = wrapper.length; length = content.length;
  } else if (format === "link" || format === "image") {
    const content = selection || (format === "image" ? "Image description" : "Link text");
    const prefix = format === "image" ? "!" : "";
    text = `${prefix}[${content}](https://example.com)`;
    offset = prefix.length + content.length + 3; length = "https://example.com".length;
  } else if (format === "heading") {
    const prefix = start > 0 && area.value[start - 1] !== "\n" ? "\n\n" : "";
    const content = selection || "Section heading";
    text = `${prefix}## ${content}\n`; offset = prefix.length + 3; length = content.length;
  } else if (format === "list") {
    const prefix = start > 0 && area.value[start - 1] !== "\n" ? "\n" : "";
    const content = selection || "List item";
    text = `${prefix}${content.split("\n").map(line => `- ${line}`).join("\n")}\n`;
    offset = prefix.length + 2; length = selection ? text.length - offset - 1 : content.length;
  } else {
    const prefix = start > 0 && area.value[start - 1] !== "\n" ? "\n\n" : "";
    const content = selection || "# Your code here";
    text = `${prefix}\`\`\`python\n${content}\n\`\`\`\n`; offset = prefix.length + 10; length = content.length;
  }
  area.focus();
  // insertText preserves the browser's undo stack; setRangeText is the fallback.
  let inserted = false;
  try { inserted = document.execCommand("insertText", false, text); } catch (_) { /* Browser fallback below. */ }
  if (!inserted) area.setRangeText(text, start, end, "end");
  area.setSelectionRange(start + offset, start + offset + length);
  area.dispatchEvent(new Event("input", { bubbles: true }));
}

const fieldSpec = (key, label, type = "text", help = "", extra = {}) => ({ key, label, type, help, ...extra });
const profileSections = [
  { key: null, title: "The essentials", description: "Your name, affiliation, contact details, and links.", fields: [
    fieldSpec("name", "Name"), fieldSpec("display_name", "Display name"), fieldSpec("role", "Role"), fieldSpec("institution", "Institution"),
    fieldSpec("location", "Location"), fieldSpec("email", "Email", "email"),
    fieldSpec("group_name", "Research group name"), fieldSpec("group_url", "Research group link", "url"),
    fieldSpec("scholar_url", "Google Scholar link", "url"), fieldSpec("orcid", "ORCID iD", "text", "Configured account.", { readonly: true }),
    fieldSpec("github", "GitHub username", "text", "Configured account.", { readonly: true }),
  ] },
  { key: null, title: "Introduction & biography", description: "Your home page introduction and About page text.", fields: [
    fieldSpec("intro", "Short introduction", "textarea"), fieldSpec("about", "About paragraphs", "lines", "One paragraph per line. Blank lines are ignored."),
    fieldSpec("source_note", "Source note", "textarea"),
  ] },
  { key: "research", title: "Research", singular: "research project", description: "Projects appear in this order on your website. Keep at least one project.", fields: [
    fieldSpec("title", "Project title", "text", "", { full: true }), fieldSpec("id", "Section link name", "text", "Lowercase words separated by hyphens; keep existing names to preserve links."),
    fieldSpec("kicker", "Research area"), fieldSpec("summary", "Summary", "textarea"),
    fieldSpec("details", "Detailed paragraphs", "lines", "One paragraph per line."), fieldSpec("methods", "Methods", "lines", "One method per line."),
    fieldSpec("institution", "Institution"), fieldSpec("period", "Period"), fieldSpec("advisor", "Advisor"), fieldSpec("status", "Status", "text", "For example: Current or Previous."),
    fieldSpec("related_dois", "Related publication DOIs", "lines", "One DOI per line, without https://doi.org/."),
  ] },
  { key: "education", title: "Education", singular: "education entry", description: "Degrees, institutions, and dates.", fields: [
    fieldSpec("institution", "Institution"), fieldSpec("degree", "Degree or program"), fieldSpec("period", "Period"),
  ] },
  { key: "honors", title: "Honors", singular: "honor", description: "Awards and recognition.", fields: [
    fieldSpec("title", "Award title", "text", "", { full: true }), fieldSpec("organization", "Organization"), fieldSpec("year", "Year"),
  ] },
  { key: "skills", title: "Methods & skills", singular: "skill category", description: "Tools, techniques, and languages grouped by category.", fields: [
    fieldSpec("category", "Category", "text", "", { full: true }), fieldSpec("items", "Items", "lines", "One skill or tool per line."),
  ] },
];
const outputSections = [
  { key: "manuscripts", title: "Manuscripts", singular: "manuscript", description: "Work in progress and manuscripts not yet listed in ORCID.", fields: [
    fieldSpec("title", "Title", "text", "", { full: true }), fieldSpec("authors", "Authors", "lines", "One author per line, in publication order."),
    fieldSpec("venue", "Journal or venue"), fieldSpec("status", "Status"), fieldSpec("note", "Additional note", "textarea"), fieldSpec("doi", "DOI", "text", "Leave blank if there is no DOI yet."),
  ] },
  { key: "patents", title: "Patents", singular: "patent", description: "Patent titles, identifiers, and current status.", fields: [
    fieldSpec("title", "Title", "text", "", { full: true }), fieldSpec("number", "Patent number"), fieldSpec("status", "Status"),
  ] },
];

function renderDataForm() {
  const form = $("#data-form");
  const openCards = new Set($$("details[open]", form).map(card => card.dataset.record));
  const firstRender = form.childElementCount === 0;
  form.replaceChildren();
  const sections = state.tab === "profile" ? profileSections : outputSections;
  for (const section of sections) {
    const wrapper = element("section", { class: "content-section" });
    const heading = element("div", { class: "section-title" }, [element("h2", { text: section.title })]);
    wrapper.append(heading, element("p", { class: "section-description", text: section.description }));
    if (!section.key) {
      wrapper.append(dataFields(state.data, section.fields, `base-${section.title}`));
    } else {
      const records = Array.isArray(state.data[section.key]) ? state.data[section.key] : [];
      records.forEach((record, index) => {
        const identity = `${section.key}-${index}`;
        const card = element("details", { class: "record-card", "data-record": identity });
        card.open = firstRender ? index === 0 : openCards.has(identity);
        const title = element("span", { class: "record-title", text: recordTitle(record, section, index) });
        card.append(element("summary", {}, [title, element("span", { class: "record-number", text: String(index + 1).padStart(2, "0") })]));
        const body = element("div", { class: "record-body" });
        const fields = dataFields(record, section.fields, identity);
        fields.addEventListener("input", () => { title.textContent = recordTitle(record, section, index); });
        body.append(fields);
        const actions = element("div", { class: "record-actions" });
        const remove = element("button", { type: "button", class: "small-button remove-button", text: `Remove ${section.singular}` });
        remove.disabled = section.key === "research" && records.length === 1;
        if (remove.disabled) remove.title = "Keep at least one research project.";
        remove.addEventListener("click", () => {
          state.removed.push({ key: section.key, index, record: clone(record) });
          state.data[section.key].splice(index, 1);
          renderDataForm(); updateDirty();
          status(`${section.singular[0].toUpperCase() + section.singular.slice(1)} removed from this form. Undo the removal below, or save to keep the change.`);
          $("#undo-removal").focus();
        });
        actions.append(remove); body.append(actions); card.append(body); wrapper.append(card);
      });
      if (!records.length) wrapper.append(element("p", { class: "section-description", text: `No ${section.title.toLowerCase()} yet.` }));
      const add = element("button", { type: "button", class: "small-button", text: `+ Add ${section.singular}` });
      add.addEventListener("click", () => {
        if (!Array.isArray(state.data[section.key])) state.data[section.key] = [];
        const entry = {};
        for (const field of section.fields) entry[field.key] = field.type === "lines" ? [] : "";
        const index = state.data[section.key].push(entry) - 1;
        renderDataForm(); updateDirty();
        const card = $(`[data-record="${section.key}-${index}"]`);
        card.open = true;
        $("input, textarea", card).focus();
      });
      wrapper.append(add);
    }
    form.append(wrapper);
  }
  $("#undo-removal").hidden = !state.removed.length;
}

function recordTitle(record, section, index) {
  return record.title || record.category || record.institution || `New ${section.singular} ${index + 1}`;
}

function dataFields(record, specs, prefix) {
  const group = element("div", { class: "data-fields" });
  for (const field of specs) {
    if (field.key === "group_name" && !(field.key in record)) continue;
    const id = `${prefix}-${field.key}`.replace(/[^a-zA-Z0-9_-]/g, "-");
    const multiline = ["textarea", "lines"].includes(field.type);
    const label = element("label", { class: `field${multiline || field.full ? " full-width" : ""}`, for: id }, [element("span", { text: field.label })]);
    const raw = record[field.key];
    const value = field.type === "lines" ? (Array.isArray(raw) ? raw.join("\n") : "") : (raw ?? "");
    const input = element(multiline ? "textarea" : "input", { id, value, readOnly: Boolean(field.readonly) });
    if (multiline) input.rows = field.key === "about" || field.key === "details" ? 5 : 3;
    else input.type = field.type;
    input.addEventListener("input", () => {
      record[field.key] = field.type === "lines" ? input.value.split(/\r?\n/).map(line => line.trim()).filter(Boolean) : input.value;
      updateDirty();
    });
    label.append(input);
    if (field.help) {
      const help = element("span", { class: "field-help", id: `${id}-help`, text: field.help });
      input.setAttribute("aria-describedby", `${id}-help`); label.append(help);
    }
    group.append(label);
  }
  return group;
}

async function loadData() {
  const version = ++state.navigationVersion;
  loading(true);
  status("Opening your website content…");
  try {
    const result = await api(`data?name=${encodeURIComponent(state.tab)}`);
    if (version !== state.navigationVersion) return;
    state.data = result.data;
    state.revision = result.revision;
    state.removed = [];
    $("#data-form").replaceChildren();
    renderDataForm(); markClean();
    status("Loaded from your local files. Save locally, then use update-site.ps1 to publish.");
  } catch (error) { if (version === state.navigationVersion) { showError(error); $("#retry-button").hidden = false; } }
  finally { if (version === state.navigationVersion) loading(false); }
}

async function switchTab(tab) {
  if (tab === state.tab || !(await mayLeave())) return;
  ++state.navigationVersion;
  clearTimeout(state.previewTimer);
  if (state.previewAbort) state.previewAbort.abort();
  state.tab = tab;
  state.post = null;
  state.data = null;
  state.removed = [];
  markClean();
  $$("[data-tab]").forEach(button => {
    if (button.dataset.tab === tab) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const notes = tab === "notes";
  $("#notes-section").hidden = !notes;
  $("#data-section").hidden = notes;
  if (!notes) $("#data-form").replaceChildren();
  const title = notes ? "Notes" : tab === "profile" ? "Profile & research" : "Other outputs";
  $("#page-title").textContent = title;
  $("#page-description").textContent = notes ? "Research notes, ideas, and things worth keeping." : tab === "profile" ? "The person and the work behind the website." : "Manuscripts, patents, and work in progress.";
  $("#data-intro").textContent = tab === "profile" ? "Edit your details below. Your Home, Research, and About pages update together when you save." : "Published papers continue to sync from ORCID. Use these forms for manuscripts and patents you maintain yourself.";
  if (notes) await initializeNotes(); else await loadData();
}

async function initializeNotes() {
  const version = ++state.navigationVersion;
  loading(true);
  $("#note-editor").hidden = true;
  $("#notes-empty").hidden = true;
  status("Opening your notebook…");
  try {
    const result = await api("posts");
    if (version !== state.navigationVersion) return;
    state.posts = result.posts;
    if (state.posts.length) await loadPost(state.posts[0].filename);
    else { state.post = null; displayPost(); listNotes(); markClean(); status("Your notebook is ready. Create your first note."); }
  } catch (error) {
    if (version === state.navigationVersion) { showError(error); $("#retry-button").hidden = false; }
  } finally {
    if (version === state.navigationVersion) loading(false);
  }
}

async function save() {
  if (state.saving || state.loading || !state.dirty) return;
  const form = state.tab === "notes" ? $("#note-form") : $("#data-form");
  if (!form.reportValidity()) return;
  if (state.tab === "notes" && !state.post.title.trim()) { $("#note-title").focus(); status("Please give your note a title.", "error"); return; }
  busy(true); status("Saving locally and rebuilding your preview…");
  let saved = false;
  try {
    let result;
    if (state.tab === "notes") {
      result = await api("posts", { method: "POST", body: JSON.stringify(state.post) });
      state.post = postFields(result.post);
      displayPost();
    } else {
      result = await api("data", { method: "POST", body: JSON.stringify({ name: state.tab, revision: state.revision, data: state.data }) });
      state.data = result.data; state.revision = result.revision; state.removed = [];
      renderDataForm();
    }
    saved = true;
    markClean();
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (result.build_ok === false) status(`Saved locally at ${time}, but the website preview could not rebuild. ${result.build_error || "Check the editor terminal."}`, "error");
    else status(`Saved locally at ${time}. Preview updated. Use update-site.ps1 to publish.`, "success");
    if (state.tab === "notes") {
      $("#post-preview").hidden = !result.preview_url;
      if (result.preview_url) $("#post-preview").href = result.preview_url;
      try { await refreshNotes(); }
      catch (_) { status(`Saved locally at ${time}. The note list could not refresh; your file was saved.`, "success"); }
    }
  } catch (error) {
    showError(error);
    if (saved) markClean();
  } finally { busy(false); }
}

$("#note-form").addEventListener("input", syncNote);
$("#note-form").addEventListener("submit", event => { event.preventDefault(); save(); });
$("#data-form").addEventListener("submit", event => { event.preventDefault(); save(); });
$("#save-button").addEventListener("click", save);
$("#new-note").addEventListener("click", newNote);
$("#empty-new-note").addEventListener("click", newNote);
$("#note-search").addEventListener("input", listNotes);
$$("[data-tab]").forEach(button => button.addEventListener("click", () => switchTab(button.dataset.tab)));
$$("[data-format]").forEach(button => {
  button.addEventListener("mousedown", event => event.preventDefault());
  button.addEventListener("click", () => insertMarkdown(button.dataset.format));
});
$("#reload-button").addEventListener("click", async () => {
  if (!(await mayLeave())) return;
  if (state.tab === "notes" && state.post?.filename) await loadPost(state.post.filename);
  else if (state.tab === "notes") await initializeNotes();
  else await loadData();
});
$("#retry-button").addEventListener("click", async () => {
  if (!(await mayLeave())) return;
  if (state.tab === "notes") await initializeNotes(); else await loadData();
});
$("#undo-removal").addEventListener("click", () => {
  if (state.saving || state.loading) return;
  const removal = state.removed.pop();
  if (!removal) return;
  state.data[removal.key].splice(removal.index, 0, removal.record);
  renderDataForm(); updateDirty();
  const card = $(`[data-record="${removal.key}-${removal.index}"]`);
  card.open = true; $("summary", card).focus();
  status("Removal undone. Save locally when your changes are ready.");
});
document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); save(); }
});
window.addEventListener("beforeunload", event => { if (state.dirty || state.saving) { event.preventDefault(); event.returnValue = ""; } });
$(".shortcut").textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘ S" : "Ctrl S";
markClean();
initializeNotes();

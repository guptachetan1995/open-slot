"use strict";

const LOOPBACK = ["localhost", "127.0.0.1", "[::1]"];
const KINDS = { music: "Music", podcast: "Podcast", comedy: "Comedy" };
const STATUS = {
  ready: "Ready",
  watch: "Watch",
  fading: "Fading",
  weak_link: "Too few links",
  outside_band: "Outside your room's range",
  dropped: "Dropped",
};
const NIGHT_STATE = { open: "Open", proposed: "Proposed", assigned: "Assigned" };
const HOW = { miss: "live", hit: "cache", fake: "synthetic" };

const RANK_RULE = "This app's ranking rule: ready before watch, then more connected acts, then Qloo's growth "
  + "figure, then closeness to your acts' median popularity, then Qloo's own order. No score is combined.";

let state = null;
let loaded = false;
let formOpen = false;
let busy = false;
let gates = 0;
let consoleDefault = "{}";
const inputs = {};
const opened = new Set();

const $ = (id) => document.getElementById(id);

function h(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(props || {})) {
    if (name === "class") node.className = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else if (value === true) node.setAttribute(name, "");
    else if (value !== false && value != null) node.setAttribute(name, value);
  }
  for (const child of children.flat()) {
    if (child !== false && child != null) node.append(child);
  }
  return node;
}

// A control inside its label. What a person has typed under a key survives the next render.
function labelled(tag, text, props, children, fallback) {
  const key = props["data-key"];
  const control = h(tag, { autocomplete: "off", ...props, oninput: () => { inputs[key] = control.value; } },
    children || []);
  if (key in inputs) control.value = inputs[key];
  else if (fallback !== undefined) control.value = fallback;
  return h("label", { class: "field" }, text, control);
}

async function request(path, options) {
  let reply;
  try {
    reply = await fetch(path, { cache: "no-store", ...options });
  } catch {
    return { status: 0, body: null };
  }
  let body = null;
  try {
    body = await reply.json();
  } catch {
    body = null;
  }
  return { status: reply.status, body };
}

async function invoke(tool, args, asAgent) {
  const headers = { "Content-Type": "application/json" };
  if (asAgent) headers["X-Open-Slot-As"] = "agent";
  const { status, body } = await request("/api/invoke", {
    method: "POST",
    headers,
    body: JSON.stringify({ tool, args }),
  });
  if (body && typeof body.ok === "boolean") return body;
  const message = status === 503 || status === 429
    ? "The server is busy; try again in a minute."
    : `The server did not answer as expected (HTTP ${status}).`;
  return { ok: false, outcome: "no_answer", error: { code: "no_answer", message } };
}

function say(text) {
  const box = $("status");
  if (box.textContent !== text) box.textContent = text;
}

function plainHttp() {
  return !window.isSecureContext && !LOOPBACK.includes(location.hostname);
}

function problem(envelope) {
  const box = $("problem");
  if (envelope === null) {
    box.hidden = true;
    return;
  }
  const forgotten = envelope.outcome === "no_workspace" && !plainHttp()
    ? " Reload the page to start a new workspace."
    : "";
  box.textContent = `${envelope.error.message}${forgotten}`;
  box.hidden = false;
}

function gateReason() {
  if (plainHttp()) return "Needs the HTTPS address.";
  if (!state || state.actor !== "person") return "Needs a browser that labels its requests.";
  return null;
}

// Every control that runs one of a person's verbs is made here, so one rule disables them all.
function personButton(text, key, label, onclick) {
  const blocked = gateReason();
  const button = h("button", {
    type: "button", "data-needs-person": true, "data-key": key, "aria-label": label, onclick,
  });
  button.append(text);
  button.disabled = busy || blocked !== null;
  const piece = document.createDocumentFragment();
  piece.append(button);
  if (blocked) {
    gates += 1;
    button.setAttribute("aria-describedby", `gate-${gates}`);
    piece.append(" ", h("span", { class: "reason", id: `gate-${gates}` }, blocked));
  }
  return piece;
}

function currentProgramme() {
  const all = state ? state.workspace.programmes : [];
  return all.length ? all[all.length - 1] : null;
}

function searchStep(programme, name) {
  return [...programme.trace].reverse().find(
    (step) => step.tool === "find_anchor" && step.request && step.request.params.query === name,
  );
}

function confirmedCount(programme) {
  return programme.anchors.filter((anchor) => anchor.confirmed !== null).length;
}

function waitingCount(programme) {
  return programme.anchors.filter((anchor) => anchor.candidates.length && anchor.confirmed === null).length;
}

function nameOf(programme, entityId) {
  const held = programme.candidates.find((candidate) => candidate.entityId === entityId);
  return held ? held.name : "an act that is no longer a candidate";
}

function nightLabels(programme, ids) {
  return ids.map((id) => (programme.nights.find((night) => night.id === id) || { label: id }).label).join(", ");
}

function typeName(urn) {
  return String(urn).replace("urn:entity:", "");
}

function figure(value, places) {
  return typeof value === "number" ? value.toFixed(places) : "not given";
}

function renderBanners() {
  const mode = state ? state.server.mode : null;
  const plain = plainHttp();
  $("banner-pending").hidden = loaded;
  $("banner-synthetic").hidden = mode !== "synthetic";
  $("banner-nokey").hidden = !state || state.server.key !== "missing";
  $("banner-live").hidden = mode !== "live";
  $("banner-spent").hidden = mode !== "spent";
  $("key-status").textContent = state ? state.server.key : "";
  $("banner-http").hidden = !plain;
  $("banner-nolabel").hidden = plain || !state || state.actorWhy !== "no_fetch_metadata";
}

function dataLine(programme) {
  if (programme.provider === "fake") {
    return "This programme runs on synthetic sample data: invented performers. Qloo is not called.";
  }
  if (state.server.mode === "spent") {
    return "This programme started on live Qloo data. Today's live calls are used up, so it can show what it "
      + "already fetched but cannot make a new Qloo call. A programme you start now runs on synthetic sample data.";
  }
  return "This programme runs on live Qloo data.";
}

function anchorItem(programme, anchor) {
  const item = h("li", { class: "anchor" }, h("h3", {}, anchor.name));
  if (!anchor.candidates.length) {
    const step = searchStep(programme, anchor.name);
    let note = "Not searched yet.";
    if (step && step.outcome === "qloo_no_results") {
      note = "Qloo found no match for this name. Check the spelling or type another public act.";
      if (programme.provider === "fake") note += " Synthetic mode only knows the sample programme's names.";
    } else if (step) {
      note = `The last search for this name did not finish (${step.outcome}).`;
    }
    item.append(
      h("p", { class: "note" }, note),
      h("button", {
        type: "button",
        class: "secondary",
        "data-key": `search:${anchor.name}`,
        "aria-label": `Search Qloo again for ${anchor.name}`,
        disabled: busy,
        onclick: () => search(programme, anchor),
      }, "Search Qloo again"),
    );
    return item;
  }
  const confirmed = anchor.candidates.find((match) => match.entityId === anchor.confirmed);
  let note = `Confirmed: ${confirmed ? confirmed.name : ""}.`;
  if (!confirmed) {
    note = anchor.candidates.length > 1
      ? `${anchor.candidates.length} matches. Pick the one you mean; nothing is chosen for you.`
      : "Not confirmed yet. Check that this is the one you mean.";
  }
  item.append(h("p", { class: "note" }, note));
  const matches = h("ul", { class: "matches" });
  for (const match of anchor.candidates) {
    const mine = match.entityId === anchor.confirmed;
    matches.append(h("li", { class: mine ? "match confirmed" : "match" },
      h("p", {},
        h("strong", {}, match.name),
        ` (${typeName(match.type)}, Qloo popularity ${figure(match.popularity, 2)}) `,
        mine ? h("span", { class: "badge ok" }, "Confirmed") : null),
      match.disambiguation ? h("p", { class: "note" }, match.disambiguation) : null,
      mine ? null : personButton("Yes, this is the act I mean", `confirm:${anchor.name}:${match.entityId}`,
        `Yes, this is the act I mean: ${match.name}, for ${anchor.name}`, () => confirmMatch(programme, anchor, match)),
    ));
  }
  item.append(matches);
  return item;
}

function fact(label, value, source) {
  return [
    h("dt", {}, label),
    h("dd", {}, value, source ? h("span", { class: "source" }, source) : null),
  ];
}

function dropBox(programme, evidence) {
  const key = `drop:${evidence.entity_id}`;
  return h("details", {
    "data-key": `${key}:box`,
    open: opened.has(`${key}:box`),
    ontoggle: (event) => (event.target.open ? opened.add(`${key}:box`) : opened.delete(`${key}:box`)),
  },
  h("summary", {}, "Drop this act"),
  labelled("input", "Why you are dropping it", { "data-key": `${key}:reason`, maxlength: "120" }),
  personButton("Drop from the board", key, `Drop ${evidence.name} from the board`,
    () => dropAct(programme, evidence)));
}

function card(programme, evidence) {
  const range = evidence.popularity.room_range;
  const links = evidence.links.length
    ? h("ul", { class: "plain" }, evidence.links.map((link) =>
      h("li", {}, `${link.anchor}: ${figure(link.score, 2)}`, h("span", { class: "source" }, ` (${link.source})`))))
    : "none";
  const trend = evidence.trend
    ? `${evidence.trend.state}. Newest weekly percentile ${figure(evidence.trend.newest_percentile, 1)}.`
    : "Not checked.";
  return h("li", { class: "card" },
    h("h3", {}, evidence.name),
    h("p", {}, h("span", { class: "badge" }, KINDS[evidence.lens] || evidence.lens),
      h("span", { class: evidence.status === "ready" ? "badge ok" : "badge" }, STATUS[evidence.status])),
    h("p", {}, evidence.status_text),
    h("dl", { class: "facts" },
      fact("Connects to", links, evidence.link_rule),
      fact("Popularity", figure(evidence.popularity.value, 2), evidence.popularity.source),
      range ? fact("Your room's range", `${figure(range.min, 2)} to ${figure(range.max, 2)}`, range.source) : null,
      fact("Trend", trend, evidence.trend ? `${evidence.trend.source}. Rule: ${evidence.trend.rule}` : ""),
      evidence.growth ? fact("Growth", figure(evidence.growth.value, 2), `${evidence.growth.source}'s own figure`) : null,
      fact("Tags", evidence.tags.length ? evidence.tags.join(", ") : "none", "Qloo's tags"),
      fact("Why it is here", evidence.ranked_by_text, RANK_RULE),
    ),
    h("div", { class: "actions" },
      h("button", {
        type: "button",
        class: "secondary",
        "data-key": `draft:${evidence.entity_id}`,
        "aria-label": `Draft inquiry for ${evidence.name}`,
        disabled: busy,
        onclick: () => draftFor(programme, evidence),
      }, "Draft inquiry")),
    dropBox(programme, evidence),
  );
}

function setAsideItem(programme, item) {
  const line = h("li", {}, h("strong", {}, `${item.name}: `), item.reason);
  if (item.status === "dropped") {
    line.append(" ", personButton("Bring back", `bring:${item.entity_id}`, `Bring ${item.name} back to the board`,
      () => bringBack(programme, item)));
  }
  return line;
}

function stepLine(step, cap) {
  const item = h("li", { class: `step ${step.kind}`, "data-step": step.step });
  if (step.kind === "stop") {
    item.append(step.because);
    return item;
  }
  if (step.kind !== "call") {
    item.append(h("strong", {}, `${step.kind}: `), step.because);
    return item;
  }
  const attempts = step.callsSpent > 1 ? `, ${step.callsSpent} attempts` : "";
  const outcome = step.outcome === "ok" ? "" : `, ${step.outcome}`;
  item.append(
    h("strong", {}, `${step.request.path}, ${HOW[step.cache] || step.cache}, ${step.ms} ms, `
      + `${Math.max(0, cap - step.spent)} of ${cap} calls left${attempts}${outcome}: `),
    step.because,
  );
  return item;
}

// Adds the steps the list does not show yet, so a screen reader hears each one once.
function renderTrace(programmeId, list, cap) {
  const box = $("trace");
  if (box.dataset.programme !== programmeId) {
    box.replaceChildren();
    box.dataset.programme = programmeId;
  }
  const shown = box.children.length;
  if (shown > list.length || (shown && box.lastElementChild.dataset.step !== String(list[shown - 1].step))) {
    box.replaceChildren();
  }
  for (const step of list.slice(box.children.length)) box.append(stepLine(step, cap));
  const calls = list.filter((step) => step.kind === "call").length;
  $("trace-summary").textContent = `What the agent did: ${calls} ${calls === 1 ? "call" : "calls"}, `
    + `${list.length} ${list.length === 1 ? "step" : "steps"}, each with its reason`;
}

function renderBudget(programme) {
  const budget = programme.budget;
  const synthetic = programme.provider === "fake";
  let text = `${budget.spent} of ${budget.cap} calls used`;
  if (budget.saved) text += `, ${budget.saved} not spent`;
  text += synthetic ? ". These were synthetic: Qloo was not called." : ".";
  if (!synthetic && state.allowances.agent) {
    const agent = state.allowances.agent;
    const daily = state.allowances.daily;
    text += ` The agent's allowance in the last 24 hours: ${agent.used} of ${agent.limit}. `
      + `The server's live calls today: ${daily.used} of ${daily.limit}.`;
  }
  $("budget-line").textContent = text;
  const last = [...programme.trace].reverse().find((step) => step.kind === "stop");
  $("stop-line").textContent = last ? `Last run: ${last.because}` : "";
  const full = budget.spent >= budget.cap;
  const most = state.limits.callCapMax;
  const box = $("cap-box");
  box.hidden = !full;
  if (!full) return;
  const newCap = Math.min(budget.cap + 8, most);
  box.replaceChildren(
    h("p", {}, `The programme's cap of ${budget.cap} calls is reached, so the agent stops there. `
      + (budget.cap < most ? `Only you can raise it, to at most ${most}.` : "That is the most this app allows.")),
    budget.cap < most ? h("div", { class: "actions" },
      labelled("input", "New cap", {
        "data-key": "cap:value", type: "number", min: String(budget.cap + 1), max: String(most), inputmode: "numeric",
      }, [], String(newCap)),
      personButton("Raise the call cap", "cap:raise", "Raise the call cap", () => raiseCap(programme))) : null,
  );
}

function renderBoard(programme, view) {
  const options = view.board.map((item) => ({ id: item.entity_id, name: item.name }));
  const taken = new Set(options.map((option) => option.id));
  for (const night of programme.nights) {
    const id = night.assigned || night.proposed;
    if (id && !taken.has(id)) {
      options.push({ id, name: nameOf(programme, id) });
      taken.add(id);
    }
  }
  $("nights-board").replaceChildren(...programme.nights.map((night) => {
    const id = night.assigned || night.proposed;
    const text = {
      open: " No act proposed or assigned.",
      proposed: ` ${nameOf(programme, id)}: proposed by the agent, not assigned.`,
      assigned: ` ${nameOf(programme, id)}: assigned by you. Nothing is booked.`,
    }[night.state];
    const item = h("li", { class: "night" },
      h("h3", {}, night.label),
      h("p", {}, h("span", { class: night.state === "assigned" ? "badge ok" : "badge" }, NIGHT_STATE[night.state]), text));
    if (!options.length) return item;
    item.append(
      labelled("select", `Act for ${night.label}`, { "data-key": `pick:${night.id}` },
        options.map((option) => h("option", { value: option.id }, option.name)), id || options[0].id),
      h("div", { class: "actions" },
        personButton("Assign", `assign:${night.id}`, `Assign the chosen act to ${night.label}`,
          () => assignNight(programme, night)),
        night.state === "assigned"
          ? personButton("Clear this night", `clear:${night.id}`, `Clear the assignment for ${night.label}`,
            () => clearNight(programme, night))
          : null),
    );
    return item;
  }));
  const used = new Set(programme.nights.flatMap((night) => [night.proposed, night.assigned]));
  const backups = programme.candidates.filter((c) => c.status === "ready" && !used.has(c.entityId));
  $("backups-line").textContent = backups.length
    ? `Backups, ready and not on a night: ${backups.map((c) => c.name).join(", ")}.`
    : "";
}

function inquiryItem(programme, draft) {
  const act = nameOf(programme, draft.entityId);
  const approved = draft.state === "approved";
  const item = h("li", { class: "inquiry" },
    h("h3", {}, `Inquiry to ${act}, about ${nightLabels(programme, draft.nightIds)}`),
    h("p", {}, h("span", { class: approved ? "badge ok" : "badge" }, approved ? "Approved" : "DRAFT, not approved")));
  if (!approved) {
    item.append(
      h("pre", { class: "text", role: "region", tabindex: "0", "aria-label": `Draft text for ${act}` }, draft.text),
      labelled("input", "Sender name, which ends the inquiry", { "data-key": `sender:${draft.draftId}`, maxlength: "60" }),
      h("p", { class: "note" }, "Approving freezes this text and takes the DRAFT line off. It still sends nothing."),
      h("div", { class: "actions" },
        personButton("Approve inquiry", `approve:${draft.draftId}`, `Approve the inquiry to ${act}`,
          () => approveDraft(programme, draft))),
    );
    return item;
  }
  const field = labelled("textarea", `Approved text for ${act}`, {
    class: "text", readonly: true, rows: "14", "data-key": `text:${draft.draftId}`,
  }, [], draft.text);
  const box = field.lastElementChild;
  const subject = `Availability question: ${act}`;
  item.append(
    h("p", { class: "note" }, `Approved by ${draft.approvedBy} on ${new Date(draft.approvedAt).toLocaleString()}. `
      + "Qloo holds no contact details: you find the address and send it yourself."),
    field,
    h("div", { class: "actions" },
      h("button", {
        type: "button", "data-key": `copy:${draft.draftId}`, "aria-label": `Copy the approved inquiry to ${act}`,
        onclick: () => copyText(draft.text, box),
      }, "Copy"),
      h("a", {
        class: "button secondary",
        href: `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(draft.text)}`,
      }, "Open in your mail app"),
      h("a", { class: "button secondary", href: `/api/inquiry/${programme.id}-${draft.draftId}.txt`, download: "" },
        "Download as text")),
  );
  return item;
}

function renderResults(programme) {
  const shown = programme.candidates.length > 0;
  $("results").hidden = !shown;
  if (!shown) return;
  const view = programme.view;
  $("range-line").textContent = view.range
    ? `Your room's range: Qloo popularity ${figure(view.range.min, 2)} to ${figure(view.range.max, 2)}. This app sets it from your confirmed acts.`
    : "No range: Qloo gave no popularity for your acts.";
  $("cards").replaceChildren(...view.board.map((evidence) => card(programme, evidence)));
  if (!view.board.length) {
    $("cards").replaceChildren(h("li", { class: "empty" },
      "No candidate is connected and in range. The set-aside list below says why each one was left out."));
  }
  $("also-details").hidden = view.also_connected.length === 0;
  $("also-summary").textContent = `Also connected and in range: ${view.also_connected.length}`;
  $("also").replaceChildren(...view.also_connected.map((item) => h("li", {}, `${item.name} (${KINDS[item.lens] || item.lens})`)));
  $("aside-summary").textContent = `Set aside, each with its reason: ${view.set_aside.length}`;
  $("aside").replaceChildren(...view.set_aside.map((item) => setAsideItem(programme, item)));
  $("propose-button").disabled = busy || !programme.candidates.some((c) => c.status === "ready");
  renderBoard(programme, view);
  $("inquiries").replaceChildren(...programme.inquiries.map((draft) => inquiryItem(programme, draft)));
}

function renderProgramme(programme) {
  $("programme-title").textContent = programme.label;
  $("nights-line").textContent = `Open nights: ${programme.nights.map((night) => night.label).join(", ")}.`;
  $("data-line").textContent = dataLine(programme);
  $("anchors").replaceChildren(...programme.anchors.map((anchor) => anchorItem(programme, anchor)));
  const confirmed = confirmedCount(programme);
  const waiting = waitingCount(programme);
  const ready = confirmed >= 2 && waiting === 0;
  $("run-button").disabled = busy || !ready;
  $("run-note").textContent = ready
    ? `${confirmed} confirmed acts.`
    : waiting
      ? "Confirm a match for each act that was found."
      : "Confirm at least two acts.";
  renderBudget(programme);
  renderTrace(programme.id, programme.trace, programme.budget.cap);
  renderResults(programme);
}

function focusTarget() {
  const active = document.activeElement;
  if (!active || active === document.body) return null;
  if (active.dataset.key) return { selector: `[data-key="${CSS.escape(active.dataset.key)}"]`, replaced: true };
  return active.id ? { selector: `#${CSS.escape(active.id)}`, replaced: false } : null;
}

function renderConsole(programme) {
  $("console-details").hidden = !state;
  if (!state) return;
  const fresh = programme ? JSON.stringify({ programme_id: programme.id }) : "{}";
  const box = $("console-args");
  if (box.value === consoleDefault) box.value = fresh;
  consoleDefault = fresh;
}

function render() {
  const keep = focusTarget();
  gates = 0;
  $("status").classList.toggle("working", busy);
  renderBanners();
  const programme = state ? currentProgramme() : null;
  const roomShown = Boolean(state) && (!programme || formOpen);
  $("room").hidden = !roomShown;
  $("programme").hidden = !programme || formOpen;
  $("open-button").disabled = busy;
  $("sample-button").hidden = !state || !state.sample;
  $("sample-button").disabled = busy;
  $("new-button").disabled = busy;
  $("propose-button").disabled = busy;
  $("console-run").disabled = busy;
  $("console-try").disabled = busy;
  if (programme && !formOpen) renderProgramme(programme);
  renderConsole(programme);
  const address = state && state.agentPath ? location.origin + state.agentPath : null;
  $("agent-details").hidden = !address;
  if (address) {
    $("agent-address").textContent = address;
    $("agent-command").textContent = `claude mcp add --transport http open-slot ${address}/mcp`;
  }
  const again = keep ? document.querySelector(keep.selector) : null;
  if (again) again.focus();
  else if (keep && keep.replaced) $("status").focus();
}

async function refresh() {
  const { status, body } = await request("/api/state");
  loaded = true;
  if (status === 200) {
    state = body;
  } else {
    state = null;
    problem({
      outcome: body && body.outcome,
      error: { message: body && body.error ? body.error.message : "The server did not answer." },
    });
  }
  render();
}

// Disabling a control while its action runs would drop the keyboard's place, so it is found again after.
async function act(work) {
  const place = focusTarget();
  busy = true;
  problem(null);
  render();
  try {
    const envelope = await work();
    if (envelope && !envelope.ok) {
      problem(envelope);
      say("");
    }
  } finally {
    busy = false;
    await refresh();
    const lost = !document.activeElement || document.activeElement === document.body;
    const again = place && lost ? document.querySelector(place.selector) : null;
    if (again) again.focus();
  }
}

async function search(programme, anchor) {
  await act(async () => {
    say(`Searching Qloo for ${anchor.name}.`);
    const found = await invoke("find_anchor", { programme_id: programme.id, name: anchor.name });
    say("");
    return found;
  });
}

// After a confirmation the keyboard moves on to the next act that still waits, or to the run button.
function focusNextConfirm() {
  const waiting = currentProgramme().anchors.find((anchor) => anchor.candidates.length && anchor.confirmed === null);
  const next = waiting && [...document.querySelectorAll("button[data-key^='confirm:']")]
    .find((button) => button.dataset.key.startsWith(`confirm:${waiting.name}:`));
  if (next) next.focus();
  else if (!$("run-button").disabled) $("run-button").focus();
}

async function confirmMatch(programme, anchor, match) {
  let confirmed = false;
  await act(async () => {
    const done = await invoke("confirm_anchor", {
      programme_id: programme.id,
      name: anchor.name,
      entity_id: match.entityId,
    });
    confirmed = done.ok;
    if (done.ok) say(`Confirmed ${match.name} as ${anchor.name}.`);
    return done;
  });
  if (confirmed) focusNextConfirm();
}

async function assignNight(programme, night) {
  const entity = document.querySelector(`[data-key="pick:${night.id}"]`).value;
  await act(async () => {
    const done = await invoke("assign_slot", {
      programme_id: programme.id, night_id: night.id, entity_id: entity, revision: programme.revision,
    });
    if (done.ok) say(`Assigned ${nameOf(programme, entity)} to ${night.label}. Nothing is booked.`);
    return done;
  });
}

async function clearNight(programme, night) {
  await act(async () => {
    const done = await invoke("assign_slot", {
      programme_id: programme.id, night_id: night.id, entity_id: null, revision: programme.revision,
    });
    if (done.ok) say(`${night.label} is open again.`);
    return done;
  });
}

async function approveDraft(programme, draft) {
  let approved = false;
  await act(async () => {
    const done = await invoke("approve_inquiry", {
      programme_id: programme.id,
      draft_id: draft.draftId,
      sender_name: inputs[`sender:${draft.draftId}`] || null,
      revision: programme.revision,
    });
    approved = done.ok;
    if (done.ok) say(`Approved the inquiry to ${nameOf(programme, draft.entityId)}. It is no longer a draft. `
      + "Copy it, or open it in your mail app.");
    return done;
  });
  if (approved) document.querySelector(`[data-key="copy:${draft.draftId}"]`).focus();
}

async function dropAct(programme, evidence) {
  await act(async () => {
    const done = await invoke("drop_act", {
      programme_id: programme.id, entity_id: evidence.entity_id, reason: inputs[`drop:${evidence.entity_id}:reason`] || "",
    });
    if (done.ok) say(`Dropped ${evidence.name}. It is in the set-aside list, and you can bring it back.`);
    return done;
  });
}

async function bringBack(programme, item) {
  await act(async () => {
    const done = await invoke("drop_act", { programme_id: programme.id, entity_id: item.entity_id, undo: true });
    if (done.ok) say(`${item.name} is back on the board.`);
    return done;
  });
}

async function raiseCap(programme) {
  const cap = Number(document.querySelector('[data-key="cap:value"]').value);
  await act(async () => {
    const done = await invoke("raise_call_cap", { programme_id: programme.id, cap });
    if (done.ok) say(`The call cap is now ${done.result.cap}.`);
    return done;
  });
}

async function proposeNights(programme) {
  await act(async () => {
    const done = await invoke("suggest_slots", { programme_id: programme.id });
    if (done.ok) say("The agent proposed acts for the open nights. Only you assign them.");
    return done;
  });
}

async function draftFor(programme, evidence) {
  const mine = programme.nights.filter((night) => night.proposed === evidence.entity_id
    || night.assigned === evidence.entity_id).map((night) => night.id);
  const args = { programme_id: programme.id, entity_id: evidence.entity_id };
  if (mine.length) args.night_ids = mine;
  let drafted = null;
  await act(async () => {
    const done = await invoke("draft_inquiry", args);
    if (done.ok) {
      drafted = done.result.draft_id;
      say(`Drafted an inquiry to ${evidence.name}. It reads DRAFT until you name the sender and approve it.`);
    }
    return done;
  });
  if (drafted) document.querySelector(`[data-key="sender:${drafted}"]`).focus();
}

async function copyText(text, box) {
  try {
    await navigator.clipboard.writeText(text);
    say("Copied the approved inquiry.");
  } catch {
    box.focus();
    box.select();
    say("This browser would not copy for you. The text is selected: press Ctrl+C or Cmd+C.");
  }
}

async function startProgramme(args) {
  await act(async () => {
    const started = await invoke("open_programme", args);
    if (!started.ok) return started;
    formOpen = false;
    const names = started.result.anchors;
    for (const [index, name] of names.entries()) {
      say(`Searching for ${name} (${index + 1} of ${names.length}).`);
      const found = await invoke("find_anchor", { programme_id: started.result.programme_id, name });
      if (!found.ok) return found;
      await refresh();
    }
    say(`Searched for ${names.length} acts. Confirm the ones you mean.`);
    return started;
  });
  if (currentProgramme() && !formOpen) $("programme-title").focus();
}

function showProgress(progress) {
  renderTrace(progress.id, progress.steps, progress.budget.cap);
  say(`The agent is running. ${progress.budget.spent} of ${progress.budget.cap} calls used so far.`);
}

function watchCalls() {
  let watching = true;
  const timer = setInterval(async () => {
    const { status, body } = await request("/api/calls");
    if (watching && status === 200 && body.programme) showProgress(body.programme);
  }, 600);
  return () => {
    watching = false;
    clearInterval(timer);
  };
}

async function run(programme) {
  $("trace-details").open = true;
  await act(async () => {
    say("The agent is running.");
    const stop = watchCalls();
    let planned;
    try {
      planned = await invoke("plan_acts", { programme_id: programme.id });
    } finally {
      stop();
    }
    if (planned.ok) {
      const calls = planned.result.calls;
      say(`The agent ${planned.result.stopped_text}. ${calls.spent_in_total} of ${calls.cap} calls used.`);
    } else {
      say("");
    }
    return planned;
  });
}

function readForm() {
  const lines = (text) => text.split("\n").map((line) => line.trim()).filter(Boolean);
  const args = {
    label: $("label").value.trim(),
    nights: lines($("nights").value),
    anchors: lines($("acts").value),
  };
  const note = $("capacity").value.trim();
  if (note) args.capacity_note = note;
  return args;
}

function showConsole(tool, envelope) {
  const line = envelope.ok
    ? `${tool}: ok (${envelope.outcome}).`
    : `${tool}: refused, ${envelope.error.code}. ${envelope.error.message}`;
  $("console-line").textContent = line;
  const shown = JSON.stringify(envelope.ok ? envelope.result : envelope.error, null, 2);
  $("console-out").textContent = shown.length > 6000
    ? `${shown.slice(0, 6000)}\n... ${shown.length - 6000} more characters`
    : shown;
}

async function runConsole(tool, args) {
  $("console-tool").value = tool;
  $("console-args").value = JSON.stringify(args);
  await act(async () => {
    showConsole(tool, await invoke(tool, args, true));
    return null;
  });
}

function tryApprove() {
  const programme = currentProgramme();
  const draft = programme && programme.inquiries.length ? programme.inquiries[programme.inquiries.length - 1] : null;
  const args = {
    programme_id: programme ? programme.id : "prog_001",
    draft_id: draft ? draft.draftId : "d1",
    sender_name: "Maya",
    revision: programme ? programme.revision : 0,
  };
  runConsole("approve_inquiry", args);
}

$("room-form").addEventListener("submit", (event) => {
  event.preventDefault();
  startProgramme(readForm());
});
$("sample-button").addEventListener("click", () => startProgramme(state.sample));
$("new-button").addEventListener("click", () => {
  formOpen = true;
  say("");
  problem(null);
  render();
  $("label").focus();
});
$("run-button").addEventListener("click", () => run(currentProgramme()));
$("propose-button").addEventListener("click", () => proposeNights(currentProgramme()));
$("console-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const tool = $("console-tool").value.trim();
  let args;
  try {
    args = JSON.parse($("console-args").value || "{}");
  } catch {
    $("console-line").textContent = "The arguments are not valid JSON.";
    return;
  }
  runConsole(tool, args);
});
$("console-try").addEventListener("click", tryApprove);

async function loadTools() {
  const { status, body } = await request("/api/tools");
  if (status !== 200) return;
  $("console-tools").replaceChildren(...body.tools.map((tool) => h("option", { value: tool.name })));
}

refresh();
loadTools();

"use strict";

// Improvement ideas: what the regular review of declined answers found, and the owner's decision on each.
window.AdminImprovements = {
  kinds: {
    content_gap: "Missing approved content",
    content_revision: "Approved content to revise",
    strategy_gap: "Missing explanation style",
    false_negative_review: "Answers refused that may have been fine",
  },
  tones: { open: "warn", deferred: "", uncertain: "bad", rejected: "", acknowledged: "good", superseded: "",
    queued: "warn", queued_publication: "warn", published: "good" },
  words(value) { return String(value).replaceAll("_", " "); },
  label(row) {
    const place = row.situation;
    const topic = this.words(place.topic);
    return `${topic[0].toUpperCase()}${topic.slice(1)}: ${this.words(place.journey_state)}, ${this.words(place.channel)}`;
  },
  // One plain sentence that says what happened, instead of the technical rationale.
  headline(row) {
    const place = row.situation, topic = this.words(place.topic), stage = this.words(place.journey_state);
    return {
      content_gap: `Members asked about ${topic}, and no approved passage could answer them.`,
      content_revision: `The approved passage ${row.draft.base_id} did not fully answer questions about ${topic}.`,
      strategy_gap: `No explanation style was left to answer ${topic} questions at ${stage} over ${this.words(place.channel)}.`,
      false_negative_review: `Answers about ${topic} were refused, but they may have been fine.`,
    }[row.kind];
  },
  next(row) {
    return {
      content_gap: "Write the missing passage, then approve it. It goes to the content publisher.",
      content_revision: "Edit the passage, then approve it. The new version replaces the old one when published.",
      strategy_gap: "Check the proposed style, then approve it. It joins the next signed release.",
      false_negative_review: "Open an example journey in Conversations to judge the answers. Then acknowledge, defer or reject.",
    }[row.kind];
  },

  mount(target, options) {
    const { row, extra, data, api, saved } = options;
    const make = (tag, text, className) => {
      const element = document.createElement(tag);
      if (text !== undefined && text !== null) element.textContent = text;
      if (className) element.className = className;
      return element;
    };
    const words = this.words;
    const proposal = row, evidence = row.evidence, draft = row.draft, idea = draft.suggested;
    const owner = Boolean(data.access.can_own);
    const decidable = ["open", "deferred", "uncertain"].includes(proposal.status.code);
    const kind = proposal.kind;
    const root = make("div", undefined, "idea");

    // Header: what happened, in one sentence, with the numbers that matter.
    const head = make("div", undefined, "idea-head");
    head.append(make("p", this.headline(proposal), "idea-headline"));
    const tiles = make("div", undefined, "stats");
    const tile = (value, label) => { const item = make("div", undefined, "stat"); item.append(make("strong", value), make("span", label)); tiles.append(item); };
    tile(String(evidence.declined), "declined answers");
    tile(String(evidence.journeys), "journeys");
    if (kind !== "strategy_gap") tile(`${Math.round(evidence.content_retrieved_share * 100)}%`, "had an approved passage");
    else tile(String(evidence.eligible_styles.length), "styles could explain");
    head.append(tiles, make("span", proposal.status.label, "badge " + (this.tones[proposal.status.code] || "")));
    root.append(head);

    const grid = make("div", undefined, "idea-grid");
    const context = make("div", undefined, "idea-context");
    const work = make("div", undefined, "idea-work");
    grid.append(context, work);
    root.append(grid);

    // Left: what members said and what the model noticed.
    const asked = make("section"); asked.append(make("h3", "What members asked"));
    const quotes = evidence.examples || [];
    if (quotes.length) {
      const list = make("ul", undefined, "quotes");
      quotes.forEach((text) => list.append(make("li", text)));
      asked.append(list);
    } else {
      asked.append(make("p", owner ? "No questions were kept for this idea." : "Member questions are shown to owners only.", "cell-note"));
    }
    context.append(asked);
    if (idea && (idea.themes.length || idea.coverage.length)) {
      const found = make("section"); found.append(make("h3", "What the AI noticed"));
      const list = make("ul", undefined, "plain");
      idea.themes.forEach((item) => list.append(make("li", `Members ask: ${item}`)));
      idea.coverage.forEach((item) => list.append(make("li", `Not yet covered: ${item}`)));
      found.append(list); context.append(found);
    }
    const more = make("details", undefined, "technical");
    more.append(make("summary", "Technical details"));
    const facts = make("dl", undefined, "facts");
    const add = (key, value) => { if (value) facts.append(make("dt", key), make("dd", value)); };
    add("Unanswered / flagged / no style", `${evidence.unanswered} / ${evidence.review_flagged} / ${evidence.no_style || 0}`);
    add("Why they were declined", Object.entries(evidence.decline_reasons || {}).map(([key, count]) => `${words(key)}: ${count}`).join("\n"));
    add("What the answer check said", Object.entries(evidence.review_issues).map(([key, count]) => `${words(key)}: ${count}`).join("\n"));
    add("Passages used", Object.entries(evidence.content_versions).map(([key, count]) => `${key}: ${count}`).join("\n"));
    add("Styles that can explain", evidence.eligible_styles.join(", "));
    add("Example journeys", evidence.sample_journey_ids.join(", "));
    add("Period", `${evidence.window_start} to ${evidence.window_end}`);
    add("Reference", `${proposal.proposal_id} (review ${proposal.batch_id})`);
    more.append(facts);
    context.append(more);

    if (!decidable || !owner) {
      work.append(make("p", decidable ? "Only an owner can decide on this idea." : proposal.status.label, "notice"));
      work.append(make("p", this.next(proposal), "cell-note"));
      target.replaceChildren(root);
      return;
    }

    // Right: the thing the owner edits.
    const form = make("form", undefined, "idea-form");
    work.append(form);
    const field = (title, input, hint) => {
      const label = make("label"); label.append(make("span", title, "field-title"));
      if (hint) label.append(make("span", hint, "cell-note"));
      label.append(input); form.append(label); return input;
    };
    const chips = (title, values, chosen, parent) => {
      const group = make("fieldset", undefined, "chips"); group.append(make("legend", title));
      const inputs = values.map((value) => {
        const chip = make("label", undefined, "chip");
        const input = make("input"); input.type = "checkbox"; input.value = value; input.checked = chosen.includes(value);
        chip.append(input, make("span", value === "*" ? "All stages" : words(value)));
        group.append(chip); return input;
      });
      parent.append(group); return () => inputs.filter((input) => input.checked).map((input) => input.value);
    };
    let collect = null;
    if (kind === "strategy_gap") {
      const name = field("Style name", make("input")); name.maxLength = 120; name.value = draft.name;
      const pattern = field("How it explains", make("select"));
      for (const item of data.style_patterns) { const option = make("option", item.name); option.value = item.strategy_id; pattern.append(option); }
      pattern.value = draft.pattern_id;
      const where = make("details", undefined, "technical"); where.append(make("summary", "Where it can be used"));
      form.append(where);
      const stages = chips("Journey stages", data.style_stages, draft.eligible_journey_states, where);
      const channels = chips("Channels", data.style_channels, draft.channels, where);
      const modes = chips("Accessibility", data.style_accessibility_modes, draft.accessibility_modes, where);
      collect = () => {
        if (!name.value.trim()) throw new Error("Give the style a name.");
        return { expected_revision: data.revision, style: { name: name.value.trim(), pattern_id: pattern.value,
          eligible_journey_states: stages(), channels: channels(), accessibility_modes: modes() } };
      };
    } else if (kind === "content_gap" || kind === "content_revision") {
      if (idea) {
        const note = make("div", undefined, "notice soft");
        note.append(make("strong", "Drafted by AI. Nobody has checked it yet."),
          make("span", ` It is written from the model's own knowledge${idea.sources.length ? ` and consistent with ${idea.sources.join(", ")}` : ""}. Facts and figures can be wrong or out of date.`));
        form.append(note);
      }
      const title = field("Title", make("input")); title.maxLength = 200; title.value = idea?.title || extra.base?.title || draft.title;
      const text = field("Passage", make("textarea")); text.rows = 9; text.maxLength = 8000;
      text.value = idea?.body || extra.base?.body || "";
      if (idea?.gaps.length) form.append(make("p", `The AI left these out as uncertain: ${idea.gaps.join(" ")}`, "cell-note"));
      if (idea?.claims.length) {
        const claims = make("section", undefined, "claims");
        claims.append(make("h3", "Check these against a source"));
        const list = make("ol"); idea.claims.forEach((claim) => list.append(make("li", claim))); claims.append(list);
        form.append(claims);
      }
      const source = field("Source you checked against", make("input"), "Required to approve: the document or authority, for example the fund regulations and date.");
      source.maxLength = 300;
      const settings = make("details", undefined, "technical"); settings.append(make("summary", "Where it applies and the questions it answers"));
      form.append(settings);
      const stages = chips("Journey stages", extra.stages, extra.base?.journey_states ?? draft.journey_states, settings);
      const answersLabel = make("label"); answersLabel.append(make("span", "Questions this passage answers", "field-title"));
      const answers = make("textarea"); answers.rows = 2; answers.maxLength = 1000;
      answers.value = idea?.answers || extra.base?.answers || draft.answers;
      answersLabel.append(answers); settings.append(answersLabel);
      if (extra.base) {
        const current = make("details", undefined, "technical");
        current.append(make("summary", `Current approved passage (${extra.base.id})`), make("pre", extra.base.body));
        form.append(current);
      }
      collect = () => {
        if (!title.value.trim() || !text.value.trim()) throw new Error("Give the passage a title and text.");
        if (source.value.trim().length < 8) throw new Error("Record the source you checked the passage against.");
        return { source: source.value.trim(), content: { title: title.value.trim(), journey_states: stages(),
          answers: answers.value.trim(), body: text.value.trim() } };
      };
    } else {
      form.append(make("p", this.next(proposal), "lead"));
    }

    // Footer: one short reason, one clear primary action. Clicking Approve is the approval.
    const footer = make("div", undefined, "idea-actions");
    const error = make("p", undefined, "notice error"); error.hidden = true; error.setAttribute("role", "alert");
    const reason = make("input"); reason.maxLength = 500; reason.placeholder = "Reason (optional when approving)";
    reason.setAttribute("aria-label", "Reason for your decision");
    const bar = make("div", undefined, "row-actions");
    footer.append(error, reason, bar);
    form.append(footer);
    const buttons = [];
    const submit = async (decision) => {
      if (buttons.some((item) => item.disabled)) return;
      error.hidden = true;
      let text = reason.value.trim();
      if (!text && decision === "approve") text = "Approved by owner.";
      if (text.length < 8) { error.textContent = "Add a short reason (at least 8 characters)."; error.hidden = false; return; }
      buttons.forEach((item) => { item.disabled = true; });
      try {
        const extras = decision === "approve" && collect ? collect() : {};
        if (extras.style && !Object.values(extras.style).every((value) => value.length)) throw new Error("Choose at least one option in each group under Where it can be used.");
        if (extras.content && !extras.content.journey_states.length) throw new Error("Choose at least one journey stage under Where it applies.");
        const result = await api("/api/proposals/decision", { proposal_id: proposal.proposal_id,
          content_digest: proposal.content_digest, decision, reason: text, confirmed: true, ...extras });
        await saved(result, decision);
      } catch (failure) {
        error.textContent = `${failure.message} If you retry, refresh first: the decision may already be recorded.`; error.hidden = false;
      } finally { buttons.forEach((item) => { item.disabled = false; }); }
    };
    const labels = { approve: kind === "strategy_gap" ? "Approve style" : "Approve", acknowledge: "Acknowledge", defer: "Defer", reject: "Reject" };
    for (const decision of ["approve", "acknowledge", "defer", "reject"]) {
      if (!proposal.allowed_decisions.includes(decision)) continue;
      const item = make("button", labels[decision]); item.type = "button";
      item.className = decision === "approve" || decision === "acknowledge" ? "primary" : "quiet";
      item.addEventListener("click", () => submit(decision));
      buttons.push(item); bar.append(item);
    }
    footer.append(make("p", this.next(proposal), "cell-note"));
    form.addEventListener("submit", (event) => event.preventDefault());
    target.replaceChildren(root);
  },
};

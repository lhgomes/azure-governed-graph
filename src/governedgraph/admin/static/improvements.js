"use strict";

// Improvement ideas: what the overnight review of declined answers found, and the owner's decision on each.
window.AdminImprovements = {
  kinds: {
    content_gap: "Missing approved content",
    content_revision: "Approved content to revise",
    strategy_gap: "Missing explanation style",
    false_negative_review: "Answers refused that may have been fine",
  },
  tones: { open: "warn", deferred: "", uncertain: "bad", rejected: "", acknowledged: "good", superseded: "",
    queued: "warn", queued_publication: "warn", published: "good" },
  label(row) {
    const place = row.situation;
    const words = (value) => String(value).replaceAll("_", " ");
    return `${words(place.topic)}: ${words(place.journey_state)}, ${words(place.channel)}`;
  },

  mount(target, options) {
    const { row, extra, data, api, saved } = options;
    const make = (tag, text, className) => {
      const element = document.createElement(tag);
      if (text !== undefined) element.textContent = text;
      if (className) element.className = className;
      return element;
    };
    const words = (value) => String(value).replaceAll("_", " ");
    const proposal = row, evidence = row.evidence, draft = row.draft;
    const body = make("div");
    body.append(make("p", proposal.rationale, "lead"));
    const facts = make("dl", undefined, "facts");
    const add = (key, value) => { if (value !== undefined && value !== "") facts.append(make("dt", key), make("dd", value)); };
    add("Status", proposal.status.label);
    add("Declined answers", `${evidence.declined} from ${evidence.journeys} journeys (${evidence.unanswered} not answered, ${evidence.review_flagged} flagged by the answer check, ${evidence.no_style || 0} with no style able to explain)`);
    add("Why they were declined", Object.entries(evidence.decline_reasons || {}).map(([key, count]) => `${words(key)}: ${count}`).join("\n"));
    add("Also handed to a person", evidence.escalated ? String(evidence.escalated) : "");
    add("Approved passage found", `${Math.round(evidence.content_retrieved_share * 100)}% of the time`);
    add("Styles that can explain here", evidence.eligible_styles.length ? evidence.eligible_styles.join(", ") : "None");
    add("What the answer check said", Object.entries(evidence.review_issues).map(([key, count]) => `${words(key)}: ${count}`).join("\n"));
    add("Passages used", Object.entries(evidence.content_versions).map(([key, count]) => `${key}: ${count}`).join("\n"));
    add("What members asked", (evidence.examples || []).map((item) => `“${item}”`).join("\n"));
    add("Example journeys", evidence.sample_journey_ids.join(", "));
    add("Period", `${evidence.window_start} to ${evidence.window_end}`);
    add("Reference", `${proposal.proposal_id} (review ${proposal.batch_id})`);
    body.append(facts);
    body.append(make("p", "Counts and labels come from the saved answer records. The questions above are what members actually asked, kept briefly for this review. Open an example journey in Conversations to judge whether an answer was fine.", "cell-note"));

    const decidable = ["open", "deferred", "uncertain"].includes(proposal.status.code);
    if (!decidable || !data.access.can_own) {
      body.append(make("p", decidable ? "Only an owner can decide on this idea." : "This idea already has a decision.", "notice"));
      target.replaceChildren(body);
      return;
    }
    const form = make("form");
    const field = (title, input) => { const label = make("label", title); label.append(input); form.append(label); return input; };
    const checks = (title, values, chosen, name) => {
      const group = make("fieldset"); group.append(make("legend", title));
      const inputs = values.map((value) => {
        const label = make("label", value === "*" ? "All stages" : words(value), "checkbox");
        const input = make("input"); input.type = "checkbox"; input.value = value; input.checked = chosen.includes(value);
        label.append(input); group.append(label); return input;
      });
      form.append(group); return () => inputs.filter((input) => input.checked).map((input) => input.value);
    };
    let collect = null;
    const kind = proposal.kind;
    if (kind === "strategy_gap") {
      form.append(make("p", "Approving queues this style for the next signed release. Learning starts fresh for it. Nothing changes until then.", "notice"));
      const name = field("Style name", make("input")); name.maxLength = 120; name.value = draft.name;
      const pattern = field("How it explains", make("select"));
      for (const item of data.style_patterns) { const option = make("option", item.name); option.value = item.strategy_id; pattern.append(option); }
      pattern.value = draft.pattern_id;
      const stages = checks("Journey stages", data.style_stages, draft.eligible_journey_states);
      const channels = checks("Channels", data.style_channels, draft.channels);
      const modes = checks("Accessibility", data.style_accessibility_modes, draft.accessibility_modes);
      collect = () => {
        if (!name.value.trim()) throw new Error("Give the style a name before approving.");
        return { expected_revision: data.revision, style: { name: name.value.trim(), pattern_id: pattern.value,
          eligible_journey_states: stages(), channels: channels(), accessibility_modes: modes() } };
      };
    } else if (kind === "content_gap" || kind === "content_revision") {
      form.append(make("p", kind === "content_gap"
        ? "Write the passage from an approved source. It is published only after you approve it here, and it may not hold figures from a member's own record."
        : "Edit a copy of the approved passage. Approving queues a new version for the content publisher. The old version is retired when it publishes.", "notice"));
      if (extra.related?.length) form.append(make("p", `Similar approved passages: ${extra.related.map((item) => `${item.id} (${item.title})`).join(", ")}`, "cell-note"));
      const idea = draft.suggested;
      if (idea) {
        const note = make("div", undefined, "notice");
        note.append(make("strong", `Drafted by an AI agent (${idea.agent}). Not checked by a person.`),
          make("p", `It was written from the model's own knowledge${idea.sources.length ? ` and kept consistent with: ${idea.sources.join(", ")}` : ""}. Facts and figures may be wrong or out of date. You are the one who makes them true.`));
        if (idea.themes.length) note.append(make("p", `What members are asking: ${idea.themes.join("; ")}`));
        if (idea.coverage.length) note.append(make("p", `Not covered by the approved passages: ${idea.coverage.join("; ")}`));
        if (idea.gaps.length) note.append(make("p", `The model left these out as uncertain: ${idea.gaps.join(" ")}`));
        form.append(note);
      }
      if (extra.base) {
        const current = make("details", undefined, "technical");
        current.append(make("summary", `Current approved passage (${extra.base.id})`), make("pre", extra.base.body));
        form.append(current);
      }
      const title = field("Title", make("input")); title.maxLength = 200; title.value = idea?.title || extra.base?.title || draft.title;
      const stages = checks("Journey stages", extra.stages, extra.base?.journey_states ?? draft.journey_states);
      const answers = field("Questions this passage answers", make("textarea")); answers.rows = 2; answers.maxLength = 1000;
      answers.value = idea?.answers || extra.base?.answers || draft.answers;
      const text = field("Passage", make("textarea")); text.rows = 8; text.maxLength = 8000;
      text.value = idea?.body || extra.base?.body || "";
      if (idea?.claims.length) {
        const claims = make("div", undefined, "notice");
        claims.append(make("strong", "Check each of these against an authoritative source before you approve:"));
        const list = make("ul"); idea.claims.forEach((claim) => list.append(make("li", claim))); claims.append(list);
        form.append(claims);
      }
      const source = field("Source you checked the passage against (document or authority, required to approve)", make("input"));
      source.maxLength = 300; source.placeholder = "For example: the fund's pension regulations, article and date";
      collect = () => {
        if (!title.value.trim() || !text.value.trim()) throw new Error("Give the passage a title and text before approving.");
        if (source.value.trim().length < 8) throw new Error("Record the source you checked the passage against.");
        return { source: source.value.trim(),
          content: { title: title.value.trim(), journey_states: stages(), answers: answers.value.trim(), body: text.value.trim() } };
      };
    } else {
      form.append(make("p", "Acknowledging records that a person looked at examples. It changes no check. Changes to a check go through the technical owners.", "notice"));
    }
    const reason = field("Reason (no member details or conversation text)", make("textarea"));
    reason.required = true; reason.minLength = 8; reason.maxLength = 500; reason.rows = 3;
    const confirm = make("label", undefined, "checkbox");
    const tick = make("input"); tick.type = "checkbox"; tick.required = true;
    confirm.append(tick, make("span", "I decide this as a human owner, have verified every claim against the source above, and have read what this does."));
    form.append(confirm);
    const error = make("p", undefined, "notice error"); error.hidden = true; error.setAttribute("role", "alert");
    const bar = make("div", undefined, "row-actions");
    form.append(error, bar);
    const buttons = [];
    const submit = async (decision) => {
      if (buttons.some((item) => item.disabled) || !form.reportValidity()) return;
      error.hidden = true; buttons.forEach((item) => { item.disabled = true; });
      try {
        const extras = decision === "approve" && collect ? collect() : {};
        if (extras.style && !Object.values(extras.style).every((value) => value.length)) throw new Error("Select at least one choice in each group.");
        if (extras.content && !extras.content.journey_states.length) throw new Error("Select at least one journey stage.");
        const result = await api("/api/proposals/decision", { proposal_id: proposal.proposal_id,
          content_digest: proposal.content_digest, decision, reason: reason.value.trim(), confirmed: true, ...extras });
        await saved(result, decision);
      } catch (failure) {
        error.textContent = `${failure.message} Refresh before retrying; the decision may already be recorded.`; error.hidden = false;
      } finally { buttons.forEach((item) => { item.disabled = false; }); }
    };
    const labels = { approve: kind === "strategy_gap" ? "Approve and queue style" : "Approve and queue for publishing",
      acknowledge: "Acknowledge", defer: "Defer", reject: "Reject" };
    for (const decision of ["approve", "acknowledge", "defer", "reject"]) {
      if (!proposal.allowed_decisions.includes(decision)) continue;
      const item = make("button", labels[decision]); item.type = "button";
      if (decision === "approve") item.className = "primary";
      item.addEventListener("click", () => submit(decision));
      buttons.push(item); bar.append(item);
    }
    form.addEventListener("submit", (event) => event.preventDefault());
    body.append(form);
    target.replaceChildren(body);
  },
};

"use strict";

window.AdminStyleEditor = {
  mount(target, options) {
    const make = (tag, text) => {
      const element = document.createElement(tag);
      if (text !== undefined) element.textContent = text;
      return element;
    };
    const { action, style, data, api, saved } = options;
    const editing = action === "add" || action === "edit";
    const form = make("form");
    const warning = make("p", action === "cancel"
      ? "Cancel the queued publication. The active catalogue will not change."
      : "Publication creates a new signed expert baseline. Learning starts fresh for changed styles. Protective pauses stay in force; existing owner order and unpause overrides are cleared.");
    warning.className = "notice";
    form.append(warning);
    const field = (title, input) => {
      const label = make("label", title);
      label.append(input);
      form.append(label);
      return input;
    };
    let name, pattern;
    const selections = {};
    if (editing) {
      name = field("Style name", make("input"));
      name.required = true; name.maxLength = 120; name.value = style?.name || "";
      pattern = field("How it explains", make("select"));
      pattern.required = true;
      if (!Array.isArray(data.style_patterns) || !data.style_patterns.length) {
        target.replaceChildren(make("p", "Approved explanation patterns are unavailable. Refresh before editing."));
        return;
      }
      for (const item of data.style_patterns) {
        const option = make("option", item.name); option.value = item.strategy_id;
        pattern.append(option);
        if (style?.template_id === item.template_id && style?.template_version === item.template_version) {
          pattern.value = item.strategy_id;
        }
      }
      if (!style) pattern.value = data.style_patterns[0].strategy_id;
      const choices = [
        ["eligible_journey_states", "Journey stages", data.style_stages],
        ["channels", "Channels", data.style_channels],
        ["accessibility_modes", "Accessibility", data.style_accessibility_modes],
      ];
      for (const [key, title, values] of choices) {
        if (!Array.isArray(values) || !values.length) {
          target.replaceChildren(make("p", "Approved eligibility settings are unavailable. Refresh before editing."));
          return;
        }
        const group = make("fieldset");
        group.append(make("legend", title));
        selections[key] = [];
        for (const value of values) {
          const label = make("label", value.replaceAll("_", " "));
          const input = make("input"); input.type = "checkbox"; input.value = value;
          input.checked = style ? style[key].includes(value) || style[key].includes("*") : true;
          label.className = "checkbox"; label.append(input);
          group.append(label); selections[key].push(input);
        }
        form.append(group);
      }
    } else if (style) {
      form.append(make("p", `Remove "${style.name}" from future selection. Existing answer and audit records are retained.`));
    }
    const reason = field("Reason (no member details or conversation text)", make("textarea"));
    reason.required = true; reason.minLength = 8; reason.maxLength = 500; reason.rows = 3;
    const error = make("p"); error.className = "notice error"; error.hidden = true; error.setAttribute("role", "alert");
    const submit = make("button", action === "cancel" ? "Cancel publication" : "Publish changes");
    submit.type = "submit";
    form.append(error, submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submit.disabled || !form.reportValidity()) return;
      error.hidden = true;
      const change = { action };
      if (style) change.strategy_id = style.strategy_id;
      if (editing) {
        change.name = name.value.trim(); change.pattern_id = pattern.value;
        for (const [key, inputs] of Object.entries(selections)) {
          change[key] = inputs.filter((input) => input.checked).map((input) => input.value);
        }
        if (Object.values(selections).some((inputs) => !inputs.some((input) => input.checked))) {
          error.textContent = "Select at least one choice in each eligibility group."; error.hidden = false;
          return;
        }
      }
      submit.disabled = true;
      try {
        const result = await api("/api/catalogue", { expected_revision: data.revision,
          reason: reason.value.trim(), confirmed: true, change });
        await saved(result);
      } catch (failure) {
        error.textContent = `${failure.message} Refresh before retrying; the request may already be recorded.`;
        error.hidden = false;
      } finally {
        submit.disabled = false;
      }
    });
    target.replaceChildren(form);
  },
};

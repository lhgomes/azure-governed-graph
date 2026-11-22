"use strict";
// All wording shown to console users lives here, so it can be reviewed and edited without touching the logic.
// Rules: short sentences, everyday words, say what it means and why it matters. Where a technical name exists
// (used in the documentation), it goes in `also`, so the two always line up.
window.GUIDE = {
  about: "See each explanation style, its availability and recorded use. Owners can approve, inspect and revoke manual overrides. Safety controls remain available to operators.",
  styleDetail: {
    intro: "One approved way of explaining. Its availability, use, orders, pauses and learning results are collected here.",
    advisor: "The human advisor is always available, never ranked and cannot be paused.",
    noOrders: "This style does not appear in the configured orders.",
    learningNote: "Published learning counts can include the approved example baseline. They are separate from recorded uses and do not prove delivery to a member.",
    reorder: "Owners can set an order for a situation. It remains pending until the next successful signed publication.",
    unpause: "Owners can lift an existing finding or revoke an owner pause. New fairness findings can pause the style again.",
    changeLimits: "Owners can approve and revoke overrides here. Pending orders are not active. New fairness findings and protective freezes take precedence.",
    orderColumns: [
      ["Situation", "Stage, channel and accessibility setting, with the topic underneath. Select to inspect both full orders."],
      ["Expert position", "This style's position after eligibility rules and pauses, not a count of uses."],
      ["Current position", "This style's position in the learned group, including an active owner order, after eligibility rules and pauses."],
      ["Comparison", "Whether the available orders differ, match, or use the expert fallback."],
    ],
    pauseColumns: [
      ["Paused stage", "Select to see the pause and its supporting check."],
      ["Review", "The review reference recorded for this pause."],
      ["Since", "When the pause was recorded."],
    ],
    learningColumns: [
      ["Situation", "Select to see the published results and whether enough observations are available."],
      ["Published uses", "Published counts, which can include approved example data. These are not live usage totals."],
      ["Settled first time", "Published outcomes where the question was settled on first contact."],
      ["Came back", "Published repeat contacts within fourteen days."],
    ],
  },
  testing: {
    intro: "Test a selected style here using the configured Azure conversation service.",
    selection: "The selected style is used when eligible and available. Pauses and human-advisor rules still apply.",
    chat: "Choose Chat, start the test, and type a question.",
    voice: "Choose Voice and record your question after the opening message. Microphone permission is required.",
    personas: "Choose Persona, select a made-up member, and start its approved conversation.",
    verify: "Each answer shows which style was actually used. Test conversations do not change learning results.",
  },
  ranking: {
    description: "Rows are situations, not styles. Both orders exclude ineligible and paused styles. The learned column explains whether the available starting order differs.",
    pauseNote: "Excluded from both orders here. The situation itself is not paused.",
    changed: {
      label: "Different from expert order",
      note: "A verified published order changes the starting order here. This alone does not show better outcomes.",
    },
    unchanged: {
      label: "Same as expert order",
      note: "A verified published order is available, but its order matches after eligibility rules and pauses. This view does not record why.",
    },
    fallback: {
      label: "Expert order used",
      note: "No published learned order covers this situation. Both groups use the expert starting order.",
    },
    unavailable: {
      label: "No eligible explanation style",
      note: "No explanation style remains available in this order. The human advisor is still available.",
    },
  },
  startHere: [
    "Start with Explanation styles. Each style appears once, with its status, availability and recorded use.",
    "Select any blue name to read it in plain words. Select a ? for a short definition.",
    "Use the safety controls only if something looks wrong. They can only make the system safer.",
  ],
  terms: {
    style: {
      name: "Explanation style",
      plain: "One approved way of explaining something, such as a plain summary or a worked example. The system can only use styles that people have approved.",
      also: "Called a strategy in the technical documents.",
    },
    order: {
      name: "Style order",
      plain: "For each situation, the starting order of styles. The system may sometimes try another approved style to keep learning.",
      also: "Called a ranking.",
    },
    situation: {
      name: "Situation",
      plain: "A kind of moment in a member's journey. It combines the stage, the topic, the channel (chat or voice) and any accessibility setting.",
      also: "Called a request key.",
    },
    expert: {
      name: "Expert order",
      plain: "The order set by experts. A small share of members always get this order, so the system must prove its learned order is better.",
      also: "Called the control group, or expert priors.",
    },
    learned: {
      name: "Learned order",
      plain: "The order the system worked out from past results. It can only reorder approved styles, and only by small steps.",
      also: "Called the treatment group.",
    },
    pause: {
      name: "Paused style",
      plain: "A style switched off for everyone at one stage of the journey, because a fairness check found a gap. Pausing is automatic and always safe.",
      also: "Called a veto or a suspension.",
    },
    review: {
      name: "Fairness review",
      plain: "A record made when the fairness check pauses a style. It holds the evidence and says who must approve bringing the style back.",
      also: "Called a governance case.",
    },
    outcome: {
      name: "Review outcome",
      plain: "The result of the checks made on a proposed change to the order, or on whether learning beats the expert order.",
      also: "Called an assurance decision.",
    },
    levels: {
      name: "Levels of approval",
      plain: "Small, safe changes happen automatically. Bigger changes get automatic checks first. Anything that widens what the system may say or use needs a person.",
      also: "Called Tier 0, Tier 1 and Tier 2.",
    },
    published: {
      name: "Published order",
      plain: "A signed, dated copy of the learned order. The system checks the signature before it uses it. With no published order, everyone gets the expert order.",
      also: "Called a signed snapshot.",
    },
    counts: {
      name: "Outcome counts",
      plain: "Totals only: how often each style was used, and how often the member's question was settled the first time. No names and no conversation text.",
      also: "Called outcome aggregates.",
    },
    enough: {
      name: "Enough data to learn",
      plain: "The system waits until a style has been used a minimum number of times before it can change the order. Until then it keeps the expert order.",
      also: "Called the sample gate.",
    },
    frozen: {
      name: "Learning paused",
      plain: "No new learned order is published. The system goes back to the last good order, if there is one.",
      also: "Called a freeze.",
    },
    journey: {
      name: "Member journey",
      plain: "One member's conversation with the service, from the first question to the end. Only made-up members appear here.",
      also: "",
    },
    record: {
      name: "Answer record",
      plain: "A saved note of each answer: which style was used, which order was active, and when. It cannot be changed afterwards and holds no member details.",
      also: "Called an audit or explainability record.",
    },
    advisor: {
      name: "Human advisor",
      plain: "A person members can always ask for. It is never ranked, and it can never be paused.",
      also: "Style S6 in the sample.",
    },
    idea: {
      name: "Improvement idea",
      plain: "A suggestion from the regular review of answers that were not given. It changes nothing until an owner approves it.",
      also: "Called a gap-review proposal.",
    },
    declined: {
      name: "Declined answer",
      plain: "A question where the member got no explanation, or where a check flagged the answer. Some of these answers may have been fine.",
      also: "Called a declined interaction. A fine answer that was refused is a false negative.",
    },
    content: {
      name: "Approved content",
      plain: "The reviewed passages the service may use to answer. If no passage fits a question, the service cannot answer it.",
      also: "Called P3 approved content.",
    },
    synthetic: {
      name: "Made-up data",
      plain: "Example data created for demonstrations. No real people, funds or accounts are involved.",
      also: "Called synthetic data.",
    },
  },
  sections: {
    strategies: {
      title: "Explanation styles",
      heading: "Styles, availability and use",
      description: "One row per style. Orders, learning results and pause details are inside its detail view.",
      what: "One list replaces the separate overview, order, pause and learning pages.",
      look: "Partly paused means the style is blocked at some stages, not everywhere. Recorded uses count saved answer records, not published example totals.",
      can: "Open Details for controls and results. Test here using chat, voice or a made-up persona. Owners can also add, edit and remove styles.",
      terms: ["style", "advisor", "pause", "order", "counts"],
      empty: "No explanation styles were found. This is unexpected. Ask a technical operator to check the approved catalogue.",
      columns: [
        ["Style", "The approved way of explaining. Select a name to see the details."],
        ["Status", "Shows active, partly paused, fully paused or always available. Approval alone does not mean available everywhere."],
        ["How it explains", "The approved explanation pattern, including whether it ends with a check-back question."],
        ["Where it can be used", "Approved journey stages and channels. Pauses can further restrict availability."],
        ["Recorded uses", "Saved answer records naming this style. A loaded sample is labelled. These counts do not prove delivery or lifetime usage."],
        ["Actions", "Open Details for controls, orders and results. Test starts chat, voice or a made-up persona here."],
      ],
    },
    journeys: {
      title: "Conversations",
      heading: "How questions were answered",
      description: "Each row is one member's conversation. You can see which style was used for each answer.",
      what: "A list of conversations with made-up members, taken from the saved answer records.",
      look: "Which styles were used, and how many answers were given.",
      can: "Select a journey to read its answer records. If conversation capture is on, you can also read the example conversation.",
      terms: ["journey", "record", "synthetic", "style"],
      empty: "No journeys found. Run a journey or the sample demo to create some.",
      columns: [
        ["Journey", "One member's conversation. Select it to see each answer."],
        ["Member", "A made-up member. No real people appear here."],
        ["Answers", "How many answers were given."],
        ["Styles used", "Which styles were used in the answers."],
        ["Last activity", "When the last answer was given."],
      ],
    },
    improvements: {
      title: "Improvement ideas",
      heading: "Missed answers and proposed fixes",
      description: "A regular review finds questions that got no answer. It proposes missing content or styles for an owner to decide.",
      what: "One row per idea. Each idea comes from counts of declined answers in one situation. It holds no words a member said.",
      look: "Ideas with many journeys are stronger than ideas with few. An idea about answers that may have been fine needs a person to look at examples first.",
      can: "Open an idea to read the evidence. Owners can approve, reject, defer or acknowledge it. Approving queues the change. It never takes effect by itself.",
      terms: ["idea", "declined", "content", "style", "advisor"],
      empty: "No ideas yet. The review needs enough declined answers from enough journeys before it proposes anything.",
      columns: [
        ["Idea", "The topic and situation the review looked at. Select it to read the evidence and decide."],
        ["Status", "Open means no decision yet. Approved ideas show whether they are queued or live."],
        ["Evidence", "How many declined answers, and from how many journeys."],
        ["Found", "When the review last updated this idea."],
      ],
    },
    activity: {
      title: "Activity",
      heading: "Reviews, checks and actions",
      description: "One history for fairness reviews, order checks and console actions. Open an entry for its full record.",
      what: "Related records are collected in one list. Each console action appears once, with all its recorded steps inside.",
      look: "Requested without a completion means the outcome is uncertain. An automatic check is not a human approval.",
      can: "Open an entry to inspect the review, checks or action journal. This view cannot grant approvals.",
      terms: ["review", "outcome", "levels", "pause"],
      empty: "No reviews, checks or console actions were found. This does not establish that the system is healthy.",
      columns: [
        ["Entry", "A fairness review, order check or console action. Select it for the full record."],
        ["Type", "Distinguishes a fairness review, automatic check or operator action."],
        ["Result", "The recorded result or required approval. Requested without completion is not success."],
        ["Context", "The affected style, check level or operator's reason."],
        ["When", "When it was recorded."],
      ],
    },
  },
  promises: [
    "Improvement ideas use counts and labels, never what a member said.",
    "An idea changes nothing until an owner approves it, and approved changes use the usual release steps.",
    "Owners can add, edit or remove explanation styles. The human advisor cannot be changed.",
    "It cannot activate an unsigned order or change any AI model.",
    "Owners can lift existing fairness findings, not future findings. New findings can pause the style again.",
    "Every owner override can be inspected and revoked.",
    "Everything you do here is written down before it happens.",
    "What you see or do here is never used for learning.",
    "A human advisor is always available to members.",
  ],
  tasks: [
    { title: "Check pauses and orders", go: "strategies", steps: [
      "Open Explanation styles and check Status.", "Select Details for a style.", "Inspect its orders and pause details without switching pages."] },
    { title: "Override or revoke a decision", go: "strategies", steps: [
      "Sign in as an owner and open a style's Details.", "Choose Reorder, Pause override, Unpause, or Revoke override.",
      "Review the effect, give a reason and confirm. Pending orders are not active until signed."] },
    { title: "See how a member's question was answered", go: "journeys", steps: [
      "Open Conversations.", "Select a journey.", "Read its answer records. If capture is on, you can also read the example conversation."] },
    { title: "Check learning and review results", go: "activity", steps: [
      "Open a style's Details for its learning results.", "Open Activity for reviews and checks.", "A changed order alone is not proof of improvement."] },
    { title: "Try chat, voice or a persona", go: "strategies", steps: [
      "Select Test beside a style.", "Choose Chat, Voice or Persona, then start the test.", "Check the answer style shown for each response. Test results never enter learning."] },
    { title: "Review an improvement idea", go: "improvements", steps: [
      "Open Improvement ideas and select an open idea.", "Read the evidence and open an example journey in Conversations if you can.",
      "Approve, reject, defer or acknowledge it, and give a reason. Approved changes are queued, not live."] },
    { title: "Stop something that looks wrong", go: "controls", steps: [
      "Select Safety controls, top right.", "Choose the smallest action that fixes the problem.", "Write the reason. Everything is recorded."] },
  ],
  actions: {
    freeze: { label: "Pause learning", full: "Pause learning",
      impact: "Stops new publication and revokes owner orders and lifted pauses. The system restores the last good order if available. Owner pauses remain. Restarting learning needs human approval." },
    revert_to_static: { label: "Go back to the expert order and pause learning", full: "Go back to the expert order and pause learning",
      impact: "Every member gets the expert order, skipping any paused style. Learning is paused. Restarting it needs a person with approval rights." },
    suspend: { label: "Pause one style for everyone", full: "Pause one style for everyone",
      impact: "The chosen style is switched off for everyone at the chosen stage. The human advisor stays available. Owners can approve bringing it back from Details." },
  },
  ownerActions: {
    owner_order: "Order override", owner_pause: "Pause override", owner_unpause: "Unpause override",
    owner_revoke: "Revoke override",
    proposal_content_approve: "Content approved for publishing", proposal_reject: "Improvement idea rejected",
    proposal_defer: "Improvement idea deferred", proposal_acknowledge: "Improvement idea acknowledged",
  },
  labels: {
    status: { requested: "Requested, not finished", applied: "Done", failed: "Failed",
      pending_signature: "Recorded; signature pending at acceptance", pending_publication: "Approved; waiting for the content publisher",
      uncertain: "Outcome uncertain; investigate before retrying" },
    outcome: { approved: "Approved", continue: "Keep going", keep_learning: "Keep learning" },
    caseType: { automatic_suspension: "Automatic pause" },
    kinds: { tier1_review: "Review of a proposed new order", uplift_window: "Is learning beating the expert order?" },
    levels: { tier0: "Automatic", tier1: "Automatic review", tier2: "A person approves", "Tier 2 to reinstate": "A person must approve bringing it back" },
  },
};

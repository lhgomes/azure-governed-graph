"use strict";

window.AdminStyleTest = class {
  constructor(style, request) {
    this.style = style; this.request = request; this.closed = false; this.ready = false;
    this.element = node("section", null, "style-test");
    this.mode = node("select"); this.mode.id = "test-mode";
    [["chat", "Chat"], ["presenter", "Voice"], ["simulator", "Persona"]].forEach(([value, name]) => {
      const option = node("option", name); option.value = value; this.mode.append(option);
    });
    this.persona = node("select"); this.persona.id = "test-persona"; this.persona.hidden = true;
    this.personaLabel = node("label", "Persona"); this.personaLabel.htmlFor = this.persona.id;
    this.personaLabel.hidden = true;
    const modeLabel = node("label", "Test with"); modeLabel.htmlFor = this.mode.id;
    this.startButton = button("Start test", () => this.start(), "primary"); this.startButton.disabled = true;
    this.endButton = button("End test", () => this.end(), "quiet"); this.endButton.disabled = true;
    this.status = node("p", "Loading test controls...", "cell-note");
    this.status.setAttribute("role", "status");
    this.result = node("p", "", "notice"); this.result.hidden = true;
    this.result.setAttribute("aria-live", "polite");
    this.error = node("p", "", "notice error"); this.error.hidden = true; this.error.setAttribute("role", "alert");
    this.transcript = node("div", null, "test-transcript"); this.transcript.setAttribute("role", "log");
    this.transcript.setAttribute("aria-label", "Test conversation");
    this.form = node("form", null, "test-composer"); const textLabel = node("label", "Your question"); textLabel.htmlFor = "test-question";
    this.input = node("textarea"); this.input.id = "test-question"; this.input.maxLength = 4000; this.input.rows = 2;
    this.send = node("button", "Send question"); this.send.type = "submit";
    this.form.append(textLabel, this.input, this.send); this.form.hidden = true;
    this.recordButton = button("Record question", () => this.capture(), "quiet"); this.recordButton.hidden = true;
    const controls = node("div", null, "test-controls");
    const actions = node("div", null, "row-actions");
    actions.append(this.startButton, this.endButton);
    controls.append(modeLabel, this.mode, this.personaLabel, this.persona, actions);
    this.element.append(controls, this.status, this.result, this.error, this.transcript, this.form, this.recordButton);
    this.mode.addEventListener("change", () => {
      this.persona.hidden = this.personaLabel.hidden = this.mode.value !== "simulator";
    });
    this.form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!this.input.value.trim() || this.send.disabled) return;
      const text = this.input.value; this.input.value = "";
      this.caption("You", text); this.send.disabled = true;
      this.sendMessage({kind: "chat", request_id: crypto.randomUUID(), text});
    });
    this.request("/config").then((config) => {
      if (this.closed) return;
      config.simulations.forEach((item) => {
        const option = node("option", item.display_name); option.value = item.simulation_id; this.persona.append(option);
      });
      this.ready = true; this.startButton.disabled = false; this.status.textContent = "Choose a mode and start the selected-style test.";
    }).catch((error) => { if (!this.closed) this.fail(error); });
  }
  caption(role, text) {
    const line = node("p"); line.append(node("strong", `${role}: `), node("span", text));
    this.transcript.append(line); this.transcript.scrollTop = this.transcript.scrollHeight;
  }
  sendMessage(message) {
    if (this.socket?.readyState !== WebSocket.OPEN) throw new Error("The test connection is closed. Start a new test.");
    this.socket.send(JSON.stringify(message));
  }
  async start() {
    this.form.hidden = true;
    this.startButton.disabled = true; this.mode.disabled = this.persona.disabled = true;
    this.error.hidden = this.result.hidden = true; this.transcript.replaceChildren();
    this.status.textContent = "Connecting...";
    try {
      if (this.mode.value !== "chat") {
        this.audio = new AudioContext({sampleRate: 24000});
        await this.audio.resume(); this.playback = Promise.resolve();
        if (this.audio.sampleRate !== 24000) throw new Error("Voice testing requires a browser supporting 24-kHz audio.");
      }
      await this.request("/authorize", {});
      if (this.closed) { this.end(); return; }
      const url = new URL("../admin-test-stream", document.baseURI); url.protocol = "wss:";
      const socket = new WebSocket(url); this.socket = socket; socket.binaryType = "arraybuffer";
      socket.onopen = () => {
        this.endButton.disabled = false;
        this.sendMessage({strategy_id: this.style.strategy_id, mode: this.mode.value,
          ...(this.mode.value === "simulator" ? {simulation_id: this.persona.value} : {})});
      };
      socket.onmessage = (event) => {
        try { this.message(event.data); } catch (error) { this.fail(error); }
      };
      socket.onerror = () => this.fail(new Error("The Azure test connection failed. Check sign-in and operator diagnostics."));
      socket.onclose = () => {
        if (this.socket !== socket) return;
        if (!this.finished && !this.closed) {
          this.error.textContent = "The test connection ended before completion."; this.error.hidden = false;
        }
        this.end();
      };
      this.finished = false;
    } catch (error) { this.fail(error); }
  }
  message(data) {
    if (data instanceof ArrayBuffer) {
      const header = this.pendingAudio; this.pendingAudio = null;
      if (!header || !data.byteLength || data.byteLength % 2) throw new Error("The test returned invalid audio.");
      this.playback = this.playback.then(() => new Promise((resolve) => {
        if (this.closed || !this.audio) { resolve(); return; }
        const pcm = new Int16Array(data), buffer = this.audio.createBuffer(1, pcm.length, 24000);
        const samples = buffer.getChannelData(0); pcm.forEach((value, index) => { samples[index] = value / 32768; });
        const source = this.audio.createBufferSource(); source.buffer = buffer; source.connect(this.audio.destination);
        source.onended = () => { if (this.socket?.readyState === WebSocket.OPEN) this.sendMessage({kind: "played", id: header.id}); resolve(); };
        source.start();
      })).catch((error) => this.fail(error));
      return;
    }
    const message = JSON.parse(data);
    if (message.kind === "error") throw new Error(message.message);
    if (message.kind === "caption") this.caption(message.role, message.text);
    if (message.kind === "audio") { this.pendingAudio = message; this.caption(message.role, message.text); }
    if (message.kind === "thinking") { this.send.disabled = true; this.status.textContent = "Preparing a governed answer..."; }
    if (message.kind === "chat_ready") {
      this.form.hidden = false; this.send.disabled = this.input.disabled = false;
      this.input.focus(); this.status.textContent = "Ready for your question.";
    }
    if (message.kind === "capture") {
      this.recordButton.hidden = false; this.recordButton.disabled = false;
      this.status.textContent = "Record your next question, then send the recording.";
    }
    if (message.kind === "progress" && message.strategy) {
      this.result.hidden = false;
      this.result.textContent = message.strategy === this.style.strategy_id
        ? `Answer style: ${message.strategy}. The selected style was used.`
        : `Answer style: ${message.strategy}, not ${this.style.strategy_id}. Pauses, eligibility and human-advisor rules still apply.`;
    }
    if (["complete", "session_ended", "chat_complete"].includes(message.kind)) {
      this.finished = true; this.status.textContent = "Test finished."; this.recordButton.hidden = true;
      this.endButton.disabled = true;
      if (message.kind === "complete" && (!message.audit_written || !message.numeric_fidelity)) {
        throw new Error("The test did not confirm both the saved answer records and numeric fidelity.");
      }
      this.end();
    }
  }
  async capture() {
    if (this.microphone) { this.finishRecording(); return; }
    this.recordButton.disabled = true;
    try {
      this.microphone = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, sampleRate: 24000}});
      if (this.closed || !this.audio) { this.releaseMicrophone(); return; }
      this.samples = []; this.inputSource = this.audio.createMediaStreamSource(this.microphone);
      this.processor = this.audio.createScriptProcessor(4096, 1, 1);
      this.processor.onaudioprocess = (event) => {
        this.samples.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };
      this.silence = this.audio.createGain(); this.silence.gain.value = 0;
      this.inputSource.connect(this.processor); this.processor.connect(this.silence); this.silence.connect(this.audio.destination);
      this.recordButton.textContent = "Send recording"; this.recordButton.disabled = false;
      this.timer = setTimeout(() => this.finishRecording(), 20000);
    } catch (error) { this.fail(error); }
  }
  finishRecording() {
    this.releaseMicrophone();
    const length = Math.min(24000 * 20, this.samples.reduce((sum, chunk) => sum + chunk.length, 0));
    if (!length) { this.fail(new Error("No microphone audio was captured. Start a new test.")); return; }
    const pcm = new Int16Array(length); let offset = 0;
    for (const chunk of this.samples) {
      for (const value of chunk) {
        if (offset >= length) break;
        pcm[offset++] = Math.round(Math.max(-1, Math.min(1, value)) * 32767);
      }
    }
    this.recordButton.textContent = "Record question"; this.recordButton.disabled = true;
    this.status.textContent = "Processing the recorded question..."; this.socket.send(pcm.buffer);
  }
  releaseMicrophone() {
    clearTimeout(this.timer);
    this.processor?.disconnect(); this.inputSource?.disconnect(); this.silence?.disconnect();
    this.microphone?.getTracks().forEach((track) => track.stop()); this.microphone = null;
  }
  end() {
    this.releaseMicrophone();
    const socket = this.socket; this.socket = null; socket?.close();
    if (this.audio) { this.audio.close().catch((error) => { this.error.textContent = error.message; this.error.hidden = false; }); this.audio = null; }
    this.send.disabled = this.input.disabled = true; this.recordButton.hidden = true;
    this.startButton.disabled = !this.ready; this.endButton.disabled = true; this.mode.disabled = this.persona.disabled = false;
    if (!this.finished) this.status.textContent = "Test ended. Start a new test to continue.";
  }
  fail(error) { this.error.textContent = error.message || String(error); this.error.hidden = false; this.end(); }
  stop() { this.closed = true; this.end(); }
};

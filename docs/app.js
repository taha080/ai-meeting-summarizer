(function () {
  var S = window.MeetingSummarizer;
  var STORAGE_KEY = "ai-meeting-summarizer.groqApiKey";

  var SAMPLE_NOTES = [
    "Product sync — Monday, October 19",
    "Attendees: Priya Shah (PM), Luis Ortega (Engineering), Maya Chen (Design), Jonah Brooks (Customer Success)",
    "",
    "Priya: Thanks everyone. The goal today is to lock the launch plan for the mobile checklist feature and the support handoff.",
    "",
    "Maya: Usability tests wrapped Friday. Two issues are blocking: the empty-state copy is confusing, and the offline banner disappears too quickly. I can deliver revised copy and banner specs by Wednesday, October 21.",
    "",
    "Luis: The offline bug is in the sync worker. I'll patch it and open the release branch by Thursday, October 22. We should not start the staged rollout until that fix is in build 1.4.2.",
    "",
    "Priya: Decision: the staged rollout starts Monday, October 26, and only after build 1.4.2 includes Luis's sync fix and Maya's copy update. We're aiming the full rollout for Friday, October 30, assuming the staged metrics stay healthy.",
    "",
    "Jonah: Enterprise accounts asked for a one-page support note. I'll draft the help-center article and send it to Maya for review by Thursday, October 22. Support should not promise Android tablet support. That stays out of this release.",
    "",
    "Priya: Agreed. Decision: Android tablet support is deferred to next quarter. Luis, please note that in the release notes. Jonah owns the customer email, due Wednesday, October 21, announcing the Monday staged rollout and the tablet deferral.",
    "",
    "Luis: I'll also add a migration check for users still on version 1.3. That's part of my Thursday patch, not a separate project.",
    "",
    "Maya: I'll post the final screens in the launch channel once copy is approved.",
    "",
    "Priya: Decision: we go to full rollout on October 30 only if crash-free sessions stay above 99.5 percent during the staged rollout and support volume doesn't spike. I'll publish the go/no-go checklist tomorrow, October 20.",
    "",
    "Jonah: If support volume doubles, I'll flag it in the launch channel the same day.",
    "",
    "Priya: Thanks. The recap after this meeting will live in the project doc. That's all.",
  ].join("\n");

  var keyInput = document.getElementById("api-key");
  var keyStatus = document.getElementById("key-status");
  var toggleKey = document.getElementById("toggle-key");
  var forgetKey = document.getElementById("forget-key");
  var notes = document.getElementById("notes");
  var notesMeta = document.getElementById("notes-meta");
  var audioInput = document.getElementById("audio");
  var dropzone = document.getElementById("dropzone");
  var audioName = document.getElementById("audio-name");
  var clearAudio = document.getElementById("clear-audio");
  var submitButton = document.getElementById("submit");
  var loadSample = document.getElementById("load-sample");
  var statusNode = document.getElementById("status");
  var errorNode = document.getElementById("error");
  var results = document.getElementById("results");
  var resultModel = document.getElementById("result-model");
  var briefing = document.getElementById("briefing");
  var sourceDetails = document.getElementById("source-details");
  var sourceText = document.getElementById("source-text");
  var copyMarkdown = document.getElementById("copy-markdown");
  var copyJson = document.getElementById("copy-json");

  var latest = null;
  var running = false;
  var saveTimer = 0;

  function readStoredKey() {
    try {
      return localStorage.getItem(STORAGE_KEY) || "";
    } catch (error) {
      return "";
    }
  }

  function writeStoredKey(value) {
    try {
      if (value) localStorage.setItem(STORAGE_KEY, value);
      else localStorage.removeItem(STORAGE_KEY);
      return true;
    } catch (error) {
      return false;
    }
  }

  function setKeyStatus(message) {
    keyStatus.textContent = message;
  }

  function refreshKeyStatus() {
    var value = keyInput.value.trim();
    if (!value) {
      setKeyStatus("Nothing is stored yet.");
      return;
    }
    var stored = readStoredKey();
    if (stored && stored === value) setKeyStatus("Stored in this browser only.");
    else setKeyStatus("Not stored yet. It will be saved in this browser when you summarize.");
  }

  function scheduleSave() {
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(function () {
      var value = keyInput.value.trim();
      if (!value) {
        writeStoredKey("");
        refreshKeyStatus();
        return;
      }
      var saved = writeStoredKey(value);
      setKeyStatus(saved ? "Stored in this browser only." : "This browser blocked storage. The key stays in the field until you close the tab.");
    }, 250);
  }

  function setError(message) {
    errorNode.textContent = message || "";
    errorNode.hidden = !message;
  }

  function setStatus(message) {
    statusNode.textContent = message || "";
  }

  function selectedAudio() {
    return audioInput.files && audioInput.files[0] ? audioInput.files[0] : null;
  }

  function refreshAudioLabel() {
    var file = selectedAudio();
    if (!file) {
      audioName.textContent = "No file selected";
      clearAudio.hidden = true;
      dropzone.classList.remove("has-file");
      return;
    }
    var megabytes = file.size / (1024 * 1024);
    audioName.textContent = file.name + " · " + megabytes.toFixed(megabytes < 10 ? 1 : 0) + " MB";
    clearAudio.hidden = false;
    dropzone.classList.add("has-file");
  }

  function clearChosenAudio() {
    var transfer = new DataTransfer();
    audioInput.files = transfer.files;
    refreshAudioLabel();
  }

  function assignAudio(file) {
    var transfer = new DataTransfer();
    transfer.items.add(file);
    audioInput.files = transfer.files;
    refreshAudioLabel();
  }

  function refreshNotesMeta() {
    var count = notes.value.length;
    notesMeta.textContent = count.toLocaleString() + " characters. Long notes are split so each request stays inside Groq’s free 8,000 tokens per minute.";
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function fillList(list, items, emptyText, builder) {
    list.replaceChildren();
    if (!items.length) {
      list.appendChild(el("li", "empty", emptyText));
      return;
    }
    items.forEach(builder);
  }

  function renderBriefing(summary) {
    briefing.replaceChildren();
    var summaryCopy = el("p", "summary-copy", summary.summary);
    briefing.append(summaryCopy);

    var pointsHeading = el("h3", null, "Key points");
    var points = el("ol", "points");
    fillList(points, summary.key_points, "No key points identified.", function (point, index) {
      var item = el("li");
      item.append(el("span", "index", String(index + 1).padStart(2, "0")), el("p", null, point));
      points.appendChild(item);
    });

    var decisionsHeading = el("h3", null, "Decisions");
    var decisions = el("ul", "decisions");
    fillList(decisions, summary.key_decisions, "No decisions identified.", function (decision) {
      decisions.appendChild(el("li", null, decision));
    });

    var actionsHeading = el("h3", null, "Action items");
    var actions = el("ol", "actions");
    fillList(actions, summary.action_items, "No action items identified.", function (item) {
      var row = el("li");
      var task = el("p", "task", item.task);
      var meta = el("p", "meta");
      var who = el("span", item.owner ? "who" : "who is-empty", item.owner || "Not named");
      var when = el("span", item.due_date ? "when" : "when is-empty", item.due_date || "No date stated");
      meta.append(who, when);
      row.append(task, meta);
      actions.appendChild(row);
    });

    briefing.append(pointsHeading, points, decisionsHeading, decisions, actionsHeading, actions);
  }

  function remember(result, source) {
    latest = result;
    resultModel.textContent = result.usedFallback
      ? "Model " + result.model + " · fallback from " + S.PRIMARY_MODEL
      : "Model " + result.model;
    renderBriefing(result.summary);
    sourceText.textContent = source;
    sourceDetails.hidden = false;
    results.hidden = false;
    results.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  }

  async function copyText(text, button, label) {
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch (error) {
      button.textContent = "Copy failed";
    }
    window.setTimeout(function () { button.textContent = label; }, 1600);
  }

  async function summarize() {
    if (running) return;
    setError("");
    var apiKey = keyInput.value.trim();
    if (!apiKey) {
      setStatus("");
      setError("Add your Groq API key in Settings. You can get a free key at console.groq.com. It stays in this browser.");
      keyInput.focus();
      return;
    }
    var file = selectedAudio();
    var audioError = S.audioProblem(file);
    if (audioError) {
      setStatus("");
      setError(audioError);
      return;
    }
    var noteText = notes.value.trim();
    if (!noteText && !file) {
      setStatus("");
      setError("Paste meeting notes or choose an audio file.");
      notes.focus();
      return;
    }

    var saved = writeStoredKey(apiKey);
    setKeyStatus(saved ? "Stored in this browser only." : "This browser blocked storage. The key stays in the field until you close the tab.");

    running = true;
    submitButton.disabled = true;
    submitButton.textContent = "Summarizing…";
    submitButton.setAttribute("aria-busy", "true");
    try {
      var audioText = "";
      if (file) audioText = await S.transcribeAudio(file, apiKey, { onStatus: setStatus });
      var source = S.combineSources(noteText, audioText);
      if (!source.trim()) throw S.GroqError("The transcript is empty.");
      var result = await S.summarizeTranscript(source, apiKey, { onStatus: setStatus });
      remember(result, source);
      setStatus("Briefing ready.");
    } catch (error) {
      setStatus("");
      setError(S.publicMessage(error, apiKey));
    } finally {
      running = false;
      submitButton.disabled = false;
      submitButton.textContent = "Summarize meeting";
      submitButton.removeAttribute("aria-busy");
    }
  }

  keyInput.value = readStoredKey();
  refreshKeyStatus();
  refreshNotesMeta();
  refreshAudioLabel();

  keyInput.addEventListener("input", function () {
    refreshKeyStatus();
    scheduleSave();
  });

  toggleKey.addEventListener("click", function () {
    var hidden = keyInput.type === "password";
    keyInput.type = hidden ? "text" : "password";
    toggleKey.textContent = hidden ? "Hide" : "Show";
    toggleKey.setAttribute("aria-pressed", hidden ? "true" : "false");
  });

  forgetKey.addEventListener("click", function () {
    keyInput.value = "";
    writeStoredKey("");
    refreshKeyStatus();
    keyInput.focus();
  });

  notes.addEventListener("input", refreshNotesMeta);
  notes.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      summarize();
    }
  });

  loadSample.addEventListener("click", function () {
    notes.value = SAMPLE_NOTES;
    refreshNotesMeta();
    notes.focus();
  });

  audioInput.addEventListener("change", refreshAudioLabel);
  clearAudio.addEventListener("click", clearChosenAudio);

  ["dragenter", "dragover"].forEach(function (name) {
    dropzone.addEventListener(name, function (event) {
      event.preventDefault();
      dropzone.classList.add("is-hot");
    });
  });
  ["dragleave", "drop"].forEach(function (name) {
    dropzone.addEventListener(name, function (event) {
      event.preventDefault();
      dropzone.classList.remove("is-hot");
    });
  });
  dropzone.addEventListener("drop", function (event) {
    var file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) assignAudio(file);
  });

  submitButton.addEventListener("click", summarize);
  copyMarkdown.addEventListener("click", function () {
    if (!latest) return;
    copyText(S.toMarkdown(latest.summary), copyMarkdown, "Copy Markdown");
  });
  copyJson.addEventListener("click", function () {
    if (!latest) return;
    copyText(JSON.stringify(latest.summary, null, 2) + "\n", copyJson, "Copy JSON");
  });
})();

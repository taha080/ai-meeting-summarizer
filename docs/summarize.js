/* Browser client for Groq's free API.
   The API key is attached only to the Authorization header of requests whose
   origin is https://api.groq.com. It is never written to a log. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.MeetingSummarizer = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  var PRIMARY_MODEL = "openai/gpt-oss-20b";
  var FALLBACK_MODEL = "openai/gpt-oss-120b";
  var TRANSCRIPTION_MODEL = "whisper-large-v3-turbo";
  var API_ORIGIN = "https://api.groq.com";
  var CHAT_URL = API_ORIGIN + "/openai/v1/chat/completions";
  var TRANSCRIBE_URL = API_ORIGIN + "/openai/v1/audio/transcriptions";
  var FREE_TIER_TPM = 8000;
  var CHARS_PER_TOKEN = 3;
  var MAX_COMPLETION_TOKENS = 3072;
  var DEFAULT_CHUNK_CHARS = 6000;
  var DEFAULT_CHUNK_OVERLAP = 400;
  var MAX_AUDIO_BYTES = 25 * 1024 * 1024;
  var SENTENCE_BREAK = /(?<=[.!?])\s+/;

  var EXTRACT_INSTRUCTIONS = [
    "You are an expert meeting analyst. Read the transcript excerpt and return a JSON object with exactly these keys:",
    '- "summary": a concise paragraph covering only this excerpt',
    '- "key_points": a list of short strings, the main points of this excerpt',
    '- "action_items": a list of objects with "task" (string), "owner" (string or null), and "due_date" (string or null)',
    '- "key_decisions": a list of strings',
    "",
    "Rules:",
    "- Include only points, tasks, owners, dates, and decisions that the excerpt explicitly supports.",
    "- Use null for owner or due_date when the excerpt does not state them.",
    "- Do not invent people, dates, or commitments.",
    "- Keep each key point, action item, and decision atomic.",
    "- Return JSON only.",
  ].join("\n");

  var MERGE_INSTRUCTIONS = [
    "You are an expert meeting analyst. You will receive JSON analyses of consecutive excerpts from the same meeting.",
    "Combine them into one JSON object with exactly these keys:",
    '- "summary": one concise paragraph describing the whole meeting',
    '- "key_points": a deduplicated list of short strings',
    '- "action_items": a deduplicated list of objects with "task", "owner", and "due_date"',
    '- "key_decisions": a deduplicated list of strings',
    "",
    "Rules:",
    "- Merge items that describe the same point, task, or decision. Keep the clearest wording and any owner or due date that was stated.",
    "- Do not add information that is not present in the partial analyses.",
    "- Use null when an owner or due date is unknown.",
    "- Return JSON only.",
  ].join("\n");

  function GroqError(message, options) {
    var settings = options || {};
    var error = new Error(message);
    error.name = "GroqError";
    error.status = settings.status || 0;
    error.retryable = Boolean(settings.retryable);
    error.retryAfterMs = settings.retryAfterMs == null ? null : settings.retryAfterMs;
    error.body = settings.body || "";
    return error;
  }

  function assertGroqUrl(url) {
    var parsed = new URL(url);
    if (parsed.origin !== API_ORIGIN) {
      throw GroqError("Requests are only sent to api.groq.com.");
    }
    return parsed.toString();
  }

  function redact(message, secret) {
    var text = String(message || "");
    if (secret && String(secret).length >= 4 && text.indexOf(secret) !== -1) {
      text = text.split(secret).join("••••");
    }
    return text;
  }

  function estimateTokens(text) {
    if (!text) return 0;
    return Math.max(1, Math.ceil(String(text).length / CHARS_PER_TOKEN));
  }

  function requestTokens(user) {
    return estimateTokens(user) + MAX_COMPLETION_TOKENS;
  }

  function safeRequestTokens() {
    return Math.min(FREE_TIER_TPM - 500, 6500);
  }

  function defaultSleep(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  function createTokenBudget(options) {
    var settings = options || {};
    var sleep = settings.sleep || defaultSleep;
    var now = settings.now || function () { return Date.now(); };
    var events = [];
    var windowMs = 60000;

    function prune(time) {
      var kept = [];
      for (var i = 0; i < events.length; i += 1) {
        if (time - events[i][0] < windowMs) kept.push(events[i]);
      }
      events = kept;
    }

    return {
      acquire: async function (tokens) {
        if (tokens <= 0) return;
        if (tokens > FREE_TIER_TPM) {
          throw GroqError(
            "This request is about " + tokens + " tokens, which is above Groq's free limit of " +
            FREE_TIER_TPM + " tokens per minute. Shorten the notes and try again."
          );
        }
        while (true) {
          var time = now();
          prune(time);
          var used = 0;
          for (var i = 0; i < events.length; i += 1) used += events[i][1];
          if (used + tokens <= FREE_TIER_TPM) {
            events.push([time, tokens]);
            return;
          }
          var oldest = events[0][0];
          for (var j = 1; j < events.length; j += 1) oldest = Math.min(oldest, events[j][0]);
          await sleep(Math.max(windowMs - (time - oldest), 10));
        }
      },
      recordActual: function (tokens) {
        if (tokens <= 0 || !events.length) return;
        events[events.length - 1][1] = tokens;
      },
    };
  }

  function join(parts) {
    return parts.join("\n\n");
  }

  function overlapTail(parts, overlap) {
    if (overlap <= 0 || !parts.length) return [];
    var kept = [];
    var total = 0;
    for (var i = parts.length - 1; i >= 0; i -= 1) {
      var part = parts[i];
      var separator = kept.length ? 2 : 0;
      var needed = part.length + separator;
      if (kept.length && total + needed > overlap) break;
      if (!kept.length && needed > overlap) return [part.slice(-overlap)];
      kept.push(part);
      total += needed;
    }
    kept.reverse();
    return kept;
  }

  function hardWrap(text, chunkSize) {
    var pieces = [];
    for (var index = 0; index < text.length; index += chunkSize) {
      pieces.push(text.slice(index, index + chunkSize));
    }
    return pieces;
  }

  function splitLongParagraph(paragraph, chunkSize) {
    var sentences = paragraph.split(SENTENCE_BREAK).map(function (sentence) {
      return sentence.trim();
    }).filter(Boolean);
    if (!sentences.length) sentences = [paragraph];
    var pieces = [];
    var buffer = "";
    sentences.forEach(function (sentence) {
      if (sentence.length > chunkSize) {
        if (buffer) {
          pieces.push(buffer);
          buffer = "";
        }
        hardWrap(sentence, chunkSize).forEach(function (piece) { pieces.push(piece); });
        return;
      }
      var candidate = buffer ? buffer + " " + sentence : sentence;
      if (candidate.length <= chunkSize) {
        buffer = candidate;
      } else {
        if (buffer) pieces.push(buffer);
        buffer = sentence;
      }
    });
    if (buffer) pieces.push(buffer);
    return pieces;
  }

  function piecesOf(text, chunkSize) {
    var paragraphs = text.split(/\n\s*\n/).map(function (paragraph) {
      return paragraph.trim();
    }).filter(Boolean);
    var pieces = [];
    paragraphs.forEach(function (paragraph) {
      if (paragraph.length <= chunkSize) pieces.push(paragraph);
      else splitLongParagraph(paragraph, chunkSize).forEach(function (piece) { pieces.push(piece); });
    });
    return pieces;
  }

  function chunkTranscript(text, chunkSize, overlap) {
    var size = chunkSize == null ? DEFAULT_CHUNK_CHARS : chunkSize;
    var tail = overlap == null ? DEFAULT_CHUNK_OVERLAP : overlap;
    if (size <= 0) throw new Error("chunk_size must be a positive integer");
    if (tail < 0 || tail >= size) throw new Error("overlap must be >= 0 and smaller than chunk_size");
    var cleaned = String(text || "").trim();
    if (!cleaned) return [];
    if (cleaned.length <= size) return [cleaned];
    var pieces = piecesOf(cleaned, size);
    var chunks = [];
    var current = [];
    pieces.forEach(function (piece) {
      if (current.length && join(current).length + 2 + piece.length > size) {
        chunks.push(join(current));
        current = overlapTail(current, tail);
        if (current.length && join(current).length + 2 + piece.length > size) current = [];
      }
      current.push(piece);
    });
    if (current.length) chunks.push(join(current));
    return chunks;
  }

  function blankToNull(value) {
    if (value == null) return null;
    var text = String(value).trim();
    if (!text || /^(null|none|n\/a|unknown)$/i.test(text)) return null;
    return text;
  }

  function cleanLine(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function decisionText(value) {
    if (typeof value === "string") return cleanLine(value);
    if (value && typeof value === "object") {
      var keys = ["decision", "text", "summary"];
      for (var i = 0; i < keys.length; i += 1) {
        var candidate = value[keys[i]];
        if (typeof candidate === "string" && candidate.trim()) return cleanLine(candidate);
      }
    }
    if (typeof value === "number") return String(value);
    return "";
  }

  function actionItem(value) {
    var record = value;
    if (typeof value === "string") record = { task: value };
    if (!record || typeof record !== "object") return null;
    var task = record.task || record.action || record.item || "";
    if (typeof task !== "string" || !task.trim()) return null;
    return {
      task: cleanLine(task),
      owner: blankToNull(record.owner != null ? record.owner : (record.assignee != null ? record.assignee : record.who)),
      due_date: blankToNull(record.due_date != null ? record.due_date : (record.due != null ? record.due : record.deadline)),
    };
  }

  function asList(value) {
    if (Array.isArray(value)) return value;
    if (typeof value === "string" && value.trim()) return [value];
    return [];
  }

  function normalizeSummary(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw GroqError("The model response was not a JSON object.");
    }
    var summary = cleanLine(payload.summary);
    if (!summary) throw GroqError("The model response did not include a summary.");
    var pointSource = payload.key_points || payload.keyPoints || payload.points || [];
    var decisionSource = payload.key_decisions || payload.keyDecisions || payload.decisions || [];
    var actionSource = payload.action_items || payload.actionItems || payload.actions || [];
    if (!Array.isArray(actionSource)) throw GroqError("action_items must be a list.");
    if (!Array.isArray(decisionSource) && typeof decisionSource !== "string") {
      throw GroqError("key_decisions must be a list.");
    }
    return {
      summary: summary,
      key_points: asList(pointSource).map(decisionText).filter(Boolean),
      key_decisions: asList(decisionSource).map(decisionText).filter(Boolean),
      action_items: asList(actionSource).map(actionItem).filter(Boolean),
    };
  }

  function extractJsonObject(content) {
    var text = String(content || "").trim();
    text = text.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
    var start = text.indexOf("{");
    var end = text.lastIndexOf("}");
    if (start === -1 || end <= start) throw GroqError("The model did not return valid JSON.");
    try {
      var payload = JSON.parse(text.slice(start, end + 1));
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
        throw new Error("not an object");
      }
      return payload;
    } catch (error) {
      if (error && error.name === "GroqError") throw error;
      throw GroqError("The model did not return valid JSON.");
    }
  }

  function messageText(payload) {
    var choice = payload && payload.choices && payload.choices[0];
    var message = choice && choice.message;
    if (!message) return "";
    if (typeof message.content === "string" && message.content.trim()) return message.content;
    if (typeof message.reasoning === "string") return message.reasoning;
    return "";
  }

  function combineSources(notes, audioText) {
    var note = String(notes || "").trim();
    var audio = String(audioText || "").trim();
    if (note && audio) return note + "\n\nAudio transcript:\n\n" + audio;
    return note || audio;
  }

  function extractUser(excerpt, index, total) {
    return EXTRACT_INSTRUCTIONS + "\n\nTranscript excerpt " + index + " of " + total + ":\n\n" + excerpt;
  }

  function mergeUser(partials) {
    var blocks = partials.map(function (partial, index) {
      return "Part " + (index + 1) + ":\n" + JSON.stringify(partial, null, 2);
    });
    return MERGE_INSTRUCTIONS + "\n\nPartial analyses of one meeting:\n\n" + blocks.join("\n\n");
  }

  function mergeBatches(partials) {
    var batches = [];
    var current = [];
    partials.forEach(function (partial) {
      var trial = current.concat([partial]);
      if (current.length && requestTokens(mergeUser(trial)) > safeRequestTokens()) {
        batches.push(current);
        current = [partial];
      } else {
        current = trial;
      }
    });
    if (current.length) batches.push(current);
    return batches;
  }

  function toMarkdown(summary) {
    var lines = ["# Meeting Summary", "", summary.summary, "", "## Key points", ""];
    if (!summary.key_points.length) lines.push("_None identified._");
    else summary.key_points.forEach(function (point) { lines.push("- " + point); });
    lines.push("", "## Decisions", "");
    if (!summary.key_decisions.length) lines.push("_None identified._");
    else summary.key_decisions.forEach(function (decision) { lines.push("- " + decision); });
    lines.push("", "## Action items", "");
    if (!summary.action_items.length) lines.push("_None identified._");
    else {
      summary.action_items.forEach(function (item) {
        var meta = [];
        if (item.owner) meta.push("Owner: " + item.owner);
        if (item.due_date) meta.push("Due: " + item.due_date);
        lines.push("- **" + item.task + "**" + (meta.length ? " — " + meta.join("; ") : ""));
      });
    }
    lines.push("");
    return lines.join("\n");
  }

  function retryAfterMs(response) {
    if (!response || !response.headers || !response.headers.get) return null;
    var raw = response.headers.get("retry-after");
    if (!raw) return null;
    var seconds = Number(raw);
    if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
    var dated = Date.parse(raw);
    if (Number.isFinite(dated)) return Math.max(0, dated - Date.now());
    return null;
  }

  function errorMessage(payload, status) {
    if (payload && payload.error && payload.error.message) return String(payload.error.message);
    return "Groq returned " + status + ".";
  }

  async function groqFetch(url, options) {
    var target = assertGroqUrl(url);
    try {
      return await fetch(target, options);
    } catch (error) {
      throw GroqError("Could not reach api.groq.com. Check the connection and try again.", {
        retryable: true,
      });
    }
  }

  async function readResponse(response) {
    var text = await response.text();
    var payload = null;
    if (text) {
      try { payload = JSON.parse(text); } catch (error) { payload = null; }
    }
    if (!response.ok) {
      throw GroqError(errorMessage(payload, response.status), {
        status: response.status,
        retryable: response.status === 429 || response.status >= 500,
        retryAfterMs: retryAfterMs(response),
        body: text,
      });
    }
    return payload || {};
  }

  async function withBackoff(fn, options) {
    var settings = options || {};
    var attempts = settings.attempts || 5;
    var sleep = settings.sleep || defaultSleep;
    var delay = 1000;
    for (var attempt = 0; attempt < attempts; attempt += 1) {
      try {
        return await fn();
      } catch (error) {
        if (!error || !error.retryable || attempt === attempts - 1) throw error;
        var wait = error.retryAfterMs == null ? delay : error.retryAfterMs;
        delay = Math.min(delay * 2, 32000);
        await sleep(wait);
      }
    }
    throw GroqError("The Groq request failed.");
  }

  function buildChatBody(model, user, jsonMode) {
    var body = {
      model: model,
      temperature: 0.6,
      reasoning_effort: "low",
      reasoning_format: "hidden",
      max_completion_tokens: MAX_COMPLETION_TOKENS,
      messages: [{ role: "user", content: user }],
    };
    if (jsonMode) body.response_format = { type: "json_object" };
    return body;
  }

  function buildChatRequest(apiKey, model, user, jsonMode) {
    return {
      url: assertGroqUrl(CHAT_URL),
      headers: {
        Authorization: "Bearer " + apiKey,
        "Content-Type": "application/json",
      },
      body: buildChatBody(model, user, jsonMode),
    };
  }

  function buildTranscriptionRequest(apiKey) {
    return {
      url: assertGroqUrl(TRANSCRIBE_URL),
      model: TRANSCRIPTION_MODEL,
      headers: { Authorization: "Bearer " + apiKey },
    };
  }

  function rejectsJsonMode(error) {
    var status = error && error.status;
    var text = String((error && error.message) || "") + " " + String((error && error.body) || "");
    return (status === 400 || status === 422) && text.toLowerCase().indexOf("response_format") !== -1;
  }

  function shouldTryFallback(error) {
    var status = error && error.status;
    return status !== 401 && status !== 403;
  }

  function publicMessage(error, apiKey) {
    var status = error && error.status;
    if (status === 401 || status === 403) {
      return "Groq rejected that API key. Check Settings, or get a free key at console.groq.com.";
    }
    if (status === 429) {
      return "Groq's free rate limit was reached. Wait a minute and try again.";
    }
    return redact((error && error.message) || "Summarization failed.", apiKey);
  }

  async function postChat(apiKey, model, user, jsonMode) {
    var request = buildChatRequest(apiKey, model, user, jsonMode);
    var response = await groqFetch(request.url, {
      method: "POST",
      headers: request.headers,
      body: JSON.stringify(request.body),
    });
    return readResponse(response);
  }

  async function postAndParse(model, user, apiKey, budget, jsonMode) {
    await budget.acquire(requestTokens(user));
    var payload = await postChat(apiKey, model, user, jsonMode);
    var total = payload.usage && payload.usage.total_tokens;
    if (typeof total === "number") budget.recordActual(total);
    var finish = payload.choices && payload.choices[0] && payload.choices[0].finish_reason;
    if (finish === "length") {
      throw GroqError("The model ran out of tokens before finishing the briefing.");
    }
    return normalizeSummary(extractJsonObject(messageText(payload)));
  }

  async function completeOnce(model, user, apiKey, budget, sleep) {
    try {
      return await withBackoff(function () {
        return postAndParse(model, user, apiKey, budget, true);
      }, { sleep: sleep });
    } catch (error) {
      if (!rejectsJsonMode(error)) throw error;
      return withBackoff(function () {
        return postAndParse(model, user, apiKey, budget, false);
      }, { sleep: sleep });
    }
  }

  async function completeJson(options) {
    var models = [PRIMARY_MODEL, FALLBACK_MODEL];
    var lastError = null;
    for (var index = 0; index < models.length; index += 1) {
      var model = models[index];
      if (index > 0 && options.onStatus) {
        options.onStatus("The first model failed. Trying " + model + "…");
      }
      try {
        var summary = await completeOnce(model, options.user, options.apiKey, options.budget, options.sleep);
        return { summary: summary, model: model };
      } catch (error) {
        lastError = error;
        if (!shouldTryFallback(error) || index === models.length - 1) throw error;
      }
    }
    throw lastError || GroqError("Summarization failed.");
  }

  async function mergePartials(partials, options) {
    var current = partials.slice();
    var usedFallback = false;
    var model = PRIMARY_MODEL;
    while (current.length > 1) {
      var batches = mergeBatches(current);
      if (batches.length === 1) {
        var single = await completeJson({
          apiKey: options.apiKey,
          user: mergeUser(batches[0]),
          budget: options.budget,
          sleep: options.sleep,
          onStatus: options.onStatus,
        });
        return {
          summary: single.summary,
          model: single.model,
          usedFallback: usedFallback || single.model !== PRIMARY_MODEL,
        };
      }
      var merged = [];
      for (var i = 0; i < batches.length; i += 1) {
        var batch = batches[i];
        if (batch.length === 1) {
          merged.push(batch[0]);
          continue;
        }
        var result = await completeJson({
          apiKey: options.apiKey,
          user: mergeUser(batch),
          budget: options.budget,
          sleep: options.sleep,
          onStatus: options.onStatus,
        });
        if (result.model !== PRIMARY_MODEL) usedFallback = true;
        model = result.model;
        merged.push(result.summary);
      }
      if (merged.length >= current.length) {
        throw GroqError(
          "Partial summaries are too large to merge within the " + FREE_TIER_TPM + " tokens-per-minute budget."
        );
      }
      current = merged;
    }
    return { summary: current[0], model: model, usedFallback: usedFallback };
  }

  async function summarizeTranscript(transcript, apiKey, options) {
    var settings = options || {};
    var chunks = chunkTranscript(transcript);
    if (!chunks.length) throw GroqError("The transcript is empty.");
    var budget = settings.budget || createTokenBudget({ sleep: settings.sleep });
    var partials = [];
    var usedFallback = false;
    var model = PRIMARY_MODEL;
    for (var index = 0; index < chunks.length; index += 1) {
      if (settings.onStatus) {
        settings.onStatus("Summarizing part " + (index + 1) + " of " + chunks.length + "…");
      }
      var result = await completeJson({
        apiKey: apiKey,
        user: extractUser(chunks[index], index + 1, chunks.length),
        budget: budget,
        sleep: settings.sleep,
        onStatus: settings.onStatus,
      });
      if (result.model !== PRIMARY_MODEL) usedFallback = true;
      model = result.model;
      partials.push(result.summary);
    }
    if (partials.length === 1) {
      return { summary: partials[0], model: model, usedFallback: usedFallback };
    }
    if (settings.onStatus) settings.onStatus("Merging the briefing…");
    var merged = await mergePartials(partials, {
      apiKey: apiKey,
      budget: budget,
      sleep: settings.sleep,
      onStatus: settings.onStatus,
    });
    return {
      summary: merged.summary,
      model: merged.model,
      usedFallback: usedFallback || merged.usedFallback,
    };
  }

  function audioProblem(file) {
    if (!file) return "";
    var name = String(file.name || "").toLowerCase();
    var allowed = [".mp3", ".wav", ".m4a"];
    var ok = allowed.some(function (suffix) { return name.endsWith(suffix); });
    if (!ok) return "Upload an MP3, WAV, or M4A recording.";
    if (!file.size) return "That audio file is empty.";
    if (file.size > MAX_AUDIO_BYTES) {
      return "Groq's free tier accepts 25 MB of audio per request. Trim the recording, or use the local app, which splits larger files.";
    }
    return "";
  }

  async function transcribeAudio(file, apiKey, options) {
    var settings = options || {};
    var problem = audioProblem(file);
    if (problem) throw GroqError(problem);
    if (settings.onStatus) settings.onStatus("Transcribing audio with whisper-large-v3-turbo…");
    var form = new FormData();
    form.append("file", file, file.name);
    form.append("model", TRANSCRIPTION_MODEL);
    form.append("response_format", "json");
    form.append("temperature", "0");
    var request = buildTranscriptionRequest(apiKey);
    var payload = await withBackoff(async function () {
      var response = await groqFetch(request.url, {
        method: "POST",
        headers: request.headers,
        body: form,
      });
      return readResponse(response);
    }, { sleep: settings.sleep });
    var text = String((payload && payload.text) || "").trim();
    if (!text) throw GroqError("No speech was detected in that recording.");
    return text;
  }

  return {
    PRIMARY_MODEL: PRIMARY_MODEL,
    FALLBACK_MODEL: FALLBACK_MODEL,
    TRANSCRIPTION_MODEL: TRANSCRIPTION_MODEL,
    API_ORIGIN: API_ORIGIN,
    CHAT_URL: CHAT_URL,
    TRANSCRIBE_URL: TRANSCRIBE_URL,
    MAX_AUDIO_BYTES: MAX_AUDIO_BYTES,
    GroqError: GroqError,
    assertGroqUrl: assertGroqUrl,
    redact: redact,
    estimateTokens: estimateTokens,
    chunkTranscript: chunkTranscript,
    normalizeSummary: normalizeSummary,
    extractJsonObject: extractJsonObject,
    combineSources: combineSources,
    toMarkdown: toMarkdown,
    buildChatRequest: buildChatRequest,
    buildTranscriptionRequest: buildTranscriptionRequest,
    shouldTryFallback: shouldTryFallback,
    publicMessage: publicMessage,
    createTokenBudget: createTokenBudget,
    summarizeTranscript: summarizeTranscript,
    transcribeAudio: transcribeAudio,
    audioProblem: audioProblem,
  };
});

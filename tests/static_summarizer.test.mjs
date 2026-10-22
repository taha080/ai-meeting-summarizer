import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const S = require("../docs/summarize.js");

test("defaults are the current free Groq models", () => {
  assert.equal(S.PRIMARY_MODEL, "openai/gpt-oss-20b");
  assert.equal(S.FALLBACK_MODEL, "openai/gpt-oss-120b");
  assert.equal(S.TRANSCRIPTION_MODEL, "whisper-large-v3-turbo");
  assert.equal(S.API_ORIGIN, "https://api.groq.com");
});

test("requests stay on api.groq.com and the key stays in the header", () => {
  const secret = "gsk_test_secret_value";
  const chat = S.buildChatRequest(secret, S.PRIMARY_MODEL, "Transcript excerpt 1 of 1:\n\nShip it.", true);
  assert.equal(chat.url, "https://api.groq.com/openai/v1/chat/completions");
  assert.equal(chat.headers.Authorization, `Bearer ${secret}`);
  assert.equal(chat.body.model, "openai/gpt-oss-20b");
  assert.equal(chat.body.reasoning_effort, "low");
  assert.equal(chat.body.reasoning_format, "hidden");
  assert.equal(JSON.stringify(chat.body).includes(secret), false);
  assert.equal(chat.body.messages[0].role, "user");

  const audio = S.buildTranscriptionRequest(secret);
  assert.equal(audio.url, "https://api.groq.com/openai/v1/audio/transcriptions");
  assert.equal(audio.model, "whisper-large-v3-turbo");
  assert.equal(audio.headers.Authorization, `Bearer ${secret}`);

  assert.throws(() => S.assertGroqUrl("https://example.com/v1/chat/completions"), /api\.groq\.com/);
});

test("chunking keeps short text whole and overlaps longer text", () => {
  assert.deepEqual(S.chunkTranscript("  Standup notes.  "), ["Standup notes."]);
  assert.deepEqual(S.chunkTranscript("   "), []);
  const paragraphs = Array.from({ length: 8 }, (_, index) => `Paragraph ${index} says ${index} clearly.`);
  const text = paragraphs.join("\n\n");
  const disjoint = S.chunkTranscript(text, 80, 0);
  assert.ok(disjoint.length > 1);
  assert.ok(disjoint.every((chunk) => chunk.length <= 80));
  const overlapped = S.chunkTranscript(text, 80, 40);
  assert.ok(overlapped.length > 1);
  for (let index = 0; index < overlapped.length - 1; index += 1) {
    const shared = paragraphs.filter((paragraph) => overlapped[index].includes(paragraph) && overlapped[index + 1].includes(paragraph));
    assert.ok(shared.length > 0);
  }
});

test("normalization keeps owners and drops blanks", () => {
  const summary = S.normalizeSummary({
    summary: "  The team will ship. ",
    key_points: [" Ship Monday ", { decision: "Copy is blocking" }],
    action_items: [
      { task: " Email notes ", owner: "Ada", due: "Friday" },
      { action: "File the bug", assignee: "unknown", deadline: "n/a" },
    ],
    key_decisions: ["Ship Monday", ""],
  });
  assert.equal(summary.summary, "The team will ship.");
  assert.deepEqual(summary.key_points, ["Ship Monday", "Copy is blocking"]);
  assert.equal(summary.action_items[0].owner, "Ada");
  assert.equal(summary.action_items[0].due_date, "Friday");
  assert.equal(summary.action_items[1].owner, null);
  assert.equal(summary.action_items[1].due_date, null);
  assert.deepEqual(summary.key_decisions, ["Ship Monday"]);
  assert.match(S.toMarkdown(summary), /Owner: Ada/);
});

test("secrets are redacted and auth failures do not fall back", () => {
  assert.equal(S.redact("rejected gsk_test_secret_value", "gsk_test_secret_value"), "rejected ••••");
  assert.equal(S.shouldTryFallback({ status: 401 }), false);
  assert.equal(S.shouldTryFallback({ status: 429 }), true);
  assert.match(S.publicMessage({ status: 401, message: "bad gsk_test_secret_value" }, "gsk_test_secret_value"), /free key/);
  assert.equal(S.publicMessage({ status: 401, message: "bad gsk_test_secret_value" }, "gsk_test_secret_value").includes("gsk_test_secret_value"), false);
});

test("notes and audio transcripts are combined without adding a key", () => {
  assert.equal(S.combineSources("Notes", "Spoken"), "Notes\n\nAudio transcript:\n\nSpoken");
  assert.equal(S.combineSources("Notes", ""), "Notes");
  assert.equal(S.combineSources("", "Spoken"), "Spoken");
});

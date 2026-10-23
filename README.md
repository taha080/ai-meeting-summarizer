# Meeting Summarizer

Free meeting summaries from notes, a transcript, or a recording: key points, decisions, and action items with owners.

**Live site:** [https://taha080.github.io/ai-meeting-summarizer/](https://taha080.github.io/ai-meeting-summarizer/)

The public site is static GitHub Pages. There is no paid host, and the repository does not contain an API key. You bring a free [Groq](https://console.groq.com/keys) key. Groq’s free tier does not ask for a credit card.

## How it works

### In the browser

The live page is the `docs/` directory. Paste your Groq key into Settings. The page stores that key only in `localStorage` on your device, sends it only to `https://api.groq.com`, and never writes it to a log. Notes and audio are not stored on GitHub Pages. The page’s scripts call `https://api.groq.com` and no other API.

A recording is transcribed in the browser with Groq `whisper-large-v3-turbo`. The transcript goes to `openai/gpt-oss-20b`. If that model keeps failing, the same request is tried with `openai/gpt-oss-120b`. Groq’s older Llama models are no longer on the free plan. OpenAI and Gemini are not used by the public page.

The browser returns JSON with `summary`, `key_points`, `key_decisions`, and `action_items`. Each action item has `task`, `owner`, and `due_date`. Owners and due dates are included only when the meeting states them.

Because a GitHub Pages project site shares an origin with that account’s other Pages, other pages on `taha080.github.io` can read `localStorage`.

### On your machine

The Typer command-line tool and the local FastAPI app use the same models. They read `GROQ_API_KEY` from `.env` (gitignored) and call Groq’s OpenAI-compatible endpoint (`https://api.groq.com/openai/v1`) through the `openai` Python package. That package is only an HTTP client here. The public site does not read `.env`.

The local CLI and FastAPI app keep their existing shape: `summary`, `action_items`, and `key_decisions`. They do not return `key_points`.

Groq’s free tier for `openai/gpt-oss-20b` and `openai/gpt-oss-120b` is 8,000 tokens per minute on each model. Transcripts are split into chunks of 6,000 characters (about 2,000 tokens), with a short overlap, so one request fits in that budget. Partial JSON analyses are then merged. A later chunk waits when sending it would pass 8,000 tokens in the current minute. A 429 response is retried with exponential backoff, honoring `Retry-After`.

Groq accepts 25 MB of audio per request. The local app splits larger files before upload: WAV with the standard library, and MP3 and M4A with ffmpeg. The static site asks you to trim recordings over 25 MB, or to use the local app, which splits them.

OpenAI is optional and paid (`PROVIDER=openai`). Gemini’s free tier is optional too (`PROVIDER=gemini`, chat model `gemini-2.5-flash`). Gemini inline audio is kept under 15 MB per request. CLI flags and the local web form override these defaults. Audio and transcripts are sent to the selected provider and are not stored after the response.

## Features

- Live at [taha080.github.io/ai-meeting-summarizer](https://taha080.github.io/ai-meeting-summarizer/). The visitor’s key stays in `localStorage` and is sent only to `api.groq.com`
- Free by default. Groq key from [console.groq.com/keys](https://console.groq.com/keys), no card
- Accepts `.mp3`, `.wav`, `.m4a`, and `.txt`
- Transcription with `whisper-large-v3-turbo` (or `whisper-large-v3` in the local app)
- Summaries from `openai/gpt-oss-20b`, falling back to `openai/gpt-oss-120b`
- JSON result: a short summary, action items (`task`, `owner`, `due_date`), and key decisions
- Owners and due dates are included only when the meeting states them
- Chunks sized for the 8,000 token-per-minute free budget, with 429 backoff
- Audio over 25 MB is split before upload
- Optional `PROVIDER=openai` or `PROVIDER=gemini`
- Typer CLI and a single-page FastAPI upload UI

## Setup

Python 3.10 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`pip install -r requirements.txt` installs the runtime packages only. The `dev` extra adds pytest and httpx.

### Groq key

1. Open [console.groq.com/keys](https://console.groq.com/keys).
2. Sign in. Groq’s free tier does not ask for a credit card.
3. Create an API key and copy it.

```bash
cp .env.example .env
```

```dotenv
PROVIDER=groq
GROQ_API_KEY=gsk_...
GROQ_MODEL=openai/gpt-oss-20b
GROQ_FALLBACK_MODEL=openai/gpt-oss-120b
GROQ_TRANSCRIPTION_MODEL=whisper-large-v3-turbo
```

`.env` is gitignored. The key is read from `GROQ_API_KEY`. `gsk_...` above is a placeholder, not a real key.

| Variable | Required | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | Yes, for the default | Free Groq key |
| `PROVIDER` | No | `groq` (default), `openai`, or `gemini` |
| `GROQ_MODEL` | No | Chat model. Default `openai/gpt-oss-20b` |
| `GROQ_FALLBACK_MODEL` | No | Used when the primary model keeps failing. Default `openai/gpt-oss-120b`. Llama models are no longer on the free plan. Leave it empty to disable fallback |
| `GROQ_TRANSCRIPTION_MODEL` | No | `whisper-large-v3-turbo` or `whisper-large-v3` |
| `OPENAI_API_KEY` | Only if `PROVIDER=openai` | Paid OpenAI API. Not used by default |
| `GEMINI_API_KEY` | Only if `PROVIDER=gemini` | Free key from [Google AI Studio](https://aistudio.google.com/apikey). Chat model `gemini-2.5-flash` |

`ffmpeg` is needed only to split MP3 or M4A files larger than 25 MB. WAV files split without it.

## Usage

### Live site

[https://taha080.github.io/ai-meeting-summarizer/](https://taha080.github.io/ai-meeting-summarizer/)

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) deploys `docs/` from `main` with GitHub Actions. The workflow has no secrets. GitHub Pages is free. In the repository settings, set Pages to **GitHub Actions** if this is the first deploy.

1. Choose **get a free key** and create one at [console.groq.com/keys](https://console.groq.com/keys). No credit card.
2. Paste the key into Settings. It is saved only in `localStorage` for this browser.
3. Paste meeting notes, or upload an MP3, WAV, or M4A up to 25 MB.
4. Read the briefing: key points, decisions, and action items with owners.

### Command line

The `summarize` command takes an audio file or a `.txt` transcript. Text files are read locally. `--transcription-model` is used for audio.

```bash
summarize meeting.mp3 --format markdown --out summary.md
summarize meeting.wav --format json --out summary.json
summarize examples/sample_transcript.txt --format markdown
summarize notes.txt --provider gemini --model gemini-2.5-flash
```

`--format` is `markdown` or `json`. The result is written to standard output and, with `--out`, to that file as well. `--provider` is `groq`, `openai`, or `gemini`.

```text
summarize FILE [--format markdown|json] [--out PATH]
               [--provider groq|openai|gemini]
               [--model TEXT] [--transcription-model TEXT]
               [--chunk-size INTEGER] [--verbose]
```

`--chunk-size` is the maximum number of characters sent in one summarization request (default 6000, about 2,000 tokens). A longer transcript is split, with a short overlap, and the partial JSON analyses are merged. On Groq, a chunk waits when sending it would pass 8,000 tokens in the current minute. `--verbose` prints the chunk count to stderr.

You can also run `python -m meeting_summarizer examples/sample_transcript.txt --format json`.

### Local web UI

```bash
meeting-web
```

Or, with auto-reload during development:

```bash
uvicorn meeting_summarizer.web.app:app --reload
```

Open http://127.0.0.1:8000, choose an MP3, WAV, M4A, or TXT file, and submit. The page shows the summary, an action-item table, the key decisions, and the raw JSON. The form defaults to Groq, `openai/gpt-oss-20b` (fallback `openai/gpt-oss-120b`), and `whisper-large-v3-turbo`. This local server reads `GROQ_API_KEY` from `.env`.

`GET /health` returns `{"status": "ok"}`. `POST /api/summarize` accepts a multipart form with `file`, and optional `provider`, `model`, and `transcription_model` fields. The response body matches the CLI JSON.

## Sample output

This is illustrative output for [`examples/sample_transcript.txt`](examples/sample_transcript.txt), a product-launch sync. A live run uses your model and can differ in wording.

```json
{
  "summary": "The team locked a staged rollout of the mobile checklist for Monday, October 26, after build 1.4.2 includes the sync fix and updated copy. Full rollout is targeted for October 30 if crash-free sessions stay above 99.5 percent and support volume holds. Android tablet support is deferred to next quarter.",
  "action_items": [
    {
      "task": "Deliver revised empty-state copy and offline banner specs",
      "owner": "Maya Chen",
      "due_date": "Wednesday, October 21"
    },
    {
      "task": "Patch the sync worker, open the release branch, and add the 1.3 migration check",
      "owner": "Luis Ortega",
      "due_date": "Thursday, October 22"
    },
    {
      "task": "Draft the help-center article and send it to Maya for review",
      "owner": "Jonah Brooks",
      "due_date": "Thursday, October 22"
    },
    {
      "task": "Send the customer email about the staged rollout and the tablet deferral",
      "owner": "Jonah Brooks",
      "due_date": "Wednesday, October 21"
    },
    {
      "task": "Note the Android tablet deferral in the release notes",
      "owner": "Luis Ortega",
      "due_date": null
    },
    {
      "task": "Publish the go/no-go checklist",
      "owner": "Priya Shah",
      "due_date": "October 20"
    }
  ],
  "key_decisions": [
    "Staged rollout starts Monday, October 26, only after build 1.4.2 includes the sync fix and copy update.",
    "Full rollout is targeted for Friday, October 30, and proceeds only if crash-free sessions stay above 99.5 percent and support volume does not spike.",
    "Android tablet support is deferred to next quarter."
  ]
}
```

The same result as Markdown, in the shape the CLI writes:

```markdown
# Meeting Summary

The team locked a staged rollout of the mobile checklist for Monday, October 26, after build 1.4.2 includes the sync fix and updated copy. Full rollout is targeted for October 30 if crash-free sessions stay above 99.5 percent and support volume holds. Android tablet support is deferred to next quarter.

## Action Items

- **Deliver revised empty-state copy and offline banner specs** — Owner: Maya Chen; Due: Wednesday, October 21
- **Patch the sync worker, open the release branch, and add the 1.3 migration check** — Owner: Luis Ortega; Due: Thursday, October 22
- **Draft the help-center article and send it to Maya for review** — Owner: Jonah Brooks; Due: Thursday, October 22
- **Send the customer email about the staged rollout and the tablet deferral** — Owner: Jonah Brooks; Due: Wednesday, October 21
- **Note the Android tablet deferral in the release notes** — Owner: Luis Ortega
- **Publish the go/no-go checklist** — Owner: Priya Shah; Due: October 20

## Key Decisions

- Staged rollout starts Monday, October 26, only after build 1.4.2 includes the sync fix and copy update.
- Full rollout is targeted for Friday, October 30, and proceeds only if crash-free sessions stay above 99.5 percent and support volume does not spike.
- Android tablet support is deferred to next quarter.
```

## Project structure

```text
.
├── .github/workflows/pages.yml  # Deploys docs/ to GitHub Pages
├── .env.example
├── docs/                        # Static site (GitHub Pages)
│   ├── index.html
│   ├── app.js
│   ├── summarize.js
│   └── styles.css
├── examples/
│   └── sample_transcript.txt
├── src/meeting_summarizer/
│   ├── audio_split.py      # Split audio above the 25 MB Groq cap
│   ├── budget.py           # 8k tokens-per-minute window
│   ├── cli.py              # Typer command
│   ├── config.py           # .env, Groq defaults, optional providers
│   ├── exceptions.py
│   ├── gemini.py           # Optional free-tier transcription
│   ├── models.py           # Summary, action item, Markdown/JSON rendering
│   ├── retry.py            # 429 backoff
│   ├── summarize.py        # Chunking, summarization, and merge
│   ├── transcribe.py       # Text loading and Whisper transcription
│   └── web/
│       ├── app.py          # FastAPI routes
│       └── templates/
│           └── index.html  # Upload page
├── tests/                  # pytest, with provider calls mocked
├── pyproject.toml
├── requirements.txt
└── LICENSE
```

## Tests

```bash
pytest
```

The tests stub Groq, OpenAI, and Gemini. They check transcription requests, chunk-and-merge chat calls, 429 backoff, the token budget, audio splitting, CLI output, the upload API, and the static Pages client. The Pages check runs `tests/static_summarizer.test.mjs` with Node’s test runner. Nothing in the suite calls a paid API.

## License

MIT. See [LICENSE](LICENSE).

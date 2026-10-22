# Meeting Summarizer

Free meeting summaries. A recording or a plain-text transcript becomes a short summary, action items, and the decisions that were made. The default provider is [Groq's free API](https://console.groq.com/keys). No credit card.

Audio (MP3, WAV, or M4A) is transcribed with Groq `whisper-large-v3-turbo`. The transcript goes to `openai/gpt-oss-120b`. If that model keeps failing, the same request is tried with `llama-3.3-70b-versatile`. Both calls use Groq's OpenAI-compatible endpoint (`https://api.groq.com/openai/v1`) through the `openai` Python package, which is only an HTTP client here.

Groq's free tier for `openai/gpt-oss-120b` is 8,000 tokens per minute. Transcripts are split into chunks of about 6,000 characters so one request fits in that budget, and later chunks wait for the next minute. A 429 response is retried with exponential backoff, honoring `Retry-After`. Groq accepts 25 MB of audio per request; larger files are split (WAV with the standard library, MP3 and M4A with ffmpeg).

OpenAI is optional and paid (`PROVIDER=openai`). Gemini's free tier is optional too (`PROVIDER=gemini`, model `gemini-2.5-flash`).

## Features

- Free by default. Groq key from [console.groq.com/keys](https://console.groq.com/keys), no card
- Accepts `.mp3`, `.wav`, `.m4a`, and `.txt`
- Transcription with `whisper-large-v3-turbo` (or `whisper-large-v3`)
- Summaries from `openai/gpt-oss-120b`, falling back to `llama-3.3-70b-versatile`
- JSON result: a short summary, action items (`task`, `owner`, `due_date`), and key decisions
- Owners and due dates are included only when the meeting states them
- Chunks sized for the 8k token-per-minute free budget, with 429 backoff
- Audio over 25 MB is split before upload
- Optional `PROVIDER=openai` or `PROVIDER=gemini`
- Typer CLI and a single-page FastAPI upload UI

## Installation

Python 3.10 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`pip install -r requirements.txt` installs the runtime packages only. The `dev` extra adds pytest and httpx.

## Get a free Groq key

1. Open [console.groq.com/keys](https://console.groq.com/keys).
2. Sign in. Groq's free tier does not ask for a credit card.
3. Create an API key and copy it.

```bash
cp .env.example .env
```

```dotenv
PROVIDER=groq
GROQ_API_KEY=gsk_...
GROQ_MODEL=openai/gpt-oss-120b
GROQ_FALLBACK_MODEL=llama-3.3-70b-versatile
GROQ_TRANSCRIPTION_MODEL=whisper-large-v3-turbo
```

`.env` is gitignored. The key is read from `GROQ_API_KEY`.

| Variable | Required | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | Yes, for the default | Free Groq key |
| `PROVIDER` | No | `groq` (default), `openai`, or `gemini` |
| `GROQ_MODEL` | No | Chat model. Default `openai/gpt-oss-120b` |
| `GROQ_FALLBACK_MODEL` | No | Used when the primary model keeps failing. Default `llama-3.3-70b-versatile`. Set `openai/gpt-oss-20b` if that Llama model is no longer on the free plan, or leave it empty to disable fallback |
| `GROQ_TRANSCRIPTION_MODEL` | No | `whisper-large-v3-turbo` or `whisper-large-v3` |
| `OPENAI_API_KEY` | Only if `PROVIDER=openai` | Paid OpenAI API. Not used by default |
| `GEMINI_API_KEY` | Only if `PROVIDER=gemini` | Free key from [Google AI Studio](https://aistudio.google.com/apikey). Chat model `gemini-2.5-flash` |

CLI flags and the web form override these defaults. Audio and transcripts are sent to the selected provider and are not stored after the response. Requests stay inside Groq's free limits: 8,000 tokens per minute for `openai/gpt-oss-120b`, and 25 MB per transcription upload.

`ffmpeg` is needed only to split MP3 or M4A files that are larger than 25 MB. WAV files split without it. Gemini inline audio is kept under 15 MB per request.

## CLI

The `summarize` command takes an audio file or a `.txt` transcript.

```bash
summarize meeting.mp3 --format markdown --out summary.md
summarize meeting.wav --format json --out summary.json
summarize examples/sample_transcript.txt --format markdown
summarize notes.txt --provider gemini --model gemini-2.5-flash
```

`--format` is `markdown` or `json`. The result is written to standard output and, with `--out`, to that file as well. `--transcription-model` is used for audio. Text files are read locally. `--provider` is `groq`, `openai`, or `gemini`.

```text
summarize FILE [--format markdown|json] [--out PATH]
               [--provider groq|openai|gemini]
               [--model TEXT] [--transcription-model TEXT]
               [--chunk-size INTEGER] [--verbose]
```

`--chunk-size` is the maximum number of characters sent in one summarization request (default 6000, about 2,000 tokens). A longer transcript is split, with a short overlap, and the partial JSON analyses are merged. On Groq, a chunk waits when sending it would pass 8,000 tokens in the current minute. `--verbose` prints the chunk count to stderr.

You can also run `python -m meeting_summarizer examples/sample_transcript.txt --format json`.

## Web UI

```bash
meeting-web
```

Or, with auto-reload during development:

```bash
uvicorn meeting_summarizer.web.app:app --reload
```

Open http://127.0.0.1:8000, choose an MP3, WAV, M4A, or TXT file, and submit. The page shows the summary, an action-item table, the key decisions, and the raw JSON. The form defaults to Groq, `openai/gpt-oss-120b`, and `whisper-large-v3-turbo`.

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

The same result as Markdown:

```markdown
# Meeting Summary

The team locked a staged rollout of the mobile checklist for Monday, October 26, after build 1.4.2 includes the sync fix and updated copy. Full rollout is targeted for October 30 if crash-free sessions stay above 99.5 percent and support volume holds. Android tablet support is deferred to next quarter.

## Action Items

- **Deliver revised empty-state copy and offline banner specs** — Owner: Maya Chen; Due: Wednesday, October 21
- **Publish the go/no-go checklist** — Owner: Priya Shah; Due: October 20

## Key Decisions

- Staged rollout starts Monday, October 26, only after build 1.4.2 includes the sync fix and copy update.
- Android tablet support is deferred to next quarter.
```

## Project structure

```text
.
├── .env.example
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

## Development

```bash
pytest
```

The tests stub Groq, OpenAI, and Gemini. They check transcription requests, chunk-and-merge chat calls, 429 backoff, the token budget, audio splitting, CLI output, and the upload API. Nothing in the suite calls a paid API.

## License

MIT. See [LICENSE](LICENSE).

# Meeting Summarizer

Turn a meeting recording or a plain-text transcript into a concise summary, action items, and the decisions that were made.

Audio (MP3, WAV, or M4A) is transcribed with OpenAI (`whisper-1` or `gpt-4o-transcribe`). The transcript is sent to a chat model (default `gpt-4o-mini`) which returns structured JSON. Long transcripts are split into overlapping chunks, summarized separately, and then merged so tasks and decisions are not duplicated.

## Features

- Accepts `.mp3`, `.wav`, `.m4a`, and `.txt`
- Transcription with `whisper-1` or `gpt-4o-transcribe`
- Summaries from `gpt-4o-mini` by default, with the chat model configurable per run
- JSON result: a short summary, action items (`task`, `owner`, `due_date`), and key decisions
- Owners and due dates are included only when the meeting states them
- Long transcripts are chunked and merged
- Typer CLI and a single-page FastAPI upload UI
- API key loaded from `OPENAI_API_KEY` in the environment or a local `.env` file

## Installation

Python 3.10 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`pip install -r requirements.txt` installs the runtime packages only. The `dev` extra adds pytest and httpx.

## Configuration

Copy the example environment file and add your key. `.env` is gitignored.

```bash
cp .env.example .env
```

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
OPENAI_TRANSCRIPTION_MODEL=whisper-1
```

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | Yes | Secret used for transcription and summarization |
| `OPENAI_MODEL` | No | Default chat model. Falls back to `gpt-4o-mini` |
| `OPENAI_TRANSCRIPTION_MODEL` | No | `whisper-1` or `gpt-4o-transcribe` |

CLI flags and the web form override these defaults. Audio and transcripts are sent to OpenAI; the app does not store uploads after the response is returned. The transcription API accepts files up to 25 MB.

## CLI

The `summarize` command takes an audio file or a `.txt` transcript.

```bash
summarize meeting.mp3 --format markdown --out summary.md
summarize meeting.wav --format json --out summary.json
summarize examples/sample_transcript.txt --format markdown
summarize notes.txt --model gpt-4o --transcription-model gpt-4o-transcribe
```

`--format` is `markdown` or `json`. The result is written to standard output and, with `--out`, to that file as well. `--transcription-model` is used for audio. Text files are read locally.

```text
summarize FILE [--format markdown|json] [--out PATH]
               [--model TEXT] [--transcription-model TEXT]
               [--chunk-size INTEGER] [--verbose]
```

`--chunk-size` is the maximum number of characters sent in one summarization request (default 12000). A transcript longer than that is split, with a short overlap, and the partial JSON analyses are merged in a final chat call. `--verbose` prints the chunk count to stderr.

You can also run `python -m meeting_summarizer examples/sample_transcript.txt --format json`.

## Web UI

```bash
meeting-web
```

Or, with auto-reload during development:

```bash
uvicorn meeting_summarizer.web.app:app --reload
```

Open http://127.0.0.1:8000, choose an MP3, WAV, M4A, or TXT file, and submit. The page shows the summary, an action-item table, the key decisions, and the raw JSON.

`GET /health` returns `{"status": "ok"}`. `POST /api/summarize` accepts a multipart form with `file`, and optional `model` and `transcription_model` fields. The response body matches the CLI JSON.

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
│   ├── cli.py              # Typer command
│   ├── config.py           # .env loading and model defaults
│   ├── exceptions.py
│   ├── models.py           # Summary, action item, Markdown/JSON rendering
│   ├── summarize.py        # Chunking, summarization, and merge
│   ├── transcribe.py       # Text loading and Whisper transcription
│   └── web/
│       ├── app.py          # FastAPI routes
│       └── templates/
│           └── index.html  # Upload page
├── tests/                  # pytest, with OpenAI mocked
├── pyproject.toml
├── requirements.txt
└── LICENSE
```

## Development

```bash
pytest
```

The tests stub the OpenAI client. They check transcription requests, chunk-and-merge chat calls, CLI output, and the upload API without a network connection.

## License

MIT. See [LICENSE](LICENSE).

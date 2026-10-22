"""Guards for the static GitHub Pages client."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def test_pages_site_keeps_the_visitor_key_local() -> None:
    page = (DOCS / "index.html").read_text(encoding="utf-8")
    script = (DOCS / "app.js").read_text(encoding="utf-8")
    logic = (DOCS / "summarize.js").read_text(encoding="utf-8")
    text_files = [
        path
        for path in DOCS.rglob("*")
        if path.suffix in {".html", ".js", ".css", ".svg"}
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in text_files)

    assert "https://console.groq.com/keys" in page
    assert ">get a free key</a>" in page
    assert "localStorage" in page
    assert "api.groq.com" in page
    assert "never logged" in page
    assert "openai/gpt-oss-20b" in page
    assert "openai/gpt-oss-120b" in page
    assert "whisper-large-v3-turbo" in page
    assert 'id="notes"' in page
    assert 'id="audio"' in page
    assert "localStorage" in script
    assert "https://api.groq.com" in logic
    assert "gsk_" not in combined
    assert "sk-" not in combined
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    assert "actions/deploy-pages" in workflow
    assert "path: docs" in workflow
    assert "secrets." not in workflow


def test_browser_summarizer_logic() -> None:
    result = subprocess.run(
        ["node", "--test", "tests/static_summarizer.test.mjs"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

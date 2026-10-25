"""Verify the saved public explanations using the application renderer."""

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from indexlens.api.explanation import Draft, canonical, validate_draft


def digest(value: Any) -> str:
    """Hash JSON using the application's order-preserving canonical encoding."""
    return sha256(canonical(value).encode()).hexdigest()


def readable(title: str, rendered: dict[str, Any]) -> str:
    """Wrap verbatim application prose with navigation and exact citations."""
    text: str = (
        f"# {title}\n\n"
        "Application-side rendering of the original generation using "
        f"`{rendered['presentation_version']}`. The generated explanation below "
        "has no manual prose edits.\n\n"
        "[Example guide](README.md) · [Structured explanation](explanation.json) · "
        "[Evidence](evidence.json) · [Provenance](provenance.json)\n\n"
        "## Generated explanation\n\n"
        + rendered["explanation"]
        + "\n\n## Exact citations\n\n"
        "Each JSON Pointer resolves within [evidence.json](evidence.json). "
        "Values below retain the original JSON type and precision; a marker "
        "with an empty display keeps its exact text here.\n\n"
    )
    for item in rendered["citations"]:
        text += f"- {item['marker']} `{item['reference']}` = `{item['value_json']}`\n"
    text += "\n## Application limitations\n\n"
    for limitation in rendered["limitations"]:
        text += f"- {limitation}\n"
    return text


def verify(directory: Path) -> None:
    """Check public hashes, exact claims, deterministic output and Markdown."""
    evidence = json.loads((directory / "evidence.json").read_text())
    saved = json.loads((directory / "explanation.json").read_text())
    provenance = json.loads((directory / "provenance.json").read_text())
    rendered = validate_draft(Draft.model_validate(saved["raw_draft"]), evidence)
    for key, value in rendered.items():
        if key != "evidence" and saved.get(key) != value:
            raise ValueError(f"{directory.name}: rendering differs at {key}")
    expected = {
        "public_evidence_sha256": digest(evidence),
        "raw_draft_sha256": digest(saved["raw_draft"]),
        "structured_explanation_sha256": digest(saved),
    }
    for key, value in expected.items():
        if provenance[key] != value:
            raise ValueError(f"{directory.name}: hash differs at {key}")
    if provenance["presentation_version"] != rendered["presentation_version"]:
        raise ValueError(f"{directory.name}: presentation version differs")
    markdown = (directory / "explanation.md").read_text()
    if markdown != readable(provenance["title"], rendered):
        raise ValueError(f"{directory.name}: readable explanation differs")
    if sha256(markdown.encode()).hexdigest() != provenance["readable_sha256"]:
        raise ValueError(f"{directory.name}: readable hash differs")
    print(
        f"{directory.name}: {len(rendered['claims'])} exact claims; "
        f"{rendered['presentation_version']}; hashes and rendering match"
    )


if __name__ == "__main__":
    for name in ("model-comparison", "company-financial-context"):
        verify(Path(__file__).resolve().parent / name)

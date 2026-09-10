"""Detect AI tool / model attribution in git commit metadata.

Signals used (lower bound — Copilot/Cursor/Codex often leave nothing):
  * Co-Authored-By / Co-authored-by / Assisted-by trailers
  * "Generated with [Claude Code]" body marker
  * author/committer name or email (aider, noreply@anthropic.com, bots)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TRAILER_RE = re.compile(
    r"^\s*(?:co-authored-by|assisted-by|generated-by|ai-assisted-by)\s*:\s*(?P<name>[^<\n]*?)\s*(?:<(?P<email>[^>\n]*)>)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
CLAUDE_CODE_MARKER = re.compile(r"generated with \[?claude code", re.IGNORECASE)
AIDER_PREFIX = re.compile(r"^\s*aider\s*:", re.IGNORECASE | re.MULTILINE)

# (tool, regex on "name <email>") — first match wins
TOOL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Claude Code", re.compile(r"noreply@anthropic\.com|claude code", re.I)),
    ("Aider", re.compile(r"\baider\b", re.I)),
    ("GitHub Copilot", re.compile(r"copilot", re.I)),
    ("OpenAI Codex", re.compile(r"\bcodex\b|@openai\.com", re.I)),
    ("Cursor", re.compile(r"\bcursor\b", re.I)),
    ("Gemini CLI", re.compile(r"gemini[\s-]?cli|@google\.com", re.I)),
    ("Devin", re.compile(r"\bdevin\b", re.I)),
    ("Sweep", re.compile(r"\bsweep\b", re.I)),
    ("Claude Code", re.compile(r"\bclaude\b", re.I)),
]

# model name extraction from the trailer's display name, e.g. "Claude Opus 4.5"
MODEL_RE = re.compile(
    r"(claude[\s-]+(?:opus|sonnet|haiku|fable|mythos)[\s-]*[\d.]*|"
    r"gpt-?[\w.]+|o[1-9](?:-\w+)?|gemini[\s-][\w.-]+|"
    r"deepseek[\w.-]*|qwen[\w.-]*|llama[\w.-]*)",
    re.I,
)


@dataclass
class Attribution:
    tool: str
    model: str | None = None
    signal: str = "trailer"   # trailer | marker | author


@dataclass
class ParsedCommit:
    sha: str
    date: str                 # ISO
    repo: str
    attributions: list[Attribution] = field(default_factory=list)

    @property
    def is_ai(self) -> bool:
        return bool(self.attributions)

    @property
    def tools(self) -> set[str]:
        return {a.tool for a in self.attributions}

    @property
    def models(self) -> set[str]:
        return {a.model for a in self.attributions if a.model}


def _classify(name: str, email: str) -> str | None:
    hay = f"{name} <{email}>"
    for tool, pat in TOOL_PATTERNS:
        if pat.search(hay):
            return tool
    return None


def _model_from(name: str) -> str | None:
    m = MODEL_RE.search(name)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip()


def parse_message(message: str, author_name: str = "", author_email: str = "",
                  committer_email: str = "") -> list[Attribution]:
    found: list[Attribution] = []
    seen: set[tuple[str, str | None]] = set()

    def add(a: Attribution):
        key = (a.tool, a.model)
        if key not in seen:
            seen.add(key)
            found.append(a)

    for m in TRAILER_RE.finditer(message):
        name, email = m.group("name") or "", m.group("email") or ""
        tool = _classify(name, email)
        if tool:
            add(Attribution(tool, _model_from(name), "trailer"))

    if CLAUDE_CODE_MARKER.search(message):
        add(Attribution("Claude Code", None, "marker"))
    if AIDER_PREFIX.search(message):
        add(Attribution("Aider", None, "marker"))

    for who in (f"{author_name} <{author_email}>", f"<{committer_email}>"):
        tool = _classify(who, "")
        if tool and "[bot]" in who or "noreply@anthropic.com" in who or "(aider)" in who.lower():
            add(Attribution(tool or "Claude Code", _model_from(who), "author"))

    # marker-only Claude Code + trailer with model → collapse duplicate tool w/o model
    if any(a.tool == "Claude Code" and a.model for a in found):
        found = [a for a in found if not (a.tool == "Claude Code" and a.model is None)]
    return found


def parse_github_commit(repo: str, c: dict) -> ParsedCommit:
    """c = one item from GET /repos/{owner}/{repo}/commits"""
    gc = c.get("commit", {})
    author = gc.get("author") or {}
    committer = gc.get("committer") or {}
    attrs = parse_message(
        gc.get("message", ""),
        author.get("name", ""),
        author.get("email", ""),
        committer.get("email", ""),
    )
    return ParsedCommit(
        sha=c.get("sha", "")[:7],
        date=(author.get("date") or committer.get("date") or "")[:10],
        repo=repo,
        attributions=attrs,
    )

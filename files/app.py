"""AI 전적 프로토타입 — GitHub OAuth → 커밋 트레일러 파싱 → 모델별 전적.

Run:  uvicorn app:app --reload --port 8000
Env:  GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET  (OAuth app; callback = http://localhost:8000/auth/callback)
      GITHUB_SCOPE   default "read:user"  — set "repo" to include private repos
      SESSION_SECRET  any random string
      GITHUB_TOKEN    optional PAT for public-user scans without OAuth
"""
from __future__ import annotations

import asyncio
import os
import secrets
from collections import Counter, defaultdict

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from trailers import ParsedCommit, parse_github_commit

CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
SCOPE = os.getenv("GITHUB_SCOPE", "read:user")
SERVER_TOKEN = os.getenv("GITHUB_TOKEN", "")
MAX_REPOS = int(os.getenv("MAX_REPOS", "30"))
MAX_COMMITS_PER_REPO = int(os.getenv("MAX_COMMITS_PER_REPO", "300"))
CONCURRENCY = 4

app = FastAPI(title="AI record prototype")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", secrets.token_hex(16)))
STATIC = os.path.join(os.path.dirname(__file__), "static")
API = "https://api.github.com"


# ---------- auth ----------
@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/auth/login")
async def login(request: Request):
    if not CLIENT_ID:
        raise HTTPException(500, "GITHUB_CLIENT_ID not set")
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    url = (f"https://github.com/login/oauth/authorize?client_id={CLIENT_ID}"
           f"&scope={SCOPE}&state={state}")
    return RedirectResponse(url)


@app.get("/auth/callback")
async def callback(request: Request, code: str, state: str):
    if state != request.session.get("oauth_state"):
        raise HTTPException(400, "bad state")
    async with httpx.AsyncClient() as c:
        r = await c.post("https://github.com/login/oauth/access_token",
                         headers={"Accept": "application/json"},
                         data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "code": code})
    tok = r.json().get("access_token")
    if not tok:
        raise HTTPException(400, f"token exchange failed: {r.text}")
    request.session["token"] = tok
    return RedirectResponse("/")


@app.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


def _headers(token: str | None):
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _get_json(c: httpx.AsyncClient, url: str, **params):
    r = await c.get(url, params=params)
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise HTTPException(429, "GitHub rate limit hit — log in or set GITHUB_TOKEN")
    r.raise_for_status()
    return r.json()


# ---------- scan ----------
async def list_repos(c: httpx.AsyncClient, login: str, authed: bool) -> list[dict]:
    url = f"{API}/user/repos" if authed else f"{API}/users/{login}/repos"
    params = {"per_page": 100, "sort": "pushed"}
    if authed:
        params["affiliation"] = "owner,collaborator"
    repos = await _get_json(c, url, **params)
    return [r for r in repos if not r.get("fork")][:MAX_REPOS]


async def scan_repo(c: httpx.AsyncClient, full_name: str, login: str) -> list[ParsedCommit]:
    out: list[ParsedCommit] = []
    page = 1
    while len(out) < MAX_COMMITS_PER_REPO:
        try:
            items = await _get_json(c, f"{API}/repos/{full_name}/commits",
                                    author=login, per_page=100, page=page)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (409, 404):   # empty repo / no access
                break
            raise
        if not items:
            break
        out.extend(parse_github_commit(full_name, it) for it in items)
        if len(items) < 100:
            break
        page += 1
    return out[:MAX_COMMITS_PER_REPO]


def aggregate(login: str, commits: list[ParsedCommit]) -> dict:
    total = len(commits)
    ai = [c for c in commits if c.is_ai]
    by_tool, by_model = Counter(), Counter()
    tool_models: dict[str, Counter] = defaultdict(Counter)
    by_month: dict[str, dict] = defaultdict(lambda: {"total": 0, "ai": 0})
    by_repo: dict[str, dict] = defaultdict(lambda: {"total": 0, "ai": 0, "tools": Counter(), "models": Counter()})
    for c in commits:
        m = c.date[:7] or "unknown"
        by_month[m]["total"] += 1
        by_repo[c.repo]["total"] += 1
        if c.is_ai:
            by_month[m]["ai"] += 1
            by_repo[c.repo]["ai"] += 1
            for t in c.tools:
                by_tool[t] += 1
                by_repo[c.repo]["tools"][t] += 1
            for mo in c.models:
                by_model[mo] += 1
                by_repo[c.repo]["models"][mo] += 1
            for a in c.attributions:
                if a.model:
                    tool_models[a.tool][a.model] += 1
    repos = [
        {"repo": r, "total": v["total"], "ai": v["ai"],
         "ratio": round(v["ai"] / v["total"], 3) if v["total"] else 0,
         "tools": dict(v["tools"].most_common()), "models": dict(v["models"].most_common(3))}
        for r, v in by_repo.items()
    ]
    repos.sort(key=lambda x: (-x["ai"], -x["total"]))
    return {
        "login": login,
        "total_commits": total,
        "ai_commits": len(ai),
        "ai_ratio": round(len(ai) / total, 3) if total else 0,
        "by_tool": dict(by_tool.most_common()),
        "by_model": dict(by_model.most_common()),
        "tool_models": {t: dict(m.most_common()) for t, m in tool_models.items()},
        "by_month": dict(sorted(by_month.items())),
        "repos": repos,
        "samples": [{"repo": c.repo, "sha": c.sha, "date": c.date,
                     "tools": sorted(c.tools), "models": sorted(c.models)} for c in ai[:20]],
        "note": "하한선입니다. Copilot·Cursor·Codex는 커밋에 흔적을 안 남기는 경우가 많습니다.",
    }


@app.get("/api/me")
async def me(request: Request):
    tok = request.session.get("token")
    if not tok:
        return {"authed": False, "oauth_configured": bool(CLIENT_ID)}
    async with httpx.AsyncClient(headers=_headers(tok), timeout=30) as c:
        u = await _get_json(c, f"{API}/user")
    return {"authed": True, "login": u["login"], "avatar": u.get("avatar_url")}


@app.get("/api/scan")
async def scan(request: Request, user: str | None = None):
    tok = request.session.get("token")
    authed = bool(tok) and user is None
    token = tok if authed else SERVER_TOKEN or None
    async with httpx.AsyncClient(headers=_headers(token), timeout=60) as c:
        if authed:
            login = (await _get_json(c, f"{API}/user"))["login"]
        elif user:
            login = user
        else:
            raise HTTPException(400, "log in or pass ?user=<github login>")
        repos = await list_repos(c, login, authed)
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(r):
            async with sem:
                return await scan_repo(c, r["full_name"], login)

        results = await asyncio.gather(*(one(r) for r in repos), return_exceptions=True)
    commits: list[ParsedCommit] = []
    errors = []
    for r, res in zip(repos, results):
        if isinstance(res, Exception):
            errors.append({"repo": r["full_name"], "error": str(res)[:120]})
        else:
            commits.extend(res)
    data = aggregate(login, commits)
    data["repos_scanned"] = len(repos)
    data["errors"] = errors
    return JSONResponse(data)

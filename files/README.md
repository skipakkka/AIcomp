# AI 전적 프로토타입 — GitHub 커밋 트레일러 파싱

"내 GitHub 커밋 중 몇 %가 AI와 함께 만들어졌고, 어떤 도구·모델을 썼나"를 보여주는 최소 프로토타입.

```
app.py          FastAPI 서버 — GitHub OAuth, 레포/커밋 수집, 집계 API
trailers.py     커밋 메시지·작성자 메타데이터에서 AI 흔적을 찾는 파서 (핵심)
static/         대시보드 (단일 HTML)
test_*.py       파서 단위 테스트 + 가짜 GitHub API로 돌리는 E2E 테스트
```

## 실행

```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
# http://localhost:8000
```

### 모드 1 — 공개 아이디로 검색 (OAuth 없이 바로)
화면에서 GitHub 아이디 입력 → 공개 레포만 스캔.
비로그인 GitHub API는 시간당 60회라 금방 막힘 → `.env`나 셸에 `GITHUB_TOKEN=<PAT>` 설정 권장 (5,000회/시).

### 모드 2 — GitHub OAuth (본인 레포, 오픈뱅킹식 "연동")
1. GitHub → Settings → Developer settings → OAuth Apps → New OAuth App
   - Homepage: `http://localhost:8000`
   - Callback: `http://localhost:8000/auth/callback`
2. 환경변수
   ```
   GITHUB_CLIENT_ID=...
   GITHUB_CLIENT_SECRET=...
   SESSION_SECRET=아무_긴_문자열
   GITHUB_SCOPE=read:user        # 비공개 레포까지 보려면 repo
   ```
3. 서버 실행 후 "GitHub로 로그인" → 자동 스캔.

## API
- `GET /api/scan` (로그인) / `GET /api/scan?user=<login>` (공개)
  ```json
  { "ai_ratio": 0.42, "by_tool": {"Claude Code": 31, "GitHub Copilot": 4},
    "tool_models": {"Claude Code": {"Claude Opus 4.5": 20, "Claude Sonnet 4.6": 11}},
    "by_month": {"2026-08": {"total": 40, "ai": 17}},
    "repos": [{"repo": "…", "ai": 17, "total": 40, "ratio": 0.425, "tools": {...}}] }
  ```

## 파서가 잡는 신호 (trailers.py)
| 도구 | 신호 |
|---|---|
| Claude Code | `Co-Authored-By: Claude <모델명> <noreply@anthropic.com>`, `Generated with [Claude Code]`, author email |
| Aider | `Co-authored-by: aider (<model>)`, 작성자명 `(aider)`, 메시지 `aider:` 접두 |
| GitHub Copilot | `Co-authored-by: Copilot …`, `copilot-swe-agent[bot]`, `Assisted-by: GitHub Copilot (<model>)` |
| Codex / Cursor / Gemini CLI / Devin | 트레일러에 이름이 있을 때만 |

**하한선이다.** Copilot·Cursor·Codex는 기본적으로 흔적을 안 남기고, Claude Code도 설정으로 끌 수 있다.
UI에 이 사실을 항상 표시한다 (`note` 필드).

## 다음 단계 후보
- 스캔 결과 캐시 (SQLite) → 재방문 시 증분 스캔 (`since=` 파라미터)
- PR 머지율·스타 등 결과 지표를 레포별로 붙여 "모델별 실전 전적" 만들기
- 프로필 공유 URL (`/u/<login>`) — op.gg 전적 링크 대응
- zip 업로드 / MCP 커넥터 어댑터 추가

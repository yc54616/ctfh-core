# 네이티브 Codex 풀이기 + 검사기: 조사 근거와 확정 구조

작성: 2026-10-09 · 상태: 1차 구현 완료 (`src/ctfh_core/native.py`), 실제 대회 사이트 종단 검증은 아직 안 함

이전 문서 [BROWSER_NATIVE_CTF_SOLVER_2026-10-08.md](BROWSER_NATIVE_CTF_SOLVER_2026-10-08.md)의
live 래퍼/TUI 방식은 입력이 많고 그냥 `codex`를 쓰는 것보다 불편했다. 사용자 결정에 따라 다음처럼 바꿨다.

- 풀이는 문제마다 평범한 대화형 `codex` 하나가 맡는다 (`--dangerously-bypass-approvals-and-sandbox`).
- 에이전트는 하나만 더 둔다. 문제 수집, flag 검증·제출, writeup 검토를 맡는 검사기다.
- 둘 사이의 약속은 폴더 구조 하나로 통일한다.

## 1. 조사 범위

GitHub 오픈소스, CTF 에이전트 논문, 검증자(verifier)·판정자(judge) 연구, 브라우저 로그인 자동화 자료를 찾아봤다.
웹 결과는 참고 자료로만 썼다. 라이선스를 확인하지 못한 저장소는 코드를 가져오지 않고 구조만 참고했다.

## 2. 오픈소스

| 저장소 | 라이선스 | 핵심 | 이번 설계에 가져온 것 |
|---|---|---|---|
| verialabs/ctf-agent | MIT | 조정자 LLM과 CTFd 5초 폴링, 문제마다 여러 모델이 경쟁해 먼저 찾은 flag 채택. BSidesSF 2026 52/52 | 조정자 1 + 문제별 풀이 세션 구조, 주기적 재수집 |
| aliasrobotics/CAI | MIT (현재 archive 브랜치) | `ctf_agent` → `flag_discriminator` 인계 | 검사기를 분리한다는 생각. 다만 원본은 flag 형식만 보므로 약하다 |
| kaist-hacking/CTFusion | 미확인 | CTFd MCP와 제출 프록시. 문제당 첫 정답 한 번만 실제 제출. 정적 14.4% 대 라이브 6.3% | 제출 게이트: flag 해시당 한 번, 결과 기록 |
| ctfbridge (PyPI) | 미확인 | CTFd, rCTF, GZCTF, HTB 등 로그인·목록·제출. Dreamhack 미지원 | 쓰지 않음. 사이트별 어댑터 대신 브라우저로 그때그때 분석 |
| umbra2728/ctfd-mcp | Apache-2.0 | `list_challenges`, `challenge_details`, `submit_flag`, Codex MCP 설정 예 | CTFd 전용 확장 후보 (열린 질문) |
| MrJamescot/ctfd-mcp-server | MIT | `submit_flag`에 confirm=True 요구 | 제출 전 확인 단계 |
| CTFd/ctfcli | Apache-2.0 | 운영자용 도구 | 해당 없음 |
| smtg-ai/claude-squad | AGPL-3.0 | tmux + worktree 다중 에이전트 | 개념만 참고. 코드 복사 금지 |
| awslabs/cli-agent-orchestrator | Apache-2.0 | tmux 기반 감독자, handoff/assign/send_message | 감독자가 tmux 창에 메시지를 넣는 방식 |
| alamops/agetor | MIT | tmux `load-buffer` + `paste-buffer`로 전달, 에이전트 JSONL 읽기 | 메시지 전달과 rollout 기반 상태 추적 |
| super-agent-ai/tmux-agents, kingbootoshi/codex-orchestrator, mekarpeles/cmux | — | tmux 다중 세션, 에이전트별 수신함 | 창 하나 = 문제 하나 |
| palisaderesearch/intercode | 미확인 | 단순 ReAct&Plan으로 InterCode-CTF 95% | 풀이기는 복잡하게 감싸지 않는다 |
| telagod/abyss | 미확인 | Rust 코드 그래프와 출력 압축 프록시, Codex 훅 | 관련성 낮음 (RTK·codebase-memory와 겹침) |
| telagod/code-abyss | MIT | 페르소나·스킬 묶음, Stop 훅 강제 | 관련성 낮음 |

브라우저 로그인 참고 자료:

- browser-use: 실제 프로필, `storage_state`, `sensitive_data` 자리표시자, TOTP, `allowed_domains`.
- laclaw-agent/agent-vault: 비밀번호 관리자 → CDP 입력, 도메인 확인 후 채움. 에이전트는 값을 보지 못한다.
- Kernel 글: 자격 증명을 에이전트 루프 밖에 두고, 로그인은 상태 기계로 처리하고, 프로필을 재사용한다.

## 3. 논문과 연구

| 자료 | 핵심 결과 | 설계 반영 |
|---|---|---|
| arXiv 2608.26237 (ctf-abacus) | 회수한 flag 가운데 실행 흔적으로 확인되는 것은 62–87%. 실행 72.9%, 유도 10.9%, 근거 없음 16.2%. 키워드 탐지기 F1 0.28 | flag를 믿지 않고 `solve.py` 재실행으로 같은 flag를 다시 얻게 한다 |
| arXiv 2412.02776 | 단순한 에이전트도 강하다 | 풀이기는 그냥 Codex |
| Anthropic 사이버 대회 보고 | HTB 19/20, PlaidCTF 0 | 어려운 대회는 사람 개입 경로를 남긴다 |
| MARCH (ACL 2026) | 검사기가 풀이기 출력을 보지 않고 검증하면 확증 편향이 준다 | writeup 검토는 문제만 보고 예상 풀이를 먼저 적은 뒤 산출물을 본다 |
| Red-MIRROR (arXiv 2603.27127) | 3표 다수결 + flag 패턴 | 형식 검사는 하되 그것만으로 통과시키지 않는다 |
| arXiv 2608.09567 | 오라클 민감도 60%, 특이도 45% | LLM 판정만으로 정답을 정하지 않는다. 실제 판정은 사이트 응답 |
| SEC-bench | 검증 에이전트가 재현으로 확인 | 재현 기반 검증 |
| CTF-Dojo (arXiv 2508.18370) | writeup이 힌트로 쓸모 있다 | writeup을 구조화해 남긴다 |
| S&P 2026 포스터 | Claude Code·Codex CLI를 pwn 28문제에 적용, 실패를 단계별로 분류 | 검토 결과에 `stage` (discovery/leak/primitive/goal) 기록 |
| arXiv 2607.05904 | 판정자는 설득력에 끌린다 | 먼저 판단하고 나중에 보는 순서 |
| arXiv 2601.14691 | 사고 과정이 아니라 관찰 가능한 증거로 판정 | 코드와 실행 기록이 기준 |
| arXiv 2606.09863, JP3ra/agentic-false-success | 검증 후 보고로 거짓 성공 17.1% → 0%. 다만 단순 검증기는 진짜 성공의 52%를 기각 | 실패는 `needs_fix`로 돌려보내고 사유를 알려 준다. 사용자 `approve` 경로를 둔다 |

## 4. 확정 구조

### 4.1 실행

```text
ctfh2 <URL> [--secondary] [--model M] [--thinking E] [--max-parallel N] [--no-submit] [--dir D]
```

1. 대회 폴더를 만들고 (`~/Desktop/CTF/<사이트>`) 수집기 `codex exec`가 문제 폴더를 채운다 (결정 2).
2. tmux 세션 `ctfh-<대회>`에 `checker` 창을 띄우고, 검사기가 문제마다 `codex` 창을 연다 (결정 1).
3. 사용자는 `tmux attach`로 아무 창에나 들어가 평소처럼 Codex와 대화할 수 있다.

### 4.2 폴더 약속

```text
<contest>/
  contest.toml            url, flag_regex (사람이 고쳐도 됨)
  AGENTS.md
  .ctfh/                  site.json(플랫폼·flag 형식·제출 힌트), board.txt, collect.log
  challenges/<slug>/
    challenge.md challenge.json files/     ← 수집기
    AGENTS.md                              ← 풀이 규칙
    work/NOTES.md solve.py flag.txt writeup.md  ← 풀이기
    status.json .ctfh-check/               ← 검사기만 씀
    USER_SOLVED                            ← 사용자 (ctfh2 done)
```

### 4.3 검사기 상태

`pending → solving → checking → verified → submitting → accepted → done`

- 재현 실패 → `needs_fix`. 사유를 `status.json`에 적고 풀이 창에 `[검사기] …`로 보낸다.
- 사이트 오답 → `needs_fix`. 같은 flag 해시는 다시 제출하지 않는다.
- 결과 불명 → `unknown`. 제출 도중 검사기가 멈춘 경우도 여기로 간다. 다시 제출하지 않고 사용자가 `done` 또는 `approve`로 정한다.
- 끝난 상태: `done`, `user_solved`, `site_solved`. 해당 창을 닫는다.

### 4.4 flag 검증 순서

1. `flag.txt`가 한 줄인지, `flag_regex`와 맞는지 확인.
2. `solve.py`가 있는지, 그 안에 flag 문자열이 그대로 들어 있지 않은지 확인.
3. 문제 폴더에서 `python3 solve.py`를 다시 실행해 (제한 시간 기본 300초) 출력에 같은 flag가 나오는지 확인.
   실행 기록은 flag를 `<FLAG>`로 가린 채 `.ctfh-check/rerun.log`에 남긴다.
4. 제출: 해시당 한 번. 브라우저를 쓰기 전에 `submitting`과 `pending` 기록을 먼저 남긴다.
   응답은 accepted / wrong / already_solved / unknown / needs_user.
5. writeup 검토: 별도 `codex exec`가 출력 스키마로 답한다. 문제만 보고 예상 풀이를 먼저 적고 그다음 산출물을 본다.

상태 파일과 로그에는 flag 원문 대신 SHA-256만 남긴다.

### 4.5 멈춘 세션 처리

- Codex rollout(`~/.codex/sessions/…`, 보조 계정은 `~/.codex-secondary/sessions/…`)에서
  `session_meta.source == "cli"`이고 `cwd`가 문제 폴더인 파일을 찾는다.
- 마지막 턴이 `task_complete`나 `turn_aborted`이고 `--idle-seconds` 동안 변화가 없으면 재촉 메시지를 보낸다.
- 창이 닫혔으면 60초 뒤에 다시 연다. 재촉과 재시작을 합쳐 `--nudge-limit`번까지만 한다.
- 토큰은 rollout의 `token_count.total_token_usage.total_tokens`로 보드에 표시한다.

### 4.6 로그인

- 기본: 사용자가 이미 로그인해 둔 Chrome을 `chrome-use`로 쓴다 (세션 `ctfh-collect`, `ctfh-submit`).
- 선택: `~/.config/ctfh/credentials.toml` (권한 600 필수).
  ```toml
  ["ctf.example.com"]
  username = "..."
  password = "..."
  login_hosts = ["auth.example.com"]   # 로그인 페이지가 다른 호스트일 때만
  ```
  이 파일이 있으면 수집기는 `ctfh2 secret-fill <대회폴더> username|password @ref --session ctfh-collect`로만 입력한다.
  이 명령은 현재 탭 호스트를 확인한 뒤 `chrome-use fill --from-env`로 값을 넘기므로 모델 문맥, 명령줄, 로그에 값이 남지 않는다.
- 2FA·캡차: 수집기는 `needs_user`로 끝난다. 처음 실행이면 터미널이 로그인을 기다리고,
  실행 중이면 보드 맨 위에 경고가 뜬다.
- 한계: 풀이 세션은 호스트 전체 권한으로 돈다. 그래서 Chrome 프로필이나 credentials 파일을 읽을 수 있다.
  이 방식은 값을 모델 대화에 넣지 않는 것까지만 보장한다.

## 5. 구현에서 확인한 사실 (2026-10-09, Codex CLI 0.161.0)

- 대화형 `codex`는 공유 백그라운드 서버와 기능 설정이 다르면 "Restart with these settings" 확인 화면을 띄운다.
  재시작하면 사용자의 다른 세션이 끊길 수 있어서 풀이 창은 `--no-daemon`으로 띄운다.
  `codex-secondary` 래퍼는 이미 `--no-daemon`을 붙이므로 중복해서 넣지 않는다.
- `--dangerously-bypass-approvals-and-sandbox`로 띄우면 신뢰 확인 없이 YOLO 모드로 바로 시작한다.
- tmux `paste-buffer -p` 뒤 `Enter`로 보낸 `[검사기] …` 메시지는 Codex TUI에서 새 턴으로 제출된다.
  rollout에서 세션 식별, idle 감지, 토큰 증가(24,970 → 55,919)를 확인했다.
- 단위 테스트: `tests/test_native.py` 18개, 전체 197개 통과.
  가짜 tmux와 가짜 codex로 상태 흐름을 확인했고, 실제 tmux로 붙여넣기와 정확한 창 이름 매칭을 확인했다.
- 아직 안 함: 실제 CTF 사이트를 대상으로 한 수집기·제출기 종단 실행.

## 6. 열린 질문

1. 사용자가 말한 "어비스"가 telagod/abyss, code-abyss, 다른 것 가운데 무엇인지.
2. CTFd처럼 API가 있는 플랫폼은 브라우저 대신 API(예: ctfd-mcp)로 수집·제출할지.
3. 인스턴스를 켜야 하는 문제를 수집기가 자동으로 켤지. 지금은 방법만 적고 풀이기가 켠다.
4. writeup 검토를 여러 표로 할지. 지금은 한 번이고 실제 정답 판정은 사이트 응답이 한다.
5. 이전 live_* 모듈을 별도 브랜치로 보관하고 정리할지.

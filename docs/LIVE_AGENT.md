# CTF 에이전트 사용 및 연결 규약

## 터미널에서 가장 짧게 사용하기

[터미널 대시보드 미리보기 (예시 데이터)](images/ctfh-terminal-dashboard.svg)

저장소 폴더에서 사이트 주소만 입력한다.

```bash
rtk proxy ./ctfh2 https://h4ckingga.me/challenges
```

연결된 Chrome에서 사이트를 열고 저장 위치·Codex 실행기 선택·진행 표시를 자동으로 처리한다.
문제들은 동시에 진행된다. 아래쪽에는 완료·제출·실패 같은 대회 주요 결과만 보인다.
문제 목록에서 항목을 클릭하거나 방향키로 선택하면 오른쪽에 그 문제의 작업 메모와
세부 진행 기록이 나온다.
문제 목록의 `토큰` 열에는 문제별 입력+출력 토큰 누계가, 상단에는 대회 전체 누계가 나온다.
문제 상세에는 입력·캐시 입력·출력 토큰을 따로 표시한다. Codex가 턴 완료 사용량을
보내기 전에는 `—`로 표시된다. 캐시 입력은 입력 토큰에 포함되어 중복 합산하지 않는다.
이미 로그인된 Chrome이나 저장된 로그인 금고를 사용한다. 직접 로그인이 필요하다는 안내가
나오면 브라우저에서 로그인하고 대시보드에서 `R`을 누른다.

실행 중에는 문제 목록에서 방향키로 항목을 고른다.

| 키 | 동작 |
|---|---|
| `D` | 선택한 문제를 완료 처리하고 해당 작업만 중단 |
| 클릭 / 방향키 | 선택한 문제의 세부 진행 기록 보기 |
| `R` | 브라우저에서 로그인한 뒤 풀이 재개 |
| `S` | 이 대회의 Codex 모델·추론 강도 설정 |
| `Q` | 실행 중단, 진행 상태 보관 |

여러 문제를 직접 풀었다면 각각 선택해 `D`를 누른다. 사이트에 직접 정답을 제출했을 때의
자동 완료 감지도 계속 동작한다. 재실행은 다음 한 줄이다.

```bash
rtk proxy ./ctfh2
```

마지막 대회와 대화를 이어간다. 처음부터 주소 없이 실행하면 사이트 주소만 물어본다.
처음 실행하는 대회는 선택한 native Codex를 사용한다.
기존 대회의 계정·진행은 재사용한다. 같은 URL의 기존 상태가 여럿이면 가장 최근 상태를
고른다. 계정을 지정해야 할 때만 `--account 계정이름`, 금고를 지정할 때만
`--auth-entry 금고항목`을 추가한다. `--account`는 로그인 비밀번호가 아닌 구분용 이름이다.
연결 실패 시 다른 엔진으로 자동 재시도하지 않는다.
일반 터미널 로그가 필요하거나 TUI를 쓸 수 없는 환경에서는 `--plain`을 붙인다.
이때는 `목록`, `완료 2`, `종료`를 입력한다. 표와 로그를 함께 보는 TUI에는
Python `textual` 의존성이 필요하다. 패키지 설치 시 함께 설치된다.

## 검사기 화면 키 (`ctfh2 <URL>`)

검사기 창은 키로 조작하는 표다. `--plain`을 주면 예전 텍스트 표로 나온다.

맨 위 둘째 줄은 상태별 개수다 (✓ 푼 문제, ● 풀이, ◐ 검사, ↑ 제출, ⏳ 대기, ! 확인 필요). 표는 확인 필요 → 진행 중 → 대기 →
끝 순서로 묶어 보여 주고, 끝난 문제는 흐리게 맨 아래에 둔다. 상태는 한글로 보이고 (`manual`은 직접 확인, `unknown`은 결과 모름),
상태만으로 뻔한 메모는 숨긴다. 표에서 잘린 메모는 아래 상세 칸에 선택한 문제의 전문으로 나온다.

| 키 | 동작 |
|---|---|
| Enter | 고른 문제의 풀이 창으로 이동 (돌아오기: tmux 창 목록, 기본 `Ctrl-b w`) |
| `n` | 지금 재촉 (자동 재촉 횟수에 넣지 않음) |
| `r` | 닫힌 창 다시 열기, `manual`·`hold` 문제 재개 |
| `o` | `work/NOTES.md` 보기 |
| `p` | 보류: 창을 닫고 `r`을 누를 때까지 자동 재시작 안 함 (팀원이 푸는 중 등) |
| `d` | 해결로 표시하고 창 닫기 (팀원이 푼 문제) |
| `s` | 지금 재스캔 (기본 60분마다, `gpt-6-luna`, 낮은 추론 강도) |
| `t` | 정렬 기준을 다음 칸으로 (문제 → 분류 → 상태 → 토큰 → 활동 → 메모 → 기본 묶음). 표 머리글을 클릭해도 그 칸으로 정렬하고, 다시 누르면 반대로, 세 번째에는 기본 묶음으로 돌아간다 (토큰은 많은 순부터) |
| `+` / `-` | 동시 창 수를 바로 조절 (`--max-parallel`을 바꾸기 전까지 검사기를 다시 켜도 유지). 제한이 있으면 푼 팀이 많은 문제부터 연다 |
| `q` | 검사기 종료 (풀이 창은 계속 돈다) |

검사기가 알아서 하는 일:

- **manual**: 재촉·재시작을 3번(`--nudge-limit`) 했거나, 재촉 2번 동안 `work/NOTES.md`가 그대로면 재촉을 멈추고 사용자에게 넘긴다.
- **일시정지**: Codex 서버에 연결되지 않거나, 사용 한도에 닿았는데 크레딧이 없거나, 대부분의 창에 Codex 오류가 보이면 재촉·재시작·재스캔을 멈춘다. 10분마다 창 하나로 다시 시도하고, 풀리면 멈춰 있던 창을 한 번씩 깨운다.
- **대회 종료**: 수집할 때 종료 시각을 `.ctfh/site.json`에 적는다 (`contest.toml`의 `ends_at`이 우선). 마지막 24시간은 남은 시간을 보여 주고, 끝나면 못 푼 문제 창은 `hold`로 닫는다. 정답 처리된 문제는 writeup을 마무리한다.
- **가짜 flag**: 원격 문제(`remote.txt`)에서 fake·dummy 같은 말이 든 flag나 배포 파일(zip/tar 안 포함)에 들어 있는 flag는 제출하지 않고 풀이 창에 돌려보낸다.
- **바뀌는 원격 flag**: 원격 문제를 다시 실행했을 때 대회 형식의 다른 flag가 나오면 새 값으로 `flag.txt`를 바꿔 제출한다 (인스턴스마다 flag가 달라지는 문제). 다시 실행이 실패했는데 서버가 닫혀 있으면 오답이 아니라 서버 대기로 둔다.
- **첨부는 뒤에서 받기**: 수집은 문제 목록과 첨부 목록만 가져오므로 웹·원격 문제는 바로 시작한다. 첨부는 재스캔 모델로 문제마다 동시에 받고, 각 작업은 자기 chrome-use 세션(`ctfh-download-<slug>`)을 쓰며 끝나면 검사기가 그 세션을 닫는다. 제출도 같은 방식(`ctfh-submit-<slug>`)으로 한꺼번에 나간다. 문제마다 파일을 다 받는 즉시(`files/.ctfh-done`) 그 문제 창을 연다. 3번 실패하면 받은 것만으로 시작한다.
- **검사기 자동 재시작**: 검사기가 죽으면 5초 뒤 다시 켠다. `q`나 Ctrl-C로 끄면 끝나고, 연달아 5번 바로 죽으면 오류를 화면에 남기고 멈춘다.
- **보조 작업 비용**: 사이트 제출은 low, writeup 검토는 medium 추론 강도로 돌린다. 모델은 풀이 모델과 같다 (`--submit-thinking`, `--review-thinking`, `same`이면 `--thinking`을 따름).
- **규칙·사용량**: 수집할 때 대회 규칙을 요약해 화면과 각 `AGENTS.md`에 넣는다. Codex 주간 사용량은 남은 양으로 보여 주고, 자동화·AI 금지 규칙과 남은 사용량 10% 이하는 `!!`로 표시한다.

## Codex 실행기와 Chrome 연결

이 저장소의 `ctfh.toml`은 일반 `codex`를 기본값으로 둔다. 인자를 생략한
`./ctfh2 URL`과 `./ctfh2`는 `~/.codex` 계정으로 실행한다. 발견·감시 턴에는
저장소 작업 공간, Chrome 세션 런타임 경로, 네트워크 접근을 허용한다.
각 문제 풀이 턴은 일반 계정이어도 `--dangerously-bypass-approvals-and-sandbox`로
실행한다. 보조 계정을
사용할 때만 `--codex codex --secondary`를 붙인다. 이 경우 이전에 사용자가 지정한
`codex-secondary --dangerously-bypass-approvals-and-sandbox` 실행을 적용한다.
`--secondary`는 CTFH의 옵션이며 실제 Codex CLI에는 전달하지 않는다.
모델과 추론 강도는 `--model gpt-6-sol --thinking xhigh`처럼 지정할 수 있다.
이 값은 대회 상태에 저장되어 새 문제 세션과 재개 세션 모두에 적용된다.
TUI에서는 `S`로 같은 값을 바꿀 수 있다. 저장 시 이미 실행 중인 턴은 유지하고,
이후 시작하는 턴부터 새 설정을 적용한다. 모델 입력칸을 비우거나 추론 강도를
`계정 기본값`으로 고르면 그 항목의 대회별 재정의를 해제한다.
생략하면 선택한 Codex 계정의 설정을 따른다. 모델과 추론 강도 설정은 계정의
사이버 안전 판정을 바꾸지 않는다.

터미널 단축 명령은 모델을 실행하기 전에 `chrome-use`로 실제 Chrome에서 사이트를 열어
연결을 확인한다. 발견·감시와 문제별 작업은 각각 저장된 Chrome 세션을 사용한다.
일반 `codex`에는 Chrome 세션 잠금 경로의 쓰기 권한과 브라우저 연결용 네트워크 접근을
해당 실행에만 추가한다. 모델 턴에서 접근이 거부되면 이벤트에서 실제 오류를 확인한다.
비밀번호는 명령 인자에 넣지 않고 기존 Chrome 로그인이나 금고 항목을 사용한다.

자동 상태 경로와 마지막 대회 기록은 저장소의 `.ctfh-live/` 아래에 저장한다.
명시적인 경로로 준비한 기존 대회도 같은 사이트라면 재사용한다. 절대 경로로 실행 파일을
호출해도 같은 저장소를 기준으로 재개한다. 장시간 서비스 운용이나 세부 옵션이 필요하면
아래 상세 명령을 사용한다.

## 상세 명령과 연결 규약

사용자가 모델 연결을 맡고, 이 프로젝트는 대회 실행·문제 상태·브라우저 관찰·제출·복구를 맡는다.
사이트별 API나 커넥터를 미리 작성할 필요는 없다. 연결한 에이전트가 화면을 읽고 문제를 푼다.
새 URL을 받으면 그 페이지에서 로그인 상태, 대회 규칙, 문제 목록으로 가는 길,
완료 표시와 플래그 제출 UI를 확인한다. 사이트마다 화면 구조가 달라도 이 탐색 결과로
해당 대회의 작업을 진행한다.
문제별 풀이 세션은 서로 다른 native Codex 대화 ID와 작업 폴더를 사용한다.
에이전트가 플래그를 자동 제출할 때는 후보 해시를 먼저 기록하고 사이트 판정을
브라우저에서 확인한다. 수락된 플래그는 작업 폴더의 `flag.txt`에 정확한 바이트로,
풀이 과정은 `writeup.md`에 저장한다. 두 파일은 비공개 권한(0600)이어야 하며,
플래그 파일의 SHA-256이 제출 후보와 같아야 성공 기록을 확정한다.
이미 사이트에서 풀린 것으로 확인된 문제의 플래그는 알 수 없으면 만들어내지 않는다.

저장소 루트의 실행 파일 `./ctfh2`로 바로 사용할 수 있다. 별도 설치나 `PYTHONPATH` 설정이
필요 없으며, 아래의 긴 `env PYTHONPATH=src python3 -m ctfh_core` 부분을 `./ctfh2`로 바꿔도 된다.

```bash
rtk proxy ./ctfh2 live run https://h4ckingga.me/challenges --state .ctfh-live/h4ckinggame
rtk proxy ./ctfh2 live status --state .ctfh-live/h4ckinggame --details
rtk proxy ./ctfh2 live events --state .ctfh-live/h4ckinggame --follow
rtk proxy ./ctfh2 live mark-solved --state .ctfh-live/h4ckinggame --challenge stable-key
```

처음 실행할 때 연결 명령이 저장되어 있지 않으면 현재 환경의 `codex`를 사용한다.
보조 계정을 쓰려면 `--codex codex --secondary`, 사용자 연결기를 쓰려면
`--agent-command`를 지정한다. 선택한 연결 방식은 상태에 저장된다.

```text
사이트 URL + 로그인된 Chrome / 금고 항목
                   │
                   ▼
       live 컨트롤러 · SQLite 원장
          ├─ 발견/사이트 완료 감시 ─ Chrome 세션 1
          ├─ 문제 A 작업 ─ Codex 대화 A ─ Chrome 세션 A
          ├─ 문제 B 작업 ─ Codex 대화 B ─ Chrome 세션 B
          └─ ... 발견된 모든 문제를 동시에 실행
                   │
          사용자 완료 입력 / 사이트 완료 감지
                   │
          해당 문제 작업만 중단 · solved 기록
```

## 1. 모델 없이 대회 준비

저장소 루트에서 실행한다. 계정 라벨은 비밀 값이 아니다. 기존 Chrome 로그인 세션을 사용하거나
`--auth-entry`에 chrome-use 금고 항목 이름을 넣는다. 비밀번호를 인자로 넘기지 않는다.

```bash
rtk proxy env PYTHONPATH=src python3 -m ctfh_core live init \
  https://h4ckingga.me/challenges --state .ctfh-live/h4ckinggame --account my-account
rtk proxy env PYTHONPATH=src python3 -m ctfh_core live status \
  --state .ctfh-live/h4ckinggame --details
```

`init`은 로컬 상태만 준비한다. 브라우저나 모델을 실행하지 않는다.

## 1-1. 선택한 Codex로 직접 실행

```bash
rtk proxy ./ctfh2 live check-engine --codex codex --secondary
rtk proxy ./ctfh2 live run https://h4ckingga.me/challenges \
  --state .ctfh-live/h4ckinggame --codex codex --secondary
```

첫 명령은 선택한 계정의 로컬 로그인 상태를 확인하고 종료한다. 모델 턴이나 문제 풀이를
시작하지 않는다. 기본 계정은 `--codex codex`를 사용한다. 발견·감시와 문제별 작업은
각자의 native 대화 ID를 보존하며 `codex exec` 프로세스로 직접 실행한다.

재개할 때 URL을 다시 입력할 필요는 없다.

```bash
rtk proxy ./ctfh2 live run --state .ctfh-live/h4ckinggame --codex codex --secondary
```

기존 상태의 `codex-shift` 엔진 값은 직접 실행하는 `codex`로 전환한다. 문제 기록과
대화 ID는 유지한다. 사용자 JSONL 연결기에서 native Codex로 전환할 때는
`--reset-thread`가 필요하다. 인증·사용량 제한·정책 거절 시 실행을 중단한다.

## 1-2. AI 풀이 진행 보기

에이전트를 이 버전으로 시작하거나 재개한 뒤 **별도 터미널**에서 실행한다.

```bash
rtk proxy ./ctfh2 live events --state .ctfh-live/h4ckinggame --follow
```

문제 키, 턴 번호, 시각과 함께 공개 진행 메시지, 작업 계획, 도구 시작·종료, 작업 메모,
제출 판정, 문제 완료와 작업 중단을 표시한다. 메시지는 완성되는 대로 표시하며
도구 실행 중에는 시작 이벤트를 먼저 볼 수 있다. 표시 예시:

```text
14:20:01 [web-01 · turn 4] agent.message  첨부 파일의 구조를 확인하고 있습니다.
14:20:02 [web-01 · turn 4] tool.started  python3
14:20:04 [web-01 · turn 4] tool.completed  python3 · completed · exit 0
14:20:05 [web-01 · turn 4] checkpoint  입력 형식 확인 · 다음: 로컬 재현 시험
```

```bash
# 한 문제만 따라보기 — 키는 live status --details에서 확인
rtk proxy ./ctfh2 live events --state .ctfh-live/h4ckinggame --challenge web-01 --follow
# 최근 기록만 보기 / JSONL 출력
rtk proxy ./ctfh2 live events --state .ctfh-live/h4ckinggame --limit 100
rtk proxy ./ctfh2 live events --state .ctfh-live/h4ckinggame --follow --json
```

기본으로 최근 50개를 보여준 뒤 새 기록을 따라간다. `--limit 0 --follow`는 이후 기록만
표시한다. `Ctrl-C`는 조회만 종료하며 다른 터미널이나 서비스의 풀이 작업은 계속된다.
상태 DB에 최근 5,000개를 보관하므로 재시작 후에도 조회할 수 있다. 이 기능을 붙이기 전의
실행 기록은 복원하지 않는다. 이벤트는 진행 관찰용이며 정답 판정의 근거로 사용하지 않는다.

두 Codex 계정을 지원한다. 사용자 JSONL 연결기는 아래 `progress.message`를
보내면 같은 화면에 표시된다. 도구 이벤트는 실행 파일·동작 이름과 종료 상태를 표시하며,
도구 인자·원문 출력·최종 보고서 전체·내부 추론은 이벤트에 저장하지 않는다.
공개 진행 문장은 길이를 제한하고 흔한 비밀 값 형식을 가린다. 자유 서술에 들어간 모든
비밀을 탐지하는 기능은 아니므로 연결기에서도 공개 진행 문장에 비밀을 넣지 않아야 한다.

## 2. 사용자가 만든 연결 실행

연결 명령은 JSON argv 배열이다. 셸 문자열로 평가하지 않는다. 재시작에도 찾을 수 있도록
실행 파일과 스크립트에 절대 경로를 권장한다. 명령은 상태에 저장되므로 비밀 값을 넣지 않는다.

```bash
rtk proxy env PYTHONPATH=src python3 -m ctfh_core live run \
  --state .ctfh-live/h4ckinggame \
  --agent-command '["/usr/bin/python3", "/absolute/path/my_codex_connection.py"]'
```

이후 `live run --state ...`만 실행하면 저장된 연결 명령과 스레드를 재사용한다.
첫 발견 턴에서 목록을 읽은 뒤 **발견된 모든 미해결 문제의 작업을 동시에 시작**한다.
각 문제는 네이티브 대화 ID, Chrome 세션, 작업 폴더를 가진다.
JSONL/exec 연결은 문제 턴마다 프로세스를 실행한다.
독립 감시 턴이 사이트에서 새 문제와 직접 제출해 푼 문제를 기본 15초 간격으로 확인한다.
`--scan-seconds`로 간격을 조절하고, 자원 제한이 필요하면 `--max-workers N`을 명시한다.
`--max-turns`는 전체 발견·감시·풀이 턴의 합계이며, 생략하면 계속 실행한다.
idle 문제는 기본 60초 뒤 재시도한다.
`--stop-when-solved`는 **현재 발견된 문제**가 모두 풀렸을 때 종료하는 선택 사항이다.
기본 동작은 새 문제 확인을 계속한다. 인증·사용량 제한·정책 거절 시에는 실행을 중단한다.

사용자가 직접 푼 문제도 입력할 수 있다. 키는 `live status --details`의
`challenge_key`를 사용한다. 여러 문제는 `--challenge`를 반복한다.

```bash
rtk proxy env PYTHONPATH=src python3 -m ctfh_core live mark-solved \
  --state .ctfh-live/h4ckinggame --challenge stable-key
```

명령을 실행하면 문제 상태가 `solved`로 기록되고 해당 문제의 실행·브라우저·제출
헬퍼가 중지된다. 다른 문제의 작업은 계속된다. 사용자가 사이트에 직접 플래그를
제출한 경우에는 감시 턴이 실제 사이트의 완료 표시를 읽어 같은 처리를 한다.
수동 완료는 `solved_source=user`, 사이트 감지는 `solved_source=site`로 남는다.
수동 완료를 특정 후보의 정답 판정으로 기록하지는 않는다.

사용자가 로그인 인계를 마친 뒤 `--resume-handoff`로 재개한다. 인계 대기 중 일반 재실행은
브라우저를 다시 점유하지 않는다. 연결 자체를 바꾸거나 네이티브 스레드를 새로 만들려면
`--reset-thread`를 사용한다. 문제 기록·작업 폴더·제출 기록은 유지된다.

장시간 실행은 사용자 서비스로 관리한다. 같은 상태를 두 컨트롤러가 동시에 소유할 수 없다.

```bash
rtk proxy systemd-run --user --unit=ctfh-live --working-directory="$PWD" \
  --setenv=PATH --setenv=CODEX_HOME --setenv=PYTHONPATH=src \
  python3 -m ctfh_core live run --state .ctfh-live/h4ckinggame
rtk proxy systemctl --user stop ctfh-live.service
```

Codex 설정 경로를 사용하지 않는 연결에서는 `--setenv=CODEX_HOME`을 생략해도 된다.

## 3. 연결 프로세스 규약: ctfh.turn.v1

컨트롤러는 발견, 감시, 각 문제 턴마다 연결 프로세스를 하나 실행한다. 여러 문제
프로세스는 동시에 실행될 수 있다. 표준 입력으로 JSON 한 줄을 주고 닫는다.

| 필드 | 내용 |
|---|---|
| `protocol` | `ctfh.turn.v1` |
| `thread_id` | 이전에 저장한 네이티브 스레드 ID 또는 null |
| `state_dir` | 현재 대회의 절대 상태 경로 |
| `prompt` | intent-gate 요약, 실행 범위, 문제 배정, 브라우저·제출 명령, 이전 체크포인트 |
| `snapshot` | `turn_id`, `turn_role` (`discovery`/`monitor`/`solver`), `assigned_challenge`, 문제 목록, 후보 상태, 진행 기록 |
| `report_schema` | 최종 보고서 JSON Schema |

연결 구현은 사용자의 Codex를 호출하고 `prompt`와 `report_schema`를 전달한다.
기존 스레드가 있으면 재개하고 새 스레드 ID를 얻으면 아래 이벤트를 즉시 출력한다.
같은 감시 세션 또는 같은 문제에서 ID가 임의로 바뀌면 거절된다. 문제 사이의
ID는 서로 달라야 한다. 출력 이벤트는 JSONL이며 각 줄을 flush한다.

```json
{"type":"thread.started","thread_id":"native-thread-id"}
{"type":"progress","message":"첨부 파일의 구조를 확인하고 있습니다."}
{"type":"turn.report","report":{"outcome":"idle","note":"새 문제 없음","challenges":[],"submissions":[],"work":[]}}
```

`turn.report`는 정확히 한 번만 보낸다. 프로세스는 정상 종료해야 한다.
`progress.message`는 선택 항목이다. 기존 `{"type":"progress"}`도 지원한다.
실패하면 `{"type":"turn.failed","kind":"auth"}`처럼 출력한다.
`kind`는 `auth`, `quota`, `cyber_safety`, `timeout`, `agent_failed`를 사용한다.
`turn.failed`는 종료 코드 0이어도 실패이며, 이전에 출력한 보고서를 적용하지 않는다.
표준 출력에는 임의 로그를 섞지 않는다. stderr는 모델 출력이나 비밀을 저장하지 않기 위해
컨트롤러가 보관하지 않는다. `--turn-timeout`을 넘기면 연결 프로세스를 종료한다.

기존 `codex exec` 연결도 남아 있다. 별도 연결 명령이나 엔진을 설정하지 않으면 이 경로를 사용한다.
실제 Codex 계정 연결과 그 모델의 풀이 성능은 사용자가 연결한 뒤 검증할 항목이다.

## 4. 보고서와 문제 상태

- `outcome`: `progress`, `idle`, `needs_human` 중 하나. `needs_human`은 사용자 로그인 인계에 사용한다.
- `challenges`: `key`, `title`, `url`, `state`, `site_evidence`. `state`는 `open`, `solved`, `unknown`.
- `submissions`: `challenge_key`, `flag_sha256`, `verdict`, `site_evidence`, `observed_url`.
  `verdict`는 `accepted`, `rejected`, `unknown`.
- `work`: `challenge_key`, `note`, `next_action`, `retry_after_seconds`.
  확인된 사실·실패한 접근·다음 행동을 기록한다. 재시도 대기는 0~86400초다.

문제 키는 재시작해도 동일해야 한다. `assigned_challenge`가 현재 문제 턴의 배정이고
`next_challenge`는 아직 실행하지 않은 다음 후보다. 작업 폴더는 상태 디렉터리 내부의 해시 경로로
고정된다. 같은 문제의 작업은 하나만 실행된다. 대기 중인 제출의 판정 확인이 우선이며,
나머지 미해결 문제는 모두 시작한다. 해결된 문제는 다시 배정하지 않는다.
상태 표시의 전체 문제 수는 프롬프트의
100개 요약 한도와 별도로 계산한다.

## 5. 브라우저 관찰과 제출

프롬프트에 이번 턴의 명령을 넣어 준다. 다음은 형태만 보여 주는 예시다.

```bash
rtk proxy env PYTHONPATH=src python3 -m ctfh_core live browser \
  --state .ctfh-live/h4ckinggame --turn 7 -- snapshot -i
rtk proxy env PYTHONPATH=src python3 -m ctfh_core live submission-begin \
  --state .ctfh-live/h4ckinggame --turn 7 --challenge stable-key --sha256 CANDIDATE_SHA256
```

브라우저 헬퍼가 해당 턴에 배정된 chrome-use 세션을 사용한다. URL과 출력을 턴에 묶은 임시 관찰 파일로
수집하므로 Codex의 도구 이벤트 형식에 의존하지 않는다. 실제 `read`, `get text`,
`click ... --observe` 출력을 확인한 뒤 보고서의 `site_evidence`에 짧은 원문을 넣고
`observed_url`에는 당시 실제 URL을 넣는다. 제출 판정은 같은 대회 origin, 같은 URL,
해당 후보를 원장에 기록한 이후의 관찰과 일치해야 한다.

후보를 제출하기 **전에** SHA-256을 기록한다. 응답이 `new`일 때만 제출한다.
다른 응답은 이미 제출됐거나, 문제를 풀었거나, 확인되지 않은 후보가 있다는 뜻이다.
서로 다른 후보도 앞선 제출의 판정이 불명확하면 추가로 제출할 수 없다.
제출 후 중단돼도 원장을 남기고 다음 턴에서 사이트 판정을 먼저 확인한다.

관찰 파일은 private 상태 경로에 잠시 저장하고 턴 종료·실패·재시작 시 삭제한다.
DB에는 비밀번호나 플래그 원문을 의도적으로 저장하지 않는다. 에이전트가 자유 서술 필드에
비밀 값을 쓰지 않아야 하며 네이티브 모델 세션/문제 작업 파일은 별도의 보존 영역이다.
이 구조는 관찰 없는 판정을 막지만, 화면 문구를 정답·오답으로 해석하는 모델의 판단까지
독립적으로 증명하는 보안 경계는 아니다.

## 6. 검증

```bash
rtk pytest -q -o pythonpath=src
rtk pytest -q -o pythonpath=src tests/test_live_start.py tests/test_live_events.py tests/test_live_parallel.py tests/test_live_core.py tests/test_live.py
rtk proxy python3 tests/manual_live_core.py --state .ctfh-live/fresh-integration
rtk proxy python3 tests/manual_live_parallel.py --state .ctfh-live/fresh-parallel-integration
```

두 수동 명령은 각각 새 상태 경로와 연결된 chrome-use가 필요하다. localhost 시험 사이트와
정해진 시험 연결기를 사용한다. 실제 Chrome에서 로그인, 설명 읽기, 첨부 해석, 오답 처리,
두 문제의 정답, 제출 직후 중단과 재개, 중복 제출 방지, 다른 화면 구성의 점수판을 확인한다.
시험 끝에 `integration-result.json`을 남기고 브라우저 시험 세션과 서버를 닫는다.
`manual_live_parallel.py`는 두 문제 작업이 동시에 시작됐는지, 사용자가 사이트에 직접 제출한
정답을 감지가 반영하는지, 그 문제만 중지되고 다른 문제는 계속되는지, 명령으로
다른 문제를 완료 처리할 수 있는지 확인하고 `parallel-integration-result.json`을 남긴다.
소요 시간이 실행 도구 제한을 넘는 환경에서는 같은 명령을 유한 systemd 작업으로 실행한다.

이 검증은 CTF 실행부와 연결 인터페이스의 동작을 확인한다. 실제 모델의 임의 문제 풀이
성공률이나 실제 사이트에서의 무인 운영을 대신 증명하지는 않는다.

선택한 Codex CLI를 통한 재개·동시 실행·문제별 중단은 로컬 시험 연결기로 확인한다.
실제 계정의 모델 턴과 외부 CTF 풀이는 아직 실행하지 않았다. 최신 결과와 전체 회귀 검사의
제약은 `docs/reviews/v3/FINAL_IMPLEMENTATION_STATUS.md`에 기록한다.

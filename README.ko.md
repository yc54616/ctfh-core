# ctfh-core

[English](README.md) · **한국어**

CTF 문제마다 대화형 Codex 창을 열고, 별도 검사기로 플래그 재현·사이트 제출·풀이 문서 검토를 진행하는 터미널 실행 도구입니다.

`ctfh2`에 대회 URL을 주면 연결된 Chrome으로 문제를 수집하고, 첨부파일을 받은 뒤 tmux에 문제별 풀이 창을 엽니다. 진행 상태는 검사기에서 확인하고, 원하는 문제 창에 들어가 Codex와 직접 대화할 수 있습니다.

```text
대회 URL → Chrome으로 문제 수집 → 문제별 폴더 → Codex 풀이 창
                                                   ↓
                                검사기 → 재현 → 제출 → 풀이 문서 검토
```

이 문서는 기본 실행 방식인 **네이티브 실행기**(`ctfh2 <URL>`)를 설명합니다. 이전 `start` / `live` 방식과 격리된 벤치마크 실행기는 별도 기능입니다. [고급 기능](#고급-기능)에서 구분해 안내합니다.

## 목차

- [준비물](#준비물)
- [설치](#설치)
- [첫 대회 실행](#첫-대회-실행)
- [검사기 화면 사용법](#검사기-화면-사용법)
- [분리·중단·재개](#분리중단재개)
- [명령어와 설정](#명령어와-설정)
- [저장 파일과 검증 과정](#저장-파일과-검증-과정)
- [문제 해결](#문제-해결)
- [고급 기능](#고급-기능)
- [개발 및 테스트](#개발-및-테스트)

## 준비물

| 항목 | 필요한 상태 |
|---|---|
| 운영체제 | 현재 개발 환경은 Linux입니다. 메모리 제어에 `/proc`과 systemd 사용자 scope를 사용합니다. 다른 운영체제는 검증하지 않았습니다. |
| Python | **3.12 이상**, `venv`와 pip 사용 가능. |
| tmux | 설치되어 있고 `PATH`에서 실행 가능. |
| Codex CLI | 설치·로그인이 완료되어 있고 선택한 모델을 사용할 수 있어야 합니다. 네이티브 실행기는 `--no-daemon`, `--dangerously-bypass-approvals-and-sandbox`, `codex exec --json --output-schema`를 지원하는 CLI가 필요합니다. |
| Chrome + chrome-use | 대회에 접속할 수 있는 Chrome 프로필이 열려 있고 [chrome-use](https://github.com/leeguooooo/chrome-use)에 연결된 상태. |
| 모델 사용량 | 문제별 풀이뿐 아니라 수집·다운로드·제출·검토 작업도 Codex 계정의 사용량을 소모합니다. |

네이티브 실행기는 백그라운드 Codex 작업을 포함하여 **승인·샌드박스 우회 옵션으로 Codex를 실행**합니다. 문제 코드를 실행하고 현재 사용자에게 허용된 파일에 접근할 수 있습니다. `ctfh.toml`의 `dangerously_bypass_approvals_and_sandbox = false`는 **이 실행기의 샌드박스를 켜는 설정이 아닙니다**. CTF 코드를 실행해도 되는 환경에서 허가된 문제에 사용하고, 대회의 자동화·AI 이용 규칙을 따라야 합니다.

RTK는 CTFH 실행에 필수가 아닙니다. 아래 명령은 RTK 없이 사용할 수 있습니다.

## 설치

### 1. 프로젝트 설치

Python 3.12 이상을 사용할 수 있는 Ubuntu/Debian 기준입니다.

```bash
sudo apt update
sudo apt install git python3 python3-venv python3-pip tmux

git clone https://github.com/yc54616/ctfh-core.git
cd ctfh-core
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

이 프로젝트 폴더의 `ctfh.toml`과 벤치마크 파일을 읽으므로 **편집 가능 설치(`-e .`)**를 사용합니다. 설치하면 `ctfh2`와 `ctfh-learn` 명령이 생깁니다. 의존성을 설치한 뒤에는 저장소 루트에서 `./ctfh2`로 실행해도 됩니다. 새 터미널에서는 가상환경을 다시 활성화하세요.

### 2. Codex와 Chrome 준비

1. [Codex CLI](https://github.com/openai/codex)를 설치하고 로그인합니다. `codex login status`가 성공하는지, 도움말에서 위 필수 옵션을 지원하는지 확인합니다.
2. [chrome-use 설치 안내](https://chrome-use.leeguoo.com/en/install.html)에 따라 CLI와 Chrome 확장 프로그램을 설치합니다. `chrome-use extension install`로 로컬 연결을 등록합니다.
3. 연결한 Chrome 프로필을 열어 두고 CTF 사이트에 로그인합니다. Codex 환경에서도 chrome-use를 사용할 수 있어야 합니다. 사용 안내는 `chrome-use skills get core`로 확인할 수 있습니다.

대회를 시작하기 전에 다음 명령으로 준비 상태를 확인합니다.

```bash
python --version
tmux -V
codex login status
chrome-use status
ctfh2 --help
ctfh2 go --help
```

pip는 Python 의존성을 설치합니다. Codex, tmux, Chrome, chrome-use와 디버거 등 문제별 도구는 별도로 준비해야 합니다.

## 첫 대회 실행

`https://ctf.example.com/challenges`를 실제 대회 URL로 바꾸세요. 처음에는 풀이 창을 두 개로 제한하고 자동 제출을 끈 상태로 시작할 수 있습니다.

```bash
ctfh2 https://ctf.example.com/challenges \
  --dir "$HOME/CTF/my-contest" \
  --max-parallel 2 \
  --no-submit
```

기본 모델을 사용할 수 없는 계정이라면 `--model YOUR_MODEL --rescan-model same`을 추가하세요. `--thinking high`처럼 해당 모델이 지원하는 추론 강도를 지정할 수도 있습니다.

실행하면 다음 순서로 진행됩니다.

1. 대회 폴더를 출력합니다. 터미널에서는 재로그인에 쓸 사이트 아이디·비밀번호를 선택적으로 물어봅니다. **아이디 입력에서 Enter를 누르면 저장을 건너뛰고** 기존 Chrome 로그인을 사용합니다.
2. Codex 수집기가 사이트·규칙·문제 목록을 읽습니다. 첫 수집은 몇 분 걸릴 수 있습니다. 로그인 도움이 필요하다고 나오면 Chrome에서 로그인하고 안내에 따라 Enter를 누릅니다.
3. tmux 세션을 만듭니다. 위 폴더를 썼다면 이름은 `ctfh-my-contest`입니다. `checker` 창에는 진행 상태가, 문제별 창에는 대화형 Codex가 표시됩니다.
4. 첨부파일은 백그라운드로 받습니다. 각 문제의 파일이 준비되면 풀이를 시작할 수 있습니다. 다운로드가 세 번 실패하면 불완전한 파일로 풀이를 시작할 수 있습니다.
5. 검사기가 `solve.py`를 실행해 플래그를 재현하고 `writeup.md`를 검토합니다. `--no-submit` 상태에서는 사이트에 플래그를 제출하지 않습니다.

자동 제출까지 사용하려면 `--no-submit`을 빼고 실행합니다.

```bash
ctfh2 https://ctf.example.com/challenges --max-parallel 2
```

**기본값은 자동 제출 켜짐, `--max-parallel 0`입니다.** `0`이면 실행 가능한 모든 문제를 동시에 풀 수 있습니다. 이미 실행 중인 대회에 다시 접속하는 것만으로 검사기 옵션이 바뀌지는 않습니다. 아래 재시작 절차를 따르세요.

## 검사기 화면 사용법

`checker` 창에서 문제별 상태, 토큰 사용량, 활동과 대기 이유를 볼 수 있습니다. 현재 화면과 생성되는 에이전트 지침은 주로 한국어로 표시됩니다.

방향키나 마우스로 문제를 선택한 뒤 사용합니다.

| 키 | 동작 |
|---|---|
| `Enter` | 선택한 문제의 Codex 창으로 이동. |
| `n` | 지금 풀이를 재촉. 사용자가 직접 누른 재촉은 자동 횟수 제한에 포함하지 않음. |
| `r` | 닫힌 풀이 창을 다시 열거나 `manual` / `hold` 문제를 재개. |
| `o` | 해당 문제의 `work/NOTES.md` 열기. |
| `p` | 문제를 보류하고 풀이 창 닫기. `r`로 재개. |
| `d` | 본인이나 팀원이 푼 문제로 표시하고 풀이 창 닫기. |
| `s` | 지금 사이트를 재수집. |
| `t` | 다음 열 기준으로 정렬. 열 제목을 클릭해도 정렬 변경 가능. |
| `+` / `-` | 풀이 창 수 제한 조절. 검사기 재시작 후에도 유지되며 설정의 `--max-parallel` 값이 바뀌면 새 값을 적용. |
| `q` / `Ctrl-c` | 검사기 종료. **문제별 풀이 창은 계속 실행됨.** |

위 키는 검사기 화면에서 사용합니다. 문제별 풀이 창에서는 평소처럼 Codex와 대화합니다.

| tmux 기본 단축키 | 동작 |
|---|---|
| `Ctrl-b`를 누른 뒤 `w` | 창 목록 열기. `checker`를 선택하면 검사기로 돌아감. |
| `Ctrl-b`를 누른 뒤 `n` / `p` | 다음 / 이전 창으로 이동. |
| `Ctrl-b`를 누른 뒤 `d` | 화면에서 분리. 대회 작업은 계속 실행됨. |

## 분리·중단·재개

**작업은 계속 두고 화면만 나가기:** `Ctrl-b`를 누른 뒤 `d`를 누릅니다. 다른 터미널에서 가상환경을 활성화한 후 상태를 보거나 다시 접속할 수 있습니다.

```bash
ctfh2 status --dir "$HOME/CTF/my-contest"
ctfh2 --dir "$HOME/CTF/my-contest"
```

인자 없이 `ctfh2`를 실행하면 현재 경로가 속한 대회를 찾고, 없으면 `~/.cache/ctfh/last_contest`에 기록된 마지막 대회를 선택합니다. 저장된 대회가 없는 첫 실행에는 URL이 필요합니다. 여러 대회를 다룬다면 `--dir`로 명시하세요.

**문제 하나만 멈추기:** 검사기에서 문제를 선택하고 `p`를 누릅니다. `hold` 상태는 재시작 후에도 유지되며 `r`로 재개합니다.

**검사기 옵션 바꾸기:** `q`로 검사기를 종료하고 창이 닫힌 뒤, 같은 `--dir`에 원하는 옵션을 붙여 다시 실행합니다. 이미 열린 풀이 창은 기존 모델·설정을 유지하고, 새로 여는 창부터 새 옵션을 사용합니다.

**대회의 tmux 세션 종료하기:** 별도 터미널에서 이름을 확인한 뒤 해당 세션을 종료합니다.

```bash
tmux list-sessions
# 위 --dir "$HOME/CTF/my-contest" 예시에 해당하는 이름입니다.
tmux kill-session -t ctfh-my-contest
```

해당 세션의 검사기와 풀이 창을 닫습니다. 저장된 문제 파일은 남습니다. 문제 도구가 별도로 분리해 실행한 프로세스는 따로 정리해야 할 수 있습니다.

저장된 작업으로 다시 시작하려면 다음처럼 실행합니다. 아래 예시는 첫 시도에서 껐던 자동 제출도 켭니다.

```bash
ctfh2 --dir "$HOME/CTF/my-contest" --max-parallel 2
```

tmux 세션이 이미 실행 중이면 다시 연결만 합니다. 다른 옵션으로 실행해도 기존 검사기 설정을 교체하지 않습니다. 재시작할 때 원하는 옵션을 다시 지정하거나 `ctfh.toml`에 저장하세요.

## 명령어와 설정

### 자주 쓰는 명령어

```bash
ctfh2 https://ctf.example.com/challenges     # 새 대회 시작
ctfh2                                       # 현재/마지막 대회 접속·재개
ctfh2 status                                # 저장된 진행 상태 보기
ctfh2 done web-1 pwn-2                      # 본인·팀원이 푼 문제로 표시
ctfh2 go --help                              # 네이티브 실행 옵션 전체 보기
ctfh2 checker --help                         # 검사기 옵션 보기
```

`web-1`, `pwn-2`는 예시입니다. 실제 `challenges/` 아래 폴더 이름을 넣으세요. `status`, `done`, `approve`에는 `--dir`을 지정할 수 있습니다.

`ctfh2 approve web-1`은 직접 판단한 플래그를 제출 대기 상태로 바꾸는 예외 명령입니다. 현재 `flag.txt`를 신뢰하고 재현 검사를 건너뛰며, 이전에 시도한 플래그도 다시 승인할 수 있습니다. **명령 자체가 즉시 제출하는 것은 아닙니다.** 실행 중인 검사기가 처리하며 검사기가 `--no-submit`이면 제출하지 않습니다. 플래그를 별도로 확인한 경우에만 사용하세요.

### 주요 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--dir PATH` | 새 URL이면 `~/Desktop/CTF/<site-slug>/` | 대회 작업 폴더. |
| `--max-parallel N` | `0` | 최대 풀이 창 수. `0`이면 고정된 창 수 제한 없음. 백그라운드 작업 수는 별개. |
| `--no-submit` | 지정하지 않음 | 사이트 제출을 끄고 재현 검사·풀이 문서 검토만 진행. |
| `--model MODEL` | `gpt-6-sol` | 풀이와 첫 문제 수집에 사용할 모델. |
| `--thinking LEVEL` | 계정 기본값 | 추론 강도. `--reasoning-effort`와 같음. 지원 여부는 모델에 따라 다름. |
| `--rescan-model MODEL` | `gpt-6-luna` | 주기적 재수집·첨부 다운로드 모델. `same`이면 `--model`과 같음. |
| `--rescan-thinking LEVEL` | `low` | 재수집·다운로드 추론 강도. `same`이면 `--thinking`을 따름. |
| `--submit-thinking LEVEL` | `low` | 제출 작업 추론 강도. 모델은 주 모델 사용. |
| `--review-thinking LEVEL` | `medium` | 풀이 문서 검토 추론 강도. 모델은 주 모델 사용. |
| `--rescan-minutes N` | `60` | 전체 문제 목록 재수집 간격. `0`이면 주기적 전체 재수집을 끔. 다른 검사기 확인 작업은 계속됨. |
| `--idle-seconds N` | `30` | 멈춘 풀이 창을 재촉하기 전 대기 시간(초). |
| `--nudge-limit N` | `3` | 사용자에게 넘기기 전 자동 재촉·재시작 최대 횟수. |
| `--solve-timeout N` | `300` | `solve.py` 재현 제한 시간(초). |
| `--memory-max SIZE` | `auto` | systemd로 제한하는 풀이 창 전체의 공유 메모리 상한. `auto`는 RAM의 98%. 예: `8G`, `off`. 창 하나당 제한이 아님. |
| `--plain` | 지정하지 않음 | 대화형 화면 대신 텍스트 상태 표 사용. |
| `--no-attach` | 지정하지 않음 | tmux 세션만 만들고 접속하지 않음. |
| `--secondary` | 지정하지 않음 | 기존 `codex-secondary` 실행 파일 사용. 해당 계정 래퍼는 CTFH가 설치하지 않음. |

기본 계정은 `codex`와 `~/.codex`를 사용합니다. `--secondary`는 CTFH의 옵션이므로 Codex에 직접 넘기는 옵션이 아닙니다. Claude는 격리된 smoke/벤치마크 어댑터에서 지원하며 기본 대회 실행기에서는 사용하지 않습니다.

### 기본값 저장

**이 프로젝트 폴더의** [ctfh.toml](ctfh.toml)을 수정합니다. 명령줄에 지정한 값이 우선합니다. 아래 항목은 `[native]` 아래, **`[native.secondary]`보다 앞에** 둬야 합니다.

```toml
[native]
binary = "codex"
model = "gpt-6-sol"
thinking = "high"
max_parallel = 2
submit = false
rescan_model = "same"
rescan_thinking = "low"
submit_thinking = "low"
review_thinking = "medium"
rescan_minutes = 60
idle_seconds = 30
nudge_limit = 3
solve_timeout = 300
memory_max = "8G"
plain = false
```

위 내용은 처음 사용할 때의 설정 예시이며 출고 기본값과는 다릅니다. 파일에서 제출을 껐다면 `submit = true`로 바꿔야 다시 켜집니다. `submit = false`를 뒤집는 `--submit` 옵션은 없습니다. 수정 후 검사기를 재시작하세요. 대회 폴더의 `contest.toml`은 그 대회 정보를 담는 파일이며 실행기 기본값 파일과 다릅니다.

## 저장 파일과 검증 과정

```text
my-contest/
├── contest.toml                 # 사이트 URL과 대회별 설정
├── AGENTS.md                    # 대회 지침
├── .ctfh/                       # 수집 로그, 사이트 정보, 상태 표, 검사기 상태
└── challenges/
    └── web-1/
        ├── challenge.md         # 문제 설명
        ├── challenge.json       # 사이트에서 읽은 문제 정보
        ├── AGENTS.md            # 풀이 지침
        ├── files/               # 첨부파일
        ├── work/NOTES.md         # 진행 기록과 재개 메모
        ├── solve.py             # 재현 가능한 풀이 코드
        ├── flag.txt             # 후보 또는 인정된 플래그
        ├── writeup.md           # 풀이 문서
        ├── status.json          # 검사기가 관리하는 상태
        └── .ctfh-check/          # 재현·검토 기록
```

파일은 해당 단계에 도달해야 생깁니다. 원격 문제에는 `remote.txt`가 있을 수도 있습니다. 대회 작업 폴더와 자격 증명은 공개 소스 저장소에 넣지 마세요.

일반적인 진행은 `solving → checking → verified → submitting → accepted → done`입니다. `accepted`는 사이트가 정답으로 인정한 상태이고 `done`은 풀이 문서 검토까지 마친 상태입니다. 단, `--no-submit`이면 **사이트 정답 확인 없이도** 재현·문서 검토를 거쳐 `done`이 될 수 있습니다.

검사기는 `solve.py`를 다시 실행해 출력과 `flag.txt`를 비교합니다. 원격 문제에서는 알려진 가짜 플래그나 첨부파일에서 복사한 플래그를 걸러내고, 새 원격 플래그가 재현되면 기존 값을 교체할 수 있습니다. 오답으로 확인된 같은 플래그는 자동 재제출하지 않습니다. 제출 결과가 불분명하면 2분 간격으로 최대 세 번 재시도하고, 이후에는 `unknown`으로 남겨 직접 확인하도록 합니다.

로컬 재현 성공만으로 원격 정답을 확정하지 않습니다. 제출 결과를 확인하세요. `done` 명령으로 직접 해결 표시를 했거나 이미 사이트에서 해결된 문제에는 로컬 플래그 파일을 억지로 만들지 않습니다.

### 로그인 유지

우선 연결된 Chrome의 로그인 상태를 사용합니다. 세션이 만료되면 Chrome에 저장된 비밀번호의 자동완성을 시도합니다. 시작 시 선택적으로 받은 재로그인 정보는 `~/.config/ctfh/credentials.toml`에 권한 `600`으로 저장합니다. **암호화 금고가 아닌 평문 파일**입니다. `secret-fill`은 값을 출력하지 않고 `chrome-use fill --from-env`로 전달하지만, 전체 접근 권한으로 실행되는 Codex는 현재 사용자의 파일에 접근할 수 있습니다. 2단계 인증과 캡차는 Chrome에서 직접 처리해야 합니다.

## 문제 해결

| 증상 | 확인할 내용 |
|---|---|
| 명령을 찾지 못함 / `yaml`·`textual` 모듈 없음 | `.venv`를 활성화하고 프로젝트 폴더에서 `python -m pip install -e .` 실행. |
| 대회 폴더를 찾지 못함 | 첫 실행에는 URL을 주고, 저장된 대회는 `--dir`로 지정. |
| Chrome 연결 끊김 / 로그인 필요 | `chrome-use status`, 확장 프로그램, 열린 프로필과 사이트 로그인 확인. 시작 안내를 완료하거나 `s`로 재수집. |
| 모델·추론 강도를 사용할 수 없음 | 계정이 지원하는 `--model` 지정. 기본 재수집 모델도 안 된다면 `--rescan-model same` 사용. |
| Codex가 `--no-daemon` 등의 옵션을 거부함 | `codex --help`, `codex exec --help`를 확인하고 실행기에 필요한 옵션을 지원하는 빌드 사용. |
| `manual` / 직접 확인 | 자동 재촉 한도를 넘었거나 두 번 재촉해도 메모 진전이 없는 상태. `work/NOTES.md`를 읽고 창에 들어가거나 `r` / `n` 사용. |
| `hold` / 보류 | `r`로 재개. 대회 종료 때도 미완료 문제를 보류함. |
| 첨부 다운로드 대기 | `.ctfh/`의 다운로드 로그와 링크 확인. 세 번 실패하면 불완전한 파일로 풀이가 시작될 수 있음. |
| 서버 닫힘 대기 | 원격 서비스 복구를 기다리는 상태. 메모를 확인하고 로컬 결과를 원격 해결로 오인하지 않기. |
| 제출 결과 `unknown` | 사이트 점수판과 `.ctfh-check/` 기록부터 확인하고 `approve` 여부 판단. |
| 계정 한도·서비스 오류 | 검사기가 재촉·재시작·재수집을 멈추고 주기적으로 복구를 확인함. 계정 접근을 복구해야 하며 추론 설정으로 제공자 정책을 우회할 수는 없음. |
| systemd 사용자 scope 오류 | Linux 사용자 systemd 환경을 확인하거나 `--memory-max off`로 메모리 제한 없이 실행. |
| 재개할 때 새 옵션이 적용되지 않음 | 검사기를 재시작하고 TOML의 `[native]` / `[native.secondary]` 위치도 확인. |

## 고급 기능

| 기능 | 시작 명령 | 관련 문서 |
|---|---|---|
| 기본 네이티브 대회 실행기 | `ctfh2 <URL>` | 이 README, [설계 기록](docs/research/NATIVE_CODEX_SOLVER_CHECKER_2026-10-09.md) |
| 이전 live 컨트롤러 | `ctfh2 start <URL>` / `ctfh2 live run` | [live 프로토콜·운영 안내](docs/LIVE_AGENT.md) |
| 격리된 직접/감독 실행 비교 | `ctfh2 smoke` | [구조](docs/ARCHITECTURE.md), [V3 설계](docs/ARCHITECTURE_V3_PROPOSAL.md), [벤치마크 규칙](benchmarks/README.md) |
| 오프라인 증거·지식 관리 | `ctfh-learn --help` | `verify-trace`, `wiki-add`, `wiki-show`, `propose`, `decide` |

`ctfh2 start`로 시작한 작업은 `ctfh2 start`로 재개합니다. 기본 네이티브 실행기와 저장 상태·단축키가 다릅니다. [기존 화면 예시](docs/images/ctfh-terminal-dashboard.svg)는 이전 컨트롤러를 샘플 데이터로 그린 것으로 현재 기본 검사기 화면이 아닙니다.

벤치마크에는 Codex/Claude 어댑터, Bubblewrap 격리, 선택적 PTY/GhostChrome 서비스, 고정된 RunLock 설정, 봉인된 실행 기록, 읽기 전용 스킬·지식 스냅샷이 있습니다. 오프라인 학습은 별도 `ctfh_learning` 패키지입니다. 이 격리 기능이 네이티브 대회 실행기에 적용되는 것은 아닙니다.

smoke 실행은 추가 환경 도구와 사용 가능한 모델 계정이 필요하며 실제 모델 호출을 시작합니다.

```bash
ctfh2 smoke --agent codex --mode pair --model gpt-6-sol --effort high
ctfh2 smoke --help
ctfh-learn --help
```

과거 검증 결과와 제약은 [프로젝트 결정 기록](docs/DECISIONS.md), [날짜별 구현 기록](docs/reviews/v3/FINAL_IMPLEMENTATION_STATUS.md)에 남아 있습니다.

## 개발 및 테스트

가상환경을 활성화한 상태에서 실행합니다.

```bash
python -m pip install pytest
python -m pytest -q -o pythonpath=src
git diff --check
```

자동화 테스트는 네이티브 검사기, live 컨트롤러 상태, 산출물과 학습 규칙을 확인합니다. 별도 `tests/manual_*.py`에는 브라우저·실행 환경 준비가 필요하며 위 명령에 포함되지 않습니다. 로컬 테스트 통과가 실제 대회 풀이 성능이나 현재 모델·계정의 사용 가능 여부를 입증하지는 않습니다.

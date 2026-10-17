# small_AGENT

> **뭐냐:** Claude Code 에 붙여 쓰는 작은 개인 비서. 실제 크롬을 몰고 들어가서 «보고 오고», «조사하고», «매일 훑고», «그림을 뽑아 온다».
> **언제 읽나:** 처음 설치할 때. 뭘 시킬 수 있는지 까먹었을 때.
> **안 담는 것:** 에이전트가 지키는 규칙 → [`CLAUDE.md`](CLAUDE.md) · 밟은 함정 → [`LESSONS.md`](LESSONS.md).

큰 개인 하네스를 두 달 넘게 굴리면서 **실제로 제일 많이 쓴 기능만** 떼어 온 것이다.
세션 기록 300여 개를 세어 보니 압도적 1위가 «크롬으로 직접 들어가서 보기»였고, 그다음이 심부름·리서치·스윕·이미지 생성이었다.
나머지(음성 보고, 클라우드 대시보드, 텔레그램 등)는 다 뺐다.

---

## 할 수 있는 것

| 이렇게 말하면 | 하는 일 |
|---|---|
| `/errand 교보문고 광화문점 오늘 몇 시까지 해?` | 크롬을 몰고 그 페이지에 직접 들어가서 보고 답한다. 검색 요약이 아니다 |
| `/research 이 회사 올해 증설 발표했어?` | 물음을 쪼개 출처를 찾고, 결론마다 **등급**(직접 봄 / 원문 봄 / 요약만 봄 / 확인 못 함)을 붙인다 |
| `/sweep` | `domains/` 의 도메인(학업·취업·주식…)을 전부 훑고, 걸린 것만 `domains/SWEEP.md` 에 올린다 |
| `/gemini-image 픽셀 캐릭터 4장, out/art 에` | 제미나이로 그림을 여러 장 뽑아 PNG 로 받는다. 레퍼런스로 화풍 고정 |
| `/chatgpt-image ...` | 같은 일을 ChatGPT 로 |

다섯 개 전부 바닥에 `module_browser` 하나가 있다 — 크롬 디버그 포트에 **CDP 로 직접** 붙는다(셀레늄보다 75배 빠르고, 서드파티 의존 없음).

> Word 문서(.docx) 만들기는 Claude 에 기본으로 들어 있는 `docx` 스킬을 쓰면 된다 — 여기엔 안 넣었다.

---

## 준비물

- **Windows 10/11** (자격증명 암호화가 Windows DPAPI 기준이다)
- **Python 3.10+** — `python -V` 가 `Python 3.x.y` 를 찍어야 한다. `Python` 한 단어만 나오면 MS 스토어 가짜다 → python.org 에서 설치
- **Google Chrome**
- **Claude Code** — 이 폴더를 열고 쓴다
- (선택) `pip install selenium` — 로그인 판정·자동 입력(`check`·`login`)과 옛 명령 `open`·`shot` 에만 쓴다. `fast` 전부와 `launch`·`status`·`close`·지도는 표준 라이브러리만 쓴다

## 처음 한 번

```powershell
cd small_AGENT

# 1) 디버그 크롬을 띄운다 — 평소 크롬과 별개 프로필이다(포트 9222)
python -m module_browser launch

# 2) 뜬 크롬 창에서 자주 쓰는 사이트(구글·제미나이·ChatGPT 등)에 «사람이 직접» 로그인한다
#    세션은 그 프로필에 남는다. 에이전트는 로그인을 대신 하지 않는다.

# 3) 잘 붙나 본다
python -m module_browser status
python -m module_browser fast recipe hacker_news 프론트     # 해커뉴스 1면 30개가 나오면 성공

# 4) Claude Code 로 이 폴더를 열고
#    /errand 해커뉴스 1등 글 뭐야?
```

### 도메인 만들기 (`/sweep` 을 쓸 거면)

```powershell
mkdir domains\study
copy _TEMPLATE\DOMAIN.md domains\study\DOMAIN.md     # 네 칸을 사람 말로 채운다
type nul > domains\study\notes.md
```

`DOMAIN.md` 의 «매일 뭘 봐야 하나»에 적힌 것만 한다. 안 적힌 건 안 한다. `domains/` 는 커밋되지 않는다.

### 로그인이 필요한 사이트 (선택)

```powershell
copy module_browser\sites.sample.json %USERPROFILE%\.agent_creds\sites.json   # 폴더가 없으면 만든다
Get-Clipboard | python -m module_browser set-cred <이름>   # 비밀번호는 클립보드에서 바로, DPAPI 로 암호화
python -m module_browser check <사이트>                      # 로그인 살아 있나
```

자격증명은 **리포 밖**(`%USERPROFILE%\.agent_creds\`)에만 있고, 에이전트 대화에 값이 실리지 않는다.
구글·네이버처럼 봇 감지가 센 곳은 `mode: manual` — 사람이 한 번 로그인하고, 에이전트는 «세션이 살아 있나»만 본다.

---

## 🔴 시행착오를 쌓는 법 — 이 리포가 자라는 유일한 길

에이전트가 새 함정을 밟거나, 예상과 다르게 굴거나, 사람이 바로잡아 주면
**그 작업을 끝내는 같은 턴에** [`LESSONS.md`](LESSONS.md) 에 이렇게 한 항목을 적는다(`CLAUDE.md` 원칙 5):

```markdown
### 2026-10-20 · 로그인 버튼을 눌렀는데 아무 일도 안 일어났다
- **증상:** ...
- **원인:** ...
- **대응:** 다음엔 이렇게 한다
```

- 다음 세션은 크롬으로 나가기 전에 이 파일을 훑으니, **같은 벽에 두 번 안 박는다.**
- 같은 걸 또 밟으면 제목 끝에 `(또: 날짜)` — 두 번 밟은 건 구조라서 코드나 스킬 절차에 막는 장치를 넣는다.
- 처음부터 28개가 들어 있다 — 원본 하네스에서 실제로 밟은 것 중 어느 PC 에서나 똑같이 밟을 것만 골랐다.
- 새 사이트는 `module_browser/site_map.json` 에, 같은 심부름을 두 번 하게 되면 그 사이트의 `recipes` 에 스텝으로 적는다.

사람이 직접 적어도 된다. «이건 이렇게 해라»를 말로 해 주면 에이전트가 받아 적는다.

---

## 폴더

```
small_AGENT/
├─ CLAUDE.md                 에이전트 규칙 (원칙 5개)
├─ LESSONS.md                시행착오 기록장  ← 계속 자란다
├─ module_browser/           크롬 조종 (CDP 직통 · 지도 · 로그인)
│   ├─ _fast.py              빠른 경로: tabs/newtab/goto/eval/text/links/shot/run/recipe
│   ├─ _chrome.py            launch/status/close + 셀레늄 붙기
│   ├─ _login.py             자격증명(DPAPI) · 로그인 판정
│   ├─ _sitemap.py           map/find/go/catalog
│   ├─ site_map.json         사이트 지도  ← 계속 자란다
│   └─ sites.sample.json     로그인 판정 샘플
├─ .claude/skills/           errand · research · sweep · gemini-image · chatgpt-image
├─ _TEMPLATE/DOMAIN.md       도메인 한 장 틀
├─ domains/                  (커밋 안 됨) 내 도메인들 + SWEEP.md
└─ research/                 (커밋 안 됨) 조사 보고서
```

## 명령 한눈에

```powershell
python -m module_browser status | launch [포트] | close [포트]
python -m module_browser find <낱말> | go <사이트> [작업] | map [open|session|api|blocked]
python -m module_browser fast tabs | newtab <URL>
python -m module_browser fast goto <URL> --until "<JS>" --tab <탭>
python -m module_browser fast text|links|alts|eval|shot --tab <탭>
python -m module_browser fast run - --tab <탭> < steps.json      # 여러 스텝을 한 방에
python -m module_browser fast recipe <사이트> <이름> [--all|--json]
python -m module_browser set-cred <이름> | list-creds | sites | check <사이트> | login <사이트>
```

## 지키는 선

- **되돌릴 수 없는 건 사람이 누른다** — 제출·결제·전송·삭제. 에이전트는 «사람이 해야 함»으로 올리기만 한다.
- **캡차·429 가 뜨면 그날 그 사이트는 접는다.** 뚫지 않는다. 계정이 잠기는 게 훨씬 비싸다.
- **대량 수집·대량 생성 안 한다.** 약관 위반이고 계정이 걸린다.
- ChatGPT·제미나이 자동화는 각 서비스 약관상 회색이다 — 본인이 시킨 것만, 적은 양으로.

---

## 변경 이력
- 2026-10-18 — 최초 작성.

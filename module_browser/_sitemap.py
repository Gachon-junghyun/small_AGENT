"""module_browser 지도 레이어 — "그 정보가 어디 있고, 거기서 뭘 할 수 있나".

`_login.py` 가 "로그인 됐나?" 를 답한다면 여기는 **"어디로 가서 뭘 하나?"** 를 답한다.
심부름을 받았을 때 사이트를 뒤지기 전에 `find_site("과제")` 로 목적지 URL 을 먼저 얻게 하는 게
목적이다. 검색엔진을 한 바퀴 돌고 오는 것보다 싸고, 무엇보다 **가면 안 되는 곳을 미리 안다.**

세 가지가 설계의 전부다:

1. **`access` 가 행동을 정한다.** open=그냥 간다 / session=로그인 판정 먼저 /
   api=브라우저 쓰지 마라(`prefer` 에 더 싼 경로가 있다) / blocked=하지 마라(`why` 에 이유).
   이 네 글자를 안 보고 브라우저를 띄우면 캡차·레이트리밋·약관 위반으로 계정이 날아간다.
2. **`can` 은 되돌릴 수 없는 것의 표시다.** `has`(볼 수 있는 것)만 있으면 읽기 전용,
   `can`(할 수 있는 것)에 닿으면 실행 직전에 사람에게 묻는다 (되돌릴 수 없는 건 사람이 한다).
3. **이 파일엔 비밀이 없다.** 그래서 `sites.json`(자격증명 참조가 있어 repo 밖에 산다)과 달리
   `site_map.json` 은 repo 안에 있다. 경로 재정의는 `AGENT_SITE_MAP`.

셀렉터·URL 은 사이트가 개편하면 깨진다. 그래서 코드가 아니라 JSON 이고, 깨진 걸 발견하면
**그 자리에서 JSON 을 고치는 게 규칙**이다 — 안 고치면 다음에 같은 벽에 또 박는다.
판단 근거와 실측 함정은 리포 루트의 `LESSONS.md`.

    python -m module_browser map              # 전체
    python -m module_browser map session      # 1회 로그인하면 활동 가능한 곳만
    python -m module_browser find 뉴스
    python -m module_browser go hacker_news 홈
    python -m module_browser catalog session  # 프롬프트에 넣을 압축본
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCESS = ("open", "session", "api", "blocked")


def map_path(path=None) -> Path:
    if path:
        return Path(path)
    return Path(os.environ.get("AGENT_SITE_MAP", HERE / "site_map.json"))


def load_map(path=None) -> dict:
    """`_` 로 시작하는 주석 키를 걸러낸 {사이트키: 항목}."""
    raw = json.loads(map_path(path).read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def get_site(site: str, sites: dict | None = None, path=None) -> dict:
    sites = sites if sites is not None else load_map(path)
    if site not in sites:
        raise KeyError(f"'{site}' 없음. 등록된 것: {sorted(sites)}")
    return sites[site]


def _haystack(key: str, e: dict) -> str:
    """검색 대상 텍스트. note/why 는 일부러 뺀다 — 주의사항 단어로 오탐되면 안 되니까."""
    parts = [key, e.get("label", ""), *e.get("has", []), *e.get("can", []),
             *e.get("go", {}).keys()]
    return " ".join(parts).lower()


def find_site(query: str, sites: dict | None = None, path=None) -> list[tuple[str, dict, int]]:
    """공백으로 나눈 단어가 몇 개 걸리는지로 점수 매겨 정렬. blocked 는 뒤로 민다."""
    sites = sites if sites is not None else load_map(path)
    words = [w for w in query.lower().split() if w]
    hits = []
    for key, e in sites.items():
        hay = _haystack(key, e)
        score = sum(1 for w in words if w in hay)
        if score:
            if e.get("access") == "blocked":
                score -= 10
            hits.append((key, e, score))
    return sorted(hits, key=lambda t: (-t[2], t[0]))


def site_url(site: str, task: str | None = None, sites: dict | None = None, path=None) -> str:
    """목적지 URL. task 를 안 주면 첫 번째 것. 부분 일치도 받는다."""
    e = get_site(site, sites, path)
    urls = e.get("go") or {}
    if not urls:
        raise KeyError(f"'{site}' 에는 go URL 이 없다"
                       + (f" — 대신: {e['prefer']}" if e.get("prefer") else ""))
    if task is None:
        return next(iter(urls.values()))
    if task in urls:
        return urls[task]
    t = task.lower().replace(" ", "")
    for k, v in urls.items():
        if t in k.lower().replace(" ", ""):
            return v
    raise KeyError(f"'{site}' 에 '{task}' 없음. 있는 것: {sorted(urls)}")


def catalog(access: str | None = None, sites: dict | None = None, path=None) -> str:
    """프롬프트에 넣을 압축본. blocked 는 '왜 안 되는지' 만 남긴다."""
    sites = sites if sites is not None else load_map(path)
    lines = []
    for key, e in sorted(sites.items()):
        acc = e.get("access", "?")
        if access and acc != access:
            continue
        if acc == "blocked":
            lines.append(f"- {key} [금지]: {e.get('why', '')[:80]}")
            continue
        bits = [f"- {key} ({e.get('label', key)}) [{acc}]"]
        if e.get("has"):
            bits.append("  볼 수 있는 것: " + ", ".join(e["has"]))
        if e.get("can"):
            bits.append("  할 수 있는 것: " + ", ".join(e["can"]))
        if e.get("go"):
            bits.append("  바로가기: " + " | ".join(f"{k}={v}" for k, v in e["go"].items()))
        if e.get("prefer"):
            bits.append("  더 나은 경로: " + e["prefer"])
        lines.append("\n".join(bits))
    return "\n".join(lines) if lines else "(해당 없음)"


# ── CLI ────────────────────────────────────────────────────────────
def _usage():
    print("지도:  map [open|session|api|blocked]   # 목록")
    print("       find <검색어...>                 # 어디로 가야 하나")
    print("       go <사이트> [작업]               # 목적지 URL")
    print("       catalog [open|session|api|blocked]  # 프롬프트용 압축본")


def main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        _usage()
        return 0
    cmd = argv[0]
    try:
        sites = load_map()
    except FileNotFoundError as e:
        print(f"site_map 이 없다: {e}")
        return 3

    if cmd == "map":
        want = argv[1] if len(argv) > 1 else None
        if want and want not in ACCESS:
            print(f"access 는 {ACCESS} 중 하나")
            return 2
        for key, e in sorted(sites.items()):
            acc = e.get("access", "?")
            if want and acc != want:
                continue
            print(f"  {key:20s} [{acc:7s}] {e.get('label', '')}")
        return 0

    if cmd == "find":
        if len(argv) < 2:
            _usage()
            return 2
        hits = find_site(" ".join(argv[1:]), sites)
        if not hits:
            print("(없음 — 지도에 없는 사이트면 그냥 가되, 끝나고 site_map.json 에 등록해라)")
            return 1
        for key, e, score in hits:
            acc = e.get("access", "?")
            print(f"  {key:20s} [{acc:7s}] {e.get('label', '')}  (일치 {score})")
            for k, v in (e.get("go") or {}).items():
                print(f"      {k}: {v}")
            if e.get("prefer"):
                print(f"      → 더 나은 경로: {e['prefer']}")
            if e.get("why"):
                print(f"      → 금지 이유: {e['why']}")
        return 0

    if cmd == "go":
        if len(argv) < 2:
            _usage()
            return 2
        try:
            print(site_url(argv[1], argv[2] if len(argv) > 2 else None, sites))
        except KeyError as e:
            print(e.args[0])
            return 1
        return 0

    if cmd == "catalog":
        print(catalog(argv[1] if len(argv) > 1 else None, sites))
        return 0

    print(f"unknown command: {cmd}")
    _usage()
    return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    sys.exit(main(sys.argv[1:]))

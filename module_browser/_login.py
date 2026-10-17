"""module_browser 로그인 레이어 — 로그인 세션을 유지·복구한다.

왜 필요한가: 디버그 프로필에 손으로 로그인해두면 쿠키는 남지만, **만료되는 순간 무인 봇이
그냥 죽는다.** 여기가 그 구멍을 메운다 — "로그인 됐나 확인하고, 안 됐으면 되살리고,
못 되살리면 사람을 부른다".

설계상 중요한 세 가지:
1. **자격증명이 호출자(=LLM) 컨텍스트에 안 들어온다.** 호출자는 `ensure_login(d, "gmail")`
   만 부르고, 실제 값은 이 모듈 안에서 읽어서 곧장 CDP 로 타이핑된다. 로그·예외 메시지에도
   값은 절대 안 실린다.
2. **argv 로 비밀을 안 넘긴다.** 프로세스 커맨드라인은 같은 머신의 아무 프로세스나 볼 수 있다.
   저장은 stdin, 조회는 파일/env 로만.
3. **레시피는 코드가 아니라 데이터.** 셀렉터는 사이트가 개편하면 깨진다. JSON 으로 빼두고
   깨지면 소비자(봇)가 사람에게 알리게 한다.

주의 — 이 모듈은 *유출* 위험만 줄인다. *권한*은 안 줄인다. login("gmail") 을 부를 수 있는
에이전트는 gmail 전권을 가진 것과 같다. 쓰기 액션 게이트는 소비자 쪽에서 따로 걸어라.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import _chrome as B

# 자격증명과 레시피는 repo 밖에 둔다 — 실수로 commit 되는 경로 자체를 없앤다.
DEFAULT_CRED_DIR = Path(os.path.expandvars(r"%USERPROFILE%")) / ".agent_creds"

# DPAPI: 현재 Windows 사용자 계정에 묶여 암호화된다. 파일만 훔쳐가도 다른 계정에선 못 푼다.
_PS_WRITE = (
    "$ErrorActionPreference='Stop';"
    "$v=[Console]::In.ReadToEnd().TrimEnd([char]13,[char]10);"
    "ConvertTo-SecureString $v -AsPlainText -Force | ConvertFrom-SecureString |"
    "Out-File -FilePath $env:CRED_FILE -Encoding utf8 -NoNewline"
)
_PS_READ = (
    "$ErrorActionPreference='Stop';"
    "$s=(Get-Content -Raw -Path $env:CRED_FILE).Trim() | ConvertTo-SecureString;"
    "$b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s);"
    "[Console]::Out.Write([Runtime.InteropServices.Marshal]::PtrToStringAuto($b))"
)


# ── 자격증명 저장소 ────────────────────────────────────────────────
def cred_dir(path: str | Path | None = None) -> Path:
    d = Path(path) if path else Path(os.environ.get("AGENT_CRED_DIR", DEFAULT_CRED_DIR))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ps(script: str, cred_file: Path, stdin: str | None = None) -> str:
    env = {**os.environ, "CRED_FILE": str(cred_file)}
    p = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        input=stdin, capture_output=True, text=True, env=env, timeout=30,
    )
    if p.returncode != 0:
        # stderr 에 값이 실릴 일은 없지만, 방어적으로 파일명만 남긴다.
        raise RuntimeError(f"DPAPI 작업 실패 ({cred_file.name}): rc={p.returncode}")
    return p.stdout


def set_credential(name: str, value: str, path: str | Path | None = None) -> Path:
    """DPAPI 로 암호화해 저장. 실패하면(비-Windows 등) 평문 .txt 로 폴백하고 경고한다.

    🔴 **제어문자가 섞였거나 너무 짧으면 «저장하지 않고» 죽는다** (2026-08-17 에 실제로 당했다).
    윈도우 터미널에서 `Ctrl+V` 가 붙여넣기로 안 먹고 **`\\x16`(SYN) 한 글자**가 그대로
    들어간 적이 있다. 그런데 저장은 성공했고, 그다음부터가 고약하다:
      · 「자격증명 있음」으로 보인다 (값을 안 보니까)
      · 쓰는 쪽은 `URL can't contain control characters` 처럼 **엉뚱한 곳에서** 죽는다
    즉 **틀린 값이 «있는 값»으로 위장한다** — «조용히 틀린 값»의 전형이다.
    붙여넣기가 실패했다는 걸 **저장하는 그 자리에서** 알려주는 게 훨씬 싸다.
    """
    bad = sorted({ch for ch in value if ord(ch) < 32 and ch not in "\r\n\t"})
    if bad:
        raise ValueError(
            f"'{name}' 값에 제어문자가 있다: {[hex(ord(c)) for c in bad]}\n"
            "붙여넣기가 안 먹은 것이다 (Ctrl+V 가 \\x16 으로 들어가는 터미널이 있다).\n"
            "클립보드에서 바로 넣어라:  Get-Clipboard | python -m module_browser set-cred " + name)
    if len(value.strip()) < 4:
        raise ValueError(
            f"'{name}' 값이 너무 짧다({len(value.strip())}자). 붙여넣기가 안 먹었을 가능성이 크다.\n"
            "클립보드에서 바로 넣어라:  Get-Clipboard | python -m module_browser set-cred " + name)
    d = cred_dir(path)
    enc = d / f"{name}.cred"
    try:
        _ps(_PS_WRITE, enc, stdin=value)
        return enc
    except Exception as e:
        plain = d / f"{name}.txt"
        plain.write_text(value, encoding="utf-8")
        print(f"[!] DPAPI 실패({type(e).__name__}) — 평문으로 저장했다: {plain}", file=sys.stderr)
        return plain


def get_credential(name: str, path: str | Path | None = None) -> str:
    """`.cred`(DPAPI) → `.txt`(평문) → 환경변수 순으로 찾는다. 없으면 KeyError."""
    d = cred_dir(path)
    enc = d / f"{name}.cred"
    if enc.exists():
        return _ps(_PS_READ, enc)
    plain = d / f"{name}.txt"
    if plain.exists():
        return plain.read_text(encoding="utf-8").strip()
    if name.upper() in os.environ:
        return os.environ[name.upper()]
    raise KeyError(
        f"자격증명 '{name}' 없음. 저장: python -m module_browser set-cred {name}"
    )


def list_credentials(path: str | Path | None = None) -> list[str]:
    """이름만. 값은 절대 안 돌려준다."""
    d = cred_dir(path)
    names = {p.stem for p in d.glob("*.cred")} | {p.stem for p in d.glob("*.txt")}
    return sorted(names)


def resolve(value: str, path: str | Path | None = None) -> str:
    """레시피의 `@cred:NAME` / `@env:NAME` 를 실제 값으로. 그 외는 리터럴."""
    if value.startswith("@cred:"):
        return get_credential(value[6:], path)
    if value.startswith("@env:"):
        return os.environ[value[5:]]
    return value


# ── 사이트 레시피 ──────────────────────────────────────────────────
def sites_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    if os.environ.get("AGENT_SITES"):
        return Path(os.environ["AGENT_SITES"])
    return cred_dir() / "sites.json"


def load_sites(path: str | Path | None = None) -> dict:
    p = sites_path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"사이트 레시피가 없다: {p}\n"
            f"module_browser/sites.sample.json 을 복사해서 채워라."
        )
    return json.loads(p.read_text(encoding="utf-8"))


def site_names(sites: dict) -> list[str]:
    """실제 사이트 키만. `_` 로 시작하는 키는 파일 안의 주석/메모다."""
    return sorted(k for k in sites if not k.startswith("_"))


def get_recipe(site: str, sites: dict | None = None, path=None) -> dict:
    sites = sites if sites is not None else load_sites(path)
    if site not in sites or site.startswith("_"):
        raise KeyError(f"'{site}' 레시피 없음. 등록된 것: {site_names(sites)}")
    return sites[site]


# ── 로그인 판정 ────────────────────────────────────────────────────
def is_logged_in(driver, recipe: dict, navigate: bool = True) -> bool:
    """`logged_in_when` 의 모든 조건이 참이어야 로그인으로 본다 (AND).

    지원 키: css / css_absent / url_contains / url_not_contains
    """
    cond = recipe.get("logged_in_when") or {}
    if not cond:
        return False  # 판정법을 안 적었으면 "모른다" = 로그인 안 된 것으로 취급

    if navigate and recipe.get("check_url"):
        B.goto(driver, recipe["check_url"])

    url = driver.current_url
    if "url_contains" in cond and cond["url_contains"] not in url:
        return False
    if "url_not_contains" in cond and cond["url_not_contains"] in url:
        return False
    if "css" in cond and not B.present(driver, cond["css"], cond.get("timeout", 5)):
        return False
    if "css_absent" in cond and B.present(driver, cond["css_absent"], cond.get("timeout", 3)):
        return False
    return True


# ── 로그인 실행 ────────────────────────────────────────────────────
def _run_step(driver, step: dict, path=None) -> None:
    action = step.get("action")
    if action == "goto":
        B.goto(driver, step["url"])
    elif action == "type":
        # resolve 결과는 여기서만 존재하고 밖으로 새지 않는다.
        B.type_text(driver, step["css"], resolve(step["value"], path),
                    clear=step.get("clear", True), timeout=step.get("timeout", 10))
    elif action == "click":
        B.click(driver, step["css"], timeout=step.get("timeout", 10))
    elif action == "click_if":
        # 있으면 누르고 없으면 조용히 넘어간다. **조건부 관문**용이다 —
        # 어떤 대학 포털은 로그인 직후 "장기간 비밀번호를 변경하지 않으셨습니다" 모달을 띄우고
        # `다음에 변경`(a.btnNo) 을 눌러야 통과한다. 그런데 이건 정책에 따라 안 뜨는 날도 있어서
        # 그냥 `click` 으로 적으면 **모달이 없는 날 로그인이 통째로 실패**한다. (2026-08-15 실측)
        # ★ `present()`(=DOM 존재)로 판단하면 안 된다 — 그 포털은 모달 마크업을 로그인 화면에도
        # **숨긴 채로** 들고 있어서 `a.btnNo` 가 항상 존재한다(실측: width/height 0).
        # 존재로 걸러 놓고 클릭하면 «보이지 않음» 에서 타임아웃 나 로그인이 통째로 실패한다.
        # 그래서 «클릭 가능해질 때까지 기다려보고 안 되면 넘어간다» 로 짠다.
        try:
            B.click(driver, step["css"], timeout=step.get("timeout", 5))
        except Exception:
            pass
    elif action == "submit":
        B.submit(driver, step["css"], timeout=step.get("timeout", 10))
    elif action == "wait":
        import time
        time.sleep(float(step.get("seconds", 1)))
    elif action == "wait_css":
        B.find(driver, step["css"], timeout=step.get("timeout", 15))
    else:
        raise ValueError(f"알 수 없는 action: {action!r}")


def login(driver, site: str, sites: dict | None = None, path=None, cred_path=None) -> dict:
    """레시피대로 로그인 시도. 반환: {ok, site, mode, needs_human, reason}

    mode='manual' 인 사이트(Google/Naver 등 봇 감지가 걸리는 곳)는 시도조차 안 하고
    needs_human=True 로 즉시 돌려준다 — 계정 잠기는 게 실패보다 비싸다.
    """
    r = get_recipe(site, sites, path)
    out = {"ok": False, "site": site, "mode": r.get("mode", "auto"),
           "needs_human": False, "reason": ""}

    if out["mode"] == "manual":
        out["needs_human"] = True
        out["reason"] = "manual-only 사이트 — 디버그 프로필에서 사람이 직접 로그인해야 한다"
        return out

    # 🔴 스텝을 돌리기 전에 창을 앞으로. 사람이 딴 창을 보고 있으면 크롬이 occlusion 상태가 되고
    # **클릭이 요소에 도달조차 안 한다** — 예외 없이 조용히 실패해서 «비번 틀림»으로 오진한다.
    # 무인보다 **사람이 붙어 있을 때 더 잘 터진다**(2026-08-15 대학 포털 실측). → LESSONS.md
    B.bring_to_front(driver)

    if r.get("login_url"):
        B.goto(driver, r["login_url"])

    for i, step in enumerate(r.get("steps", [])):
        try:
            _run_step(driver, step, cred_path)
        except KeyError as e:
            out["reason"] = f"자격증명 없음: {e.args[0] if e.args else e}"
            out["needs_human"] = True
            return out
        except Exception as e:
            # 값은 절대 안 싣는다 — 어떤 스텝의 어떤 셀렉터가 깨졌는지만.
            out["reason"] = (
                f"step[{i}] {step.get('action')} css={step.get('css')!r} 실패 "
                f"({type(e).__name__}) — 셀렉터가 깨졌을 가능성"
            )
            out["needs_human"] = True
            return out

    out["ok"] = is_logged_in(driver, r)
    if not out["ok"]:
        out["needs_human"] = True
        out["reason"] = "스텝은 다 돌았는데 로그인 판정 실패 — 2FA/CAPTCHA/새기기 인증 의심"
    return out


def ensure_login(driver, site: str, sites: dict | None = None, path=None, cred_path=None) -> dict:
    """소비자가 부르는 진입점. 이미 로그인이면 아무것도 안 한다."""
    r = get_recipe(site, sites, path)
    if is_logged_in(driver, r):
        return {"ok": True, "site": site, "mode": r.get("mode", "auto"),
                "needs_human": False, "reason": "already logged in"}
    return login(driver, site, sites, path, cred_path)


# ── CLI ────────────────────────────────────────────────────────────
def _usage():
    print("usage:")
    print("  python -m module_browser set-cred <name>      # 값은 stdin 으로 (화면에 안 뜸)")
    print("  python -m module_browser list-creds")
    print("  python -m module_browser sites")
    print("  python -m module_browser check <site> [PORT]")
    print("  python -m module_browser login <site> [PORT]")


def main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        _usage()
        return 0
    cmd = argv[0]

    if cmd == "set-cred":
        if len(argv) < 2:
            _usage()
            return 2
        import getpass
        name = argv[1]
        # 파이프로 들어오면 stdin 에서 읽는다. Windows 의 getpass 는 sys.stdin 이 아니라
        # **콘솔을 직접** 읽어서, 파이프를 물려도 무시하고 사람 입력을 기다리다 멈춘다.
        # 그래서 스크립트에서 `echo x | set-cred` 가 안 됐다 — 문서엔 된다고 적혀 있었는데.
        if not sys.stdin.isatty():
            v = sys.stdin.read().strip()
        else:
            v = getpass.getpass(f"{name} 값 (화면에 안 보임): ")
        if not v:
            print("빈 값 — 취소했다.")
            return 2
        p = set_credential(name, v)
        print(f"저장됨: {p}")
        return 0

    if cmd == "list-creds":
        names = list_credentials()
        print(f"{cred_dir()}")
        for n in names:
            print(f"  - {n}")
        if not names:
            print("  (비어 있음)")
        return 0

    if cmd == "sites":
        try:
            s = load_sites()
        except FileNotFoundError as e:
            print(e)
            return 3
        print(f"{sites_path()}")
        for k in site_names(s):
            print(f"  - {k:20s} mode={s[k].get('mode', 'auto')}")
        return 0

    if cmd in ("check", "login"):
        if len(argv) < 2:
            _usage()
            return 2
        site = argv[1]
        port = int(argv[2]) if len(argv) > 2 else B.DEFAULT_PORT
        d = B.ensure(port)
        if cmd == "check":
            r = get_recipe(site)
            ok = is_logged_in(d, r)
            print(f"{site}: {'로그인됨' if ok else '로그인 안 됨'} (url={d.current_url})")
            return 0 if ok else 1
        res = ensure_login(d, site)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 1

    print(f"unknown command: {cmd}")
    _usage()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""module_browser — CDP 디버그 포트로 띄운 Chrome 에 붙어서 조종하는 최소 인프라.

도메인 로직은 여기 넣지 말 것 — 열고/찾고/누르고/입력하고/JS 돌리는 것까지만.

설계상 중요한 두 가지:
- status()/wait_port()/launch() 는 **표준 라이브러리만** 쓴다. selenium 없이도 띄우고 확인 가능.
- attach() 이후만 selenium 이 필요하고, 그건 함수 안에서 lazy import 한다.

launch() 를 파이썬에 둔 이유: .bat 을 따로 호출하면 "런처와 attach 가 한 셸 호출 안에
있어야 한다"는 제약이 생긴다. 같은 프로세스에서 띄우고 기다리면 그게 없어짐.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PORT = 9222

_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)


# ── 붙기 전: 포트 확인 / 실행 (stdlib only) ─────────────────────────
def status(port: int = DEFAULT_PORT, timeout: float = 2.0) -> dict:
    """{'ok': bool, 'browser': str, 'tabs': [{'title','url'}], 'error': str} 반환."""
    base = f"http://127.0.0.1:{port}"
    out = {"ok": False, "browser": "", "tabs": [], "error": ""}
    try:
        with urllib.request.urlopen(base + "/json/version", timeout=timeout) as r:
            out["browser"] = json.loads(r.read().decode("utf-8")).get("Browser", "")
        with urllib.request.urlopen(base + "/json/list", timeout=timeout) as r:
            for t in json.loads(r.read().decode("utf-8")):
                if t.get("type") == "page":
                    out["tabs"].append({"title": t.get("title", ""), "url": t.get("url", "")})
        out["ok"] = True
    except (urllib.error.URLError, OSError, ValueError) as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def wait_port(port: int = DEFAULT_PORT, seconds: float = 15, require_page: bool = True) -> bool:
    """launch() 직후 호출용. 열릴 때까지 폴링.

    `require_page`: 디버그 포트는 **페이지 탭이 하나도 없을 때도 응답한다.** 그 틈에 attach 하면
    셀레늄이 곧 사라질 임시 타겟을 잡아 `NoSuchWindowException` 이 난다(실측). 그래서 기본값은
    "탭이 최소 하나 보일 때까지" 기다린다. 단순히 포트만 확인하려면 False.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        st = status(port, timeout=1.0)
        if st["ok"] and (st["tabs"] or not require_page):
            return True
        time.sleep(0.5)
    return False


def find_chrome(chrome_path: str | None = None) -> str:
    """chrome.exe 경로. 인자 > CHROME_PATH 환경변수 > 표준 설치 경로 > PATH."""
    for cand in (chrome_path, os.environ.get("CHROME_PATH")):
        if cand and Path(cand).exists():
            return cand
    for cand in _CHROME_CANDIDATES:
        if Path(cand).exists():
            return cand
    which = shutil.which("chrome") or shutil.which("google-chrome")
    if which:
        return which
    raise RuntimeError(
        "chrome.exe 를 못 찾았다. CHROME_PATH 환경변수를 설정하거나 chrome_path= 로 넘겨라."
    )


def profile_dir(port: int = DEFAULT_PORT, profile: str | None = None) -> str:
    """디버그 전용 프로필 경로. 포트마다 프로필이 따로라 로그인이 그 포트에 남는다.

    Chrome 136+ 는 *기본* 프로필에서의 원격 디버깅을 거부한다 — 그래서 항상 별도 프로필.
    """
    for cand in (profile, os.environ.get("PROFILE_PATH")):
        if cand:
            return cand
    return os.path.expandvars(rf"%LOCALAPPDATA%\ChromeDebugProfile{port}")


def launch(
    port: int = DEFAULT_PORT,
    url: str = "about:blank",
    profile: str | None = None,
    chrome_path: str | None = None,
    wait: float = 20,
) -> dict:
    """디버그 포트를 열고 Chrome 을 띄운다. 이미 떠 있으면 아무것도 안 하고 반환.

    반환: {'ok', 'port', 'profile', 'launched': bool, 'error'}
    """
    prof = profile_dir(port, profile)
    if status(port, timeout=1.0)["ok"]:
        # 이미 떠 있어도 페이지 탭이 보일 때까지는 기다린다 (attach 안정성 — wait_port 주석 참고)
        ok = wait_port(port, 5)
        return {"ok": ok, "port": port, "profile": prof, "launched": False,
                "error": "" if ok else f"포트 {port} 는 열렸는데 페이지 탭이 안 보임"}

    exe = find_chrome(chrome_path)
    args = [
        exe,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={prof}",
        # 새 프로필 첫 실행 때 뜨는 chrome://intro 창이 없어야 한다. 그게 떠 있으면
        # close() 의 Browser.close 가 먹지 않고, attach 가 엉뚱한 탭을 잡는다.
        "--no-first-run",
        "--no-default-browser-check",
        # ★ 창이 다른 창에 가려지면 Windows 크롬은 그 탭을 occluded 로 보고
        # document.visibilityState 를 'hidden' 으로 만든다. 그러면 antd/React 앱의
        # **포털(드롭다운·모달)이 아예 마운트되지 않는다** — 클릭은 정상으로 도달하는데
        # (elementFromPoint 로 재도 버튼이 최상단) 아무 일도 안 일어나고 에러도 없다.
        # 사람이 딴 창을 보고 있는 사이 자동화가 조용히 죽는 것이라 반드시 꺼둔다.
        # 실측: 광고 관리 화면의 '생성' 버튼들이 전부 무반응이었다 (2026-08-07).
        "--disable-features=CalculateNativeWinOcclusion",
        "--disable-backgrounding-occluded-windows",
        url,
    ]
    # Chrome 이 파이썬보다 오래 살아야 한다 — 부모가 끝나도 안 죽게 분리해서 띄운다.
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)

    ok = wait_port(port, wait)
    return {
        "ok": ok,
        "port": port,
        "profile": prof,
        "launched": True,
        "error": "" if ok else f"{wait}s 안에 포트 {port} 가 안 열림",
    }


def ensure(port: int = DEFAULT_PORT, url: str = "about:blank", **kw):
    """소비자가 부르는 진입점: 필요하면 띄우고, 붙은 driver 를 돌려준다."""
    res = launch(port, url, **kw)
    if not res["ok"]:
        raise RuntimeError(f"Chrome 실행 실패: {res['error']} (profile={res['profile']})")
    return attach(port)


def close(port: int = DEFAULT_PORT, wait: float = 10) -> bool:
    """이 포트의 Chrome 을 종료. 이미 닫혀 있으면 True.

    attach 로 붙은 driver 는 quit() 해도 창이 안 닫힌다 — CDP `Browser.close` 를 써야 한다.
    그런데 종료가 **비동기**라 호출 직후 포트를 보면 아직 살아있다. 그래서 여기서 폴링까지 한다.
    (launch() 는 절대 남의 크롬을 안 죽인다. 종료는 이렇게 명시적으로 부를 때만.)
    """
    if not status(port, timeout=1.0)["ok"]:
        return True
    try:
        from . import _fast            # CDP 직통 — 셀레늄 없이 닫는다
        _fast.close_browser(port)
    except Exception:
        try:
            attach(port).execute_cdp_cmd("Browser.close", {})
        except Exception:
            pass
    deadline = time.time() + wait
    while time.time() < deadline:
        if not status(port, timeout=1.0)["ok"]:
            return True
        time.sleep(0.5)
    return False


# ── 붙기: Selenium attach ──────────────────────────────────────────
def attach(port: int = DEFAULT_PORT):
    """이미 떠 있는 크롬에 붙는다. 새 크롬을 안 띄우고, quit() 해도 창은 안 닫힌다."""
    from selenium import webdriver  # 무거우니 함수 안에서 import
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.debugger_address = f"127.0.0.1:{port}"
    d = webdriver.Chrome(options=opts)
    reset_zoom(d)
    return d


def bring_to_front(driver) -> bool:
    """탭을 활성화하고 창을 앞으로 꺼낸다. **occlusion 을 푸는 유일한 길이다.**

    창이 뒤에 있거나 최소화돼 있으면 클릭이 **요소에 도달조차 안 한다** — 예외도 안 나고
    화면만 그대로라 «셀렉터가 깨졌나»로 오진한다(2026-08-15 대학 포털 실측: 클릭 도달 **0회**,
    `window.outerWidth` 가 **0**). `SetForegroundWindow` 로는 안 풀리고 이건 탭 활성화까지
    같이 해서 크롬의 occlusion 판정을 다시 계산시킨다. → LESSONS.md
    """
    try:
        driver.execute_cdp_cmd("Page.bringToFront", {})
        return True
    except Exception:
        return False


def reset_zoom(driver) -> bool:
    """페이지 줌을 100% 로. 줌이 100%가 아니면 클릭/입력 좌표가 통째로 어긋나서
    send_keys 와 click 이 **예외 없이 조용히 빗나간다**(대학 학사행정 사이트 실측).
    프로필에 줌이 저장돼 있으면 세션 내내 따라다니므로 붙자마자 한 번 편다."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.CONTROL, "0")
        return True
    except Exception:
        return False


# ── 조종: 얇은 헬퍼 ────────────────────────────────────────────────
def goto(driver, url: str, timeout: float = 30):
    driver.set_page_load_timeout(timeout)
    driver.get(url)


def find(driver, css: str, timeout: float = 10):
    """CSS 로 요소 하나. 못 찾으면 TimeoutException."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, css))
    )


def present(driver, css: str, timeout: float = 3) -> bool:
    """find 의 bool 판. 로그인 판정 같은 데 쓴다."""
    try:
        find(driver, css, timeout)
        return True
    except Exception:
        return False


def click(driver, css: str, timeout: float = 10):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    el = WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, css))
    )
    el.click()
    return el


def type_text(driver, css: str, text: str, clear: bool = True, timeout: float = 10):
    """입력 후 value 를 검증한다. 조용히 안 박히는 폼이 있어서(대학 SSO 실측)
    포커스 → send_keys → 그래도 비면 JS 주입 순으로 물러난다."""
    el = find(driver, css, timeout)
    try:
        el.click()          # 포커스 없이 send_keys 하면 먹히는 폼이 있다
    except Exception:
        pass
    if clear:
        try:
            el.clear()
        except Exception:
            pass
    el.send_keys(text)
    if (el.get_attribute("value") or "") == text:
        return el

    # 폴백: 값을 직접 넣고 input/change 를 쏴서 jQuery 바인딩까지 깨운다
    driver.execute_script(
        "arguments[0].value = arguments[1];"
        "arguments[0].dispatchEvent(new Event('input',  {bubbles:true}));"
        "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
        el, text,
    )
    if (el.get_attribute("value") or "") != text:
        raise RuntimeError(f"입력이 필드에 안 박힌다: {css} (키보드 보안 모듈 의심)")
    return el


def submit(driver, css: str, timeout: float = 10):
    """폼 필드에서 Enter. 제출 버튼 셀렉터가 불안정할 때 대안."""
    from selenium.webdriver.common.keys import Keys

    el = find(driver, css, timeout)
    el.send_keys(Keys.RETURN)
    return el


def text_of(driver, css: str, timeout: float = 10) -> str:
    return find(driver, css, timeout).text


def js(driver, script: str, *args):
    return driver.execute_script(script, *args)


def screenshot(driver, path=None, full: bool = False) -> str:
    """지금 화면을 PNG 로. path 를 생략하면 `%TEMP%/shots/shot_<n>.png` 에 알아서 떨군다.

    **막혔을 때 제일 먼저 쓸 도구다.** DOM 텍스트로 더듬는 것보다 한 장이 싸다
    (탭이 겹쳐 숨은 버튼을 누르고 있었던 건 텍스트로는 20번 찔러도 안 보였다).
    full=True 면 스크롤 전체를 찍는다(CDP — 긴 표를 통째로 볼 때).
    """
    if path is None:
        base = Path(tempfile.gettempdir()) / "shots"
        base.mkdir(parents=True, exist_ok=True)
        n = len(list(base.glob("shot_*.png"))) + 1
        path = base / f"shot_{n:03d}.png"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if full:
        m = driver.execute_cdp_cmd("Page.getLayoutMetrics", {})
        css = m.get("cssContentSize") or m["contentSize"]
        shot = driver.execute_cdp_cmd(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": True,
             "clip": {"x": 0, "y": 0, "width": css["width"],
                      "height": css["height"], "scale": 1}},
        )
        path.write_bytes(base64.b64decode(shot["data"]))
    else:
        driver.save_screenshot(str(path))
    return str(path)


def switch_tab(driver, url_contains: str) -> bool:
    """열린 탭 중 URL 에 문자열이 들어간 첫 탭으로 전환. 없으면 False."""
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        if url_contains in driver.current_url:
            return True
    return False


# ── CLI: 동작 확인용 ───────────────────────────────────────────────
def _usage():
    print(__doc__.strip().splitlines()[0])
    print("\nusage:")
    print("  python -m module_browser status [PORT]")
    print("  python -m module_browser launch [PORT] [URL]")
    print("  python -m module_browser open <URL> [PORT]")
    print("  python -m module_browser shot [PATH] [PORT] [--full]   # 지금 화면 PNG")
    print("  python -m module_browser close [PORT]")


def main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        _usage()
        return 0
    cmd = argv[0]

    if cmd == "status":
        port = int(argv[1]) if len(argv) > 1 else DEFAULT_PORT
        st = status(port)
        if not st["ok"]:
            print(f"[X] port {port} closed - {st['error']}")
            print("    'python -m module_browser launch' 먼저.")
            return 3
        print(f"[O] {st['browser']} on port {port}, tabs={len(st['tabs'])}")
        for t in st["tabs"]:
            print(f"    - {t['title'][:60]!r} {t['url'][:80]}")
        return 0

    if cmd == "launch":
        port = int(argv[1]) if len(argv) > 1 else DEFAULT_PORT
        url = argv[2] if len(argv) > 2 else "about:blank"
        res = launch(port, url)
        print(json.dumps(res, ensure_ascii=False))
        if res["ok"] and res["launched"]:
            print("이 창 안에서 로그인해라 — 세션은 이 프로필에 남는다.")
        return 0 if res["ok"] else 3

    if cmd == "close":
        port = int(argv[1]) if len(argv) > 1 else DEFAULT_PORT
        ok = close(port)
        print(f"port {port}: {'closed' if ok else '안 닫힘 (수동 종료 필요)'}")
        return 0 if ok else 3

    if cmd == "open":
        if len(argv) < 2:
            _usage()
            return 2
        url = argv[1]
        port = int(argv[2]) if len(argv) > 2 else DEFAULT_PORT
        d = ensure(port)
        goto(d, url)
        print(f"opened: {d.current_url}")
        print(f"title : {d.title}")
        return 0

    if cmd == "shot":
        args = [a for a in argv[1:] if a != "--full"]
        full = "--full" in argv
        path = args[0] if args and not args[0].isdigit() else None
        port = next((int(a) for a in args if a.isdigit()), DEFAULT_PORT)
        d = attach(port)
        out = screenshot(d, path, full=full)
        print(out)                     # 이 경로를 Read 로 열어서 보면 된다
        print(f"url   : {d.current_url}")
        return 0

    print(f"unknown command: {cmd}")
    _usage()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

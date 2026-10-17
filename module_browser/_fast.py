"""_fast — chromedriver 를 걷어내고 CDP 로 직접 말한다. 심부름용 빠른 경로.

**왜 있나.** `_chrome.attach()` 는 부를 때마다 chromedriver 프로세스를 새로 띄운다 —
실측 **1,567ms**. 그런데 심부름은 스크립트를 여러 번 나눠 돌리는 패턴이라 그 1.5초를
매번 낸다. 여기 CDP 직통은 같은 일을 **21ms** 에 한다(75배). 요소 하나씩 왕복하던 것도
`Runtime.evaluate` 한 방으로 접어서 20개 속성 읽기가 140ms → 0.7ms.

**더 큰 범인은 `time.sleep(6)` 이었다.** "SPA 니까 5~6초 기다려라"를 그대로 박아 넣으면
실제로 0.2초면 뜨는 페이지에도 6초를 낸다. `goto(..., until=...)` 는 **내가 필요한 게
보일 때까지만** 기다린다 — 실측 3개 페이지 18.0s → 0.97s.

**측정값 (2026-08-07, 이 PC).**
| | selenium 경로 | CDP 직통 |
|---|---|---|
| 붙기 | 1,567 ms | **21 ms** |
| eval 1회 | 3.4 ms | **0.44 ms** |
| 요소 20개 속성 | 140 ms | **0.7 ms** |
| 페이지 3개 대기 | 18.00 s (sleep 6×3) | **0.97 s** |

**안 하는 것.** 클릭·입력의 까다로운 경우(키보드 보안 모듈, 폼 검증)는 여전히
`_chrome` 의 selenium 경로가 낫다. 여기는 **읽기와 이동**이 주력이다.
서드파티 의존 없음 — WebSocket 프레이밍을 직접 한다(약 40줄). 그래서 selenium 이
없는 환경에서도 돈다.
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import socket
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PORT = 9222
_T = time.perf_counter


# ── 타겟 목록 (WebSocket 없이 HTTP 만으로) ─────────────────────────
def tabs(port: int = DEFAULT_PORT, timeout: float = 2.0) -> list[dict]:
    """열린 page 탭 목록. 11ms. **남의 탭을 건드리기 전에 항상 이걸 먼저 봐라**(되돌릴 수 없는 건 사람이 한다)."""
    url = f"http://127.0.0.1:{port}/json/list"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        raw = json.loads(r.read().decode("utf-8"))
    return [t for t in raw if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]


def new_tab(url: str = "about:blank", port: int = DEFAULT_PORT) -> dict:
    """새 탭을 연다. 남의 작업 탭을 건드리지 않는 유일하게 안전한 시작점."""
    q = urllib.parse.quote(url, safe="")
    ep = f"http://127.0.0.1:{port}/json/new?{q}"
    try:
        req = urllib.request.Request(ep, method="PUT")   # Chrome 111+ 는 PUT 만 받는다
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError:
        with urllib.request.urlopen(ep, timeout=5) as r:  # 구버전 폴백
            return json.loads(r.read().decode("utf-8"))


def browser_ws(port: int = DEFAULT_PORT, timeout: float = 2.0) -> str:
    """탭이 아니라 «브라우저 전체»에 붙는 WebSocket 주소. `Browser.close` 같은 건 여기로 보낸다."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))["webSocketDebuggerUrl"]


def close_browser(port: int = DEFAULT_PORT) -> None:
    """이 포트의 크롬을 통째로 닫는다(셀레늄 없이). 응답 전에 연결이 끊기는 게 정상이다."""
    c = CDP(port, ws=browser_ws(port))
    try:
        c.call("Browser.close", timeout=5)
    except (ConnectionError, TimeoutError, OSError):
        pass
    finally:
        c.close()


def close_tab(tab_id: str, port: int = DEFAULT_PORT) -> bool:
    """타겟 id 로 탭 하나를 닫는다. 레시피가 연 작업 탭을 치울 때 쓴다."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/close/{tab_id}", timeout=5) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


# ── CDP 연결 ───────────────────────────────────────────────────────
class CDP:
    """탭 하나에 붙은 CDP 세션. WebSocket 을 직접 말한다(서드파티 없음)."""

    def __init__(self, port: int = DEFAULT_PORT, match: str | None = None,
                 timeout: float = 30.0, ws: str | None = None):
        """`match`: URL 또는 제목에 이 문자열이 든 탭. 없으면 첫 탭.

        ★ 문자열을 안 주면 **첫 탭**이 잡힌다 — 내가 방금 연 탭이 아니다.
        `_chrome.attach()` 를 다시 부르면 같은 일이 벌어져서 "셀렉터가 하나도 안 잡힘"
        으로 오진했던 이력이 있다(LESSONS.md). 심부름 중이면 `match` 를 꼭 줘라.
        `ws`: 탭 대신 이 주소에 붙는다 — 브라우저 전체 세션(`browser_ws()`)용.
        """
        page = {"webSocketDebuggerUrl": ws} if ws else _pick(tabs(port), match)
        self.target = page
        ws = page["webSocketDebuggerUrl"]                 # ws://127.0.0.1:9222/devtools/page/<ID>
        host, path = ws[len("ws://"):].split("/", 1)
        h, p = host.rsplit(":", 1)
        self.s = socket.create_connection((h, int(p)), timeout=timeout)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # 작은 프레임이 뭉치면 지연된다
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(
            f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("핸드셰이크 중 연결이 끊겼다")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"업그레이드 거절: {buf[:200]!r}")
        self._buf = buf.split(b"\r\n\r\n", 1)[1]
        self._id = 0
        self._frame_id = None    # 격리 월드용. 처음 eval 때 한 번 채운다
        self._ctx = None         # 격리 월드 executionContextId. 항해로 죽으면 다시 만든다

    # -- WebSocket 프레이밍 (클라이언트→서버는 마스킹 필수) --
    def _recv(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.s.recv(max(1 << 16, n - len(self._buf)))
            if not chunk:
                raise ConnectionError("연결이 닫혔다")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send(self, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            hdr = b"\x81" + bytes([0x80 | n])
        elif n < 1 << 16:
            hdr = b"\x81\xfe" + struct.pack(">H", n)
        else:
            hdr = b"\x81\xff" + struct.pack(">Q", n)
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _frame(self) -> tuple[int, bytes]:
        b0, b1 = self._recv(2)
        op, n = b0 & 0x0F, b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._recv(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._recv(8))[0]
        return op, self._recv(n)

    # -- CDP --
    def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        self._id += 1
        mid = self._id
        self._send(json.dumps({"id": mid, "method": method,
                               "params": params or {}}).encode("utf-8"))
        deadline = _T() + timeout
        while _T() < deadline:
            op, data = self._frame()
            if op == 0x8:
                raise ConnectionError("브라우저가 세션을 닫았다")
            if op == 0x9:          # ping
                continue
            msg = json.loads(data)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            # 그 밖의 이벤트는 흘린다 (Page.* 를 enable 하지 않았으니 거의 안 온다)
        raise TimeoutError(f"{method} 응답 없음 ({timeout}s)")

    # -- 격리 월드: «메인 월드 실행» 누출을 막는다 (2026-09-16) --
    def _isolated_ctx(self) -> int:
        """이 프레임의 «격리 월드» executionContextId. 없으면 만든다.

        메인 월드(페이지 스크립트가 사는 곳)에서 `Runtime.evaluate` 를 돌리면 페이지가
        계기(Proxy·`Object.defineProperty`)를 심어 그 호출을 «관측»할 수 있다 — brotector 가
        잡는 `at :1:28` 프레임이 그거다. 격리 월드는 **DOM 은 그대로 공유**하되 페이지 JS·전역과
        분리돼 있어, 우리 eval 이 페이지 계기에 안 걸린다(확장 프로그램이 사는 곳과 같은 격.
        네트워크 지문은 이거와 무관하다 — 그건 attach 라서 이미 진짜 크롬이다).
        🔴 **항해하면 이 컨텍스트가 파괴된다.** stale id 로 부르면 CDP 가 에러를 내고,
          `eval` 이 그 에러를 잡아 한 번 다시 만든다.
        """
        if self._frame_id is None:
            self._frame_id = self.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
        if self._ctx is None:
            r = self.call("Page.createIsolatedWorld",
                          {"frameId": self._frame_id, "worldName": "__agent",
                           "grantUniveralAccess": True})   # ★ CDP 스펙 원문 철자다(오타가 규격)
            self._ctx = r["executionContextId"]
        return self._ctx

    def _eval_raw(self, js: str, ctx: int, timeout: float):
        r = self.call("Runtime.evaluate",
                      {"expression": js, "contextId": ctx,
                       "returnByValue": True, "awaitPromise": True},
                      timeout=timeout)
        exc = r.get("exceptionDetails")
        if exc:
            raise RuntimeError(exc.get("text", "") + " :: " +
                               str(exc.get("exception", {}).get("description", ""))[:300])
        return r.get("result", {}).get("value")

    def eval(self, js: str, timeout: float = 30.0):
        """페이지의 «격리 월드»에서 JS. **여러 값이 필요하면 한 번에 배열/객체로 받아라** —
        요소마다 왕복하면 20개에 140ms, 한 방이면 0.7ms 다.

        DOM(querySelector·innerText·location·elementFromPoint)은 격리 월드에서도 그대로
        보인다. 페이지 JS 가 만든 «전역 변수»는 안 보이지만 이 파일은 그런 걸 안 읽는다
        (goto 의 `window.__agent_nav` 도장은 «우리»가 격리 월드에 찍고 격리 월드에서 읽으니 문제없다).
        """
        try:
            return self._eval_raw(js, self._isolated_ctx(), timeout)
        except RuntimeError as e:
            # 항해로 컨텍스트가 죽었으면 stale id 다 — 딱 이 세 문구일 때만 다시 만들고 재시도한다
            # (JS 예외 텍스트를 잘못 잡아 무한 재시도하지 않도록 문구를 좁혔다).
            m = str(e)
            if self._ctx is not None and (
                "Cannot find context" in m or "Execution context was destroyed" in m
                or "uniqueContextId" in m or "Inspected target" in m):
                self._ctx = None
                return self._eval_raw(js, self._isolated_ctx(), timeout)
            raise

    # -- 이동과 대기 --
    def until(self, js: str, timeout: float = 20.0, poll: float = 0.04):
        """`js` 가 truthy 를 낼 때까지 폴링하고 그 값을 반환. 못 채우면 TimeoutError.

        **`sleep(N)` 대신 이걸 써라.** 고정 대기는 빠른 페이지에서 통째로 낭비고,
        느린 페이지에서는 그래도 모자란다 — 둘 다 틀린다.
        🔴 **DOM 노드를 그대로 돌려주면 영영 안 끝난다** — `returnByValue` 라 요소가 `{}` 로 오고
        파이썬에서 빈 dict 는 거짓이다. `document.querySelector(...)` 앞에 `!!` 를 붙여라
        (2026-09-19 실측: 페이지는 0.2초에 떴는데 30초 타임아웃).
        """
        t0 = _T()
        last_err = None
        while _T() - t0 < timeout:
            try:
                v = self.eval(js)
                if v:
                    return v
            except Exception as e:          # 이동 중엔 컨텍스트가 잠깐 사라진다
                last_err = e
            time.sleep(poll)
        raise TimeoutError(f"{timeout}s 안에 조건 미충족: {js[:120]} (마지막 오류: {last_err})")

    def goto(self, url: str, until: str | None = None, timeout: float = 30.0) -> dict:
        """이동하고 **새 문서가 실제로 뜬 뒤에** `until` 을 기다린다.

        ★ 순진하게 폴링하면 **직전 페이지의 DOM 을 읽고 조건을 만족시켜 버린다.**
        그래서 이동 전에 옛 문서에 도장(`window.__agent_nav`)을 찍고, 그 도장이
        사라지는 것으로 문서 교체를 확인한다. 도장은 새 문서에 안 따라온다.
        (주의: SPA 의 pushState 라우팅은 문서를 안 갈아치우므로 도장이 남는다 —
        그 경우엔 `until` 만으로 판정된다.)
        """
        t0 = _T()
        try:
            self.eval("window.__agent_nav = 1")
        except Exception:
            pass
        self.call("Page.navigate", {"url": url}, timeout=timeout)
        swapped = None
        while _T() - t0 < timeout:
            try:
                if not self.eval("window.__agent_nav"):
                    swapped = _T() - t0
                    break
            except Exception:
                pass
            time.sleep(0.03)
        got = None
        if until:
            got = self.until(until, timeout=max(1.0, timeout - (_T() - t0)))
        return {"url": self.eval("location.href"), "title": self.eval("document.title"),
                "swapped_s": round(swapped, 3) if swapped else None,
                "elapsed_s": round(_T() - t0, 3), "until": got}

    # -- 읽기 --
    def text(self, css: str = "body") -> str:
        # 이동 직후엔 document.body 가 잠깐 null 이다 — 그냥 읽으면 TypeError 로 튄다.
        return self.eval(f"((document.querySelector({css!r})||{{}}).innerText)||''")

    def links(self, contains: str = "", limit: int = 60) -> list[list[str]]:
        """[[텍스트, href], ...]. 한 번의 eval 로 전부 가져온다."""
        return self.eval(
            "Array.from(document.querySelectorAll('a'))"
            f".filter(a=>(a.getAttribute('href')||'').includes({contains!r}))"
            ".map(a=>[a.innerText.trim().slice(0,80), a.href])"
            f".filter(x=>x[0]).slice(0,{int(limit)})"
        ) or []

    def alts(self, limit: int = 60) -> list[str]:
        """img 의 alt. **카피가 이미지인 상품 페이지에서 텍스트를 건지는 값싼 경로**다."""
        return self.eval(
            "Array.from(document.querySelectorAll('img')).map(i=>i.alt)"
            f".filter(a=>a&&a.length>3).slice(0,{int(limit)})") or []

    # -- 쓰기 (가벼운 것만) --
    def click(self, css: str) -> dict:
        """요소 중앙에 **진짜 마우스 이벤트**를 쏜다. 가려져 있으면 누구에게 가로채였는지 알려준다.

        LESSONS.md 의 '클릭이 안 먹는다' 진단을 내장했다 — 셀렉터를 20번 찌르는 대신
        `elementFromPoint` 로 1회에 판별한다.
        """
        info = self.eval(
            f"(()=>{{const e=document.querySelector({css!r});if(!e)return{{err:'no-element'}};"
            "e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();"
            "if(r.width===0||r.height===0)return{err:'zero-size'};"
            "const x=r.left+r.width/2,y=r.top+r.height/2;"
            "const iv=x>=0&&y>=0&&x<=innerWidth&&y<=innerHeight;"
            "if(!iv)return{err:'offscreen',x,y};const t=document.elementFromPoint(x,y);"
            "return{x,y,ok:!!t&&(e===t||e.contains(t)),"
            "by:t?((t.tagName+'.'+(t.className||'')+'#'+(t.id||'')).slice(0,80)):'(없음)'}})()")
        err = info.get("err")
        if err == "no-element":
            raise RuntimeError(f"요소 없음: {css}")
        # ★ 여기서 원인을 갈라야 한다. 셋 다 증상이 '클릭이 안 먹는다'로 똑같이 보이는데
        #   대응은 전혀 다르다 — 덮였으면 모달을 닫고, 0크기/화면밖이면 셀렉터가 틀린 거다.
        if err == "zero-size":
            raise RuntimeError(f"크기가 0인 요소다: {css} (숨겨진 요소를 잡았다 — 셀렉터를 의심해라)")
        if err == "offscreen":
            raise RuntimeError(f"뷰포트 밖이다: {css} @({info['x']:.0f},{info['y']:.0f}) "
                               f"— sticky 헤더/푸터에 가렸거나 스크롤 컨테이너 안이다")
        if not info["ok"]:
            raise RuntimeError(f"가려져 있다: {css} ← '{info['by']}' 가 덮음 (모달/오버레이 먼저 닫아라)")
        for typ in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent",
                      {"type": typ, "x": info["x"], "y": info["y"],
                       "button": "left", "clickCount": 1})
        return info

    def shot(self, path: str | None = None, full: bool = False) -> str:
        """PNG 로 떨군다. selenium 없이 CDP 로만."""
        if path is None:
            base = Path(os.environ.get("TEMP", ".")) / "shots"
            base.mkdir(parents=True, exist_ok=True)
            path = base / f"shot_{len(list(base.glob('shot_*.png'))) + 1:03d}.png"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        params = {"format": "png"}
        if full:
            m = self.call("Page.getLayoutMetrics")
            size = m.get("cssContentSize") or m["contentSize"]
            params.update(captureBeyondViewport=True,
                          clip={"x": 0, "y": 0, "width": size["width"],
                                "height": size["height"], "scale": 1})
        path.write_bytes(base64.b64decode(self.call("Page.captureScreenshot", params)["data"]))
        return str(path)

    def close(self) -> None:
        try:
            self.s.close()
        except OSError:
            pass


def _pick(pages: list[dict], match: str | None) -> dict:
    if not pages:
        raise RuntimeError("page 탭이 하나도 없다. `python -m module_browser launch` 먼저.")
    if match:
        # 타겟 id 가 그대로 오면 그 탭 — `newtab` 이 돌려준 id 를 --tab 에 바로 넣을 수 있다.
        # (URL/제목으로 잡으면 about:blank 가 둘일 때 남의 탭을 집는다)
        hit = [t for t in pages if t.get("id") == match] or [
            t for t in pages
            if match.lower() in (t.get("url", "") + " " + t.get("title", "")).lower()]
        if not hit:
            have = "\n  ".join(f"{t.get('title','')[:40]!r} {t.get('url','')[:70]}" for t in pages)
            raise RuntimeError(f"{match!r} 인 탭이 없다. 열린 탭:\n  {have}")
        return hit[0]
    return pages[0]


# ── 배치: 한 번의 호출로 심부름 한 건 ──────────────────────────────
def run(steps: list, port: int = DEFAULT_PORT, match: str | None = None,
        cdp: "CDP | None" = None) -> list:
    """스텝 목록을 한 세션에서 순서대로 실행하고 결과 목록을 돌려준다.

    **이게 이 파일의 존재 이유다.** 이동→대기→추출→다음 이동을 스크립트 3개로 나누면
    프로세스 시작(×3) + 붙기(×3) + 사람(=에이전트) 왕복(×3)을 전부 낸다. 한 번에 부으면
    붙기 한 번으로 끝난다.

    스텝: `["goto", url, until?]` `["until", js]` `["eval", js]` `["text", css?]`
          `["links", 부분문자열, 개수?]` `["alts", 개수?]` `["click", css]`
          `["shot", path?, full?]` `["sleep", 초]`
          `["sleep", lo, hi]` — lo~hi 초 무작위 대기 (고정은 `["sleep", n]`)
          `["each", js, [하위 스텝…]]` — `js` 가 낸 배열의 원소마다 하위 스텝을 돈다.
          하위 스텝의 문자열 인자에서 `{item}`·`{item[0]}` 이 그 원소로 치환된다
          (강좌 목록을 읽어 강좌마다 표를 긁는 식 — 목록을 JSON 에 박아 두면 학기마다 썩는다).
    """
    c = cdp or CDP(port, match)
    out = []
    for i, st in enumerate(steps):
        op, args = st[0], st[1:]
        try:
            if op == "each":
                items = c.eval(args[0]) or []
                r = []
                for item in items:
                    sub = run(_fill(args[1], item), port, match, cdp=c)
                    r.append({"item": item, "steps": sub})
                    if sub and not sub[-1]["ok"]:
                        break
            elif op == "goto":
                r = c.goto(args[0], args[1] if len(args) > 1 else None)
            elif op == "until":
                r = c.until(*args)
            elif op == "eval":
                r = c.eval(*args)
            elif op == "text":
                r = c.text(*(args or ["body"]))
            elif op == "links":
                r = c.links(*(args or [""]))
            elif op == "alts":
                r = c.alts(*args)
            elif op == "click":
                r = c.click(*args)
            elif op == "shot":
                r = c.shot(*args)
            elif op == "sleep":
                # ["sleep", n] 고정 · ["sleep", lo, hi] 는 lo~hi 무작위 — 매번 같은 박자로 누르면 봇이다(2026-09-22, 하루 4회 무인 갱신용)
                secs = random.uniform(float(args[0]), float(args[1])) if len(args) > 1 else float(args[0])
                time.sleep(secs)
                r = round(secs, 2)
            else:
                raise ValueError(f"모르는 스텝: {op}")
            out.append({"step": i, "op": op, "ok": True, "result": r})
        except Exception as e:
            # ★ 중간에 실패해도 여태 건진 것은 돌려준다 — 처음부터 다시 하는 게 제일 비싸다.
            out.append({"step": i, "op": op, "ok": False, "error": f"{type(e).__name__}: {e}"})
            break
    if cdp is None:
        c.close()
    return out


def _fill(steps: list, item) -> list:
    """`each` 의 하위 스텝에 원소를 끼운다. `{item}` 전체, `{item[N]}` 은 N번째."""
    def sub(v):
        if isinstance(v, str):
            v = re.sub(r"\{item\[(\d+)\]\}", lambda m: str(item[int(m.group(1))]), v)
            return v.replace("{item}", str(item))
        if isinstance(v, list):
            return [sub(x) for x in v]
        return v
    return sub(steps)


# ── 레시피: 지도에 적힌 스텝을 이름으로 돌린다 ───────────────────────
def recipe(site: str, name: str, port: int = DEFAULT_PORT) -> dict:
    """`site_map.json` 의 `<site>.recipes.<name>` 을 새 탭에서 돌리고 행을 모아 돌려준다.

    **왜 있나.** «학교 사이트에 오늘 마감 뭐 있나» 같은 심부름은 판단이 아니라 절차다 — 크롬 확인 →
    로그인 확인 → SSO → 표 긁기 → 정렬. 매번 에이전트가 스텝 JSON 을 새로 짜고 열 번 왕복하면
    그 비용을 매번 내고, 세션 만료를 «빈 일정»으로 오독한 실측(2026-09-19)도 코드면 한 줄로 잡힌다.
    **새 모듈이 아니다** — 스텝은 지도(데이터)에 있고 여기는 그걸 읽어 `run()` 에 넘길 뿐이다.
    셀렉터가 깨지면 고칠 곳은 `site_map.json` 이지 이 파일이 아니다.

    레시피 항목:
      `login`  — `_login` 의 사이트 키. 로그인 벽에 떨어지면 «한 번» 자동 로그인하고 처음부터 다시 돈다.
      `wall`   — JS. truthy 면 로그인 벽으로 본다 (`goto`·`each` 마다 잰다).
      `steps`  — `run()` 스텝. `eval` 이 돌려준 dict 목록은 `rows` 로 모인다.
    반환: {"ok", "site", "recipe", "rows", "raw", "note"}. 로그인이 사람 몫이면 ok=False + note.
    되돌릴 수 없는 클릭은 레시피에 넣지 마라 — 이건 «읽기»용이다.
    """
    from . import _chrome, _sitemap
    entry = _sitemap.get_site(site)
    rec = (entry.get("recipes") or {}).get(name)
    if rec is None:
        raise KeyError(f"'{site}' 에 '{name}' 레시피 없음. 있는 것: {sorted(entry.get('recipes') or {})}")
    lr = _chrome.launch(port)
    if not lr["ok"]:
        raise RuntimeError(f"Chrome 실행 실패: {lr['error']}")
    tab = new_tab("about:blank", port)["id"]
    note = ""
    raw = []
    relogged = False
    for attempt in range(2):
        raw = _run_walled(rec["steps"], rec.get("wall"), port, tab)
        if not (raw and raw[-1].get("wall")):
            break
        # 로그인 벽. 두 번째면 포기 — 자동 로그인 재시도는 계정을 잠근다 (errand 스킬 §후퇴 신호).
        if attempt or not rec.get("login"):
            note = "로그인 벽 — 사람이 로그인해야 한다"
            break
        from . import _login
        lg = _login.login(_chrome.attach(port), rec["login"])   # selenium 경로는 여기서만(1.6초)
        if not lg["ok"]:
            note = f"자동 로그인 실패({lg['reason']}) — 사람이 로그인해야 한다"
            break
        relogged = True
    rows = _flatten(raw)
    ok = bool(raw) and all(r.get("ok") for r in raw) and not note
    if ok:
        close_tab(tab, port)      # 실패했으면 탭을 남긴다 — `shot` 으로 볼 수 있게
    return {"ok": ok, "site": site, "recipe": name, "rows": rows, "raw": raw, "note": note,
            "relogged": relogged, "at": time.strftime("%Y-%m-%d %H:%M")}


def _run_walled(steps: list, wall: str | None, port: int, tab: str) -> list:
    """`run()` 과 같되 `goto`·`each` 뒤마다 `wall` 을 잰다. 벽이면 거기서 멈추고 `wall: True` 를 단다."""
    if not wall:
        return run(steps, port, tab)
    c = CDP(port, tab)
    out = []
    try:
        for st in steps:
            r = run([st], port, tab, cdp=c)
            r[-1]["step"] = len(out)
            out.extend(r)
            if not r[-1]["ok"]:
                break
            if st[0] in ("goto", "each") and c.eval(wall):
                out[-1]["wall"] = True
                break
    finally:
        c.close()
    return out


def _flatten(raw: list) -> list:
    """결과 트리에서 eval 이 낸 dict 들을 한 줄로 편다 (`each` 는 중첩이라 재귀)."""
    rows = []
    for r in raw:
        if not r.get("ok"):
            continue
        if r["op"] == "each":
            for part in r["result"]:
                rows.extend(_flatten(part["steps"]))
        elif r["op"] == "eval":
            v = r["result"]
            if isinstance(v, list):
                rows.extend(x for x in v if isinstance(x, dict))
            elif isinstance(v, dict):
                rows.append(v)
    return rows


def _brief(res: dict, show_all: bool = False) -> str:
    """행을 사람이 읽는 줄로. `due` 오름차순, 기본은 `done` 이 참인 줄을 숨긴다.

    「제출 완료」와 «이미 지난 마감»을 감추는 건 이 출력이 «오늘 뭐 해야 하나»에 답하기 위해서다.
    다 보려면 `--all`. 값이 없는 칸은 비워 둔다 — 그럴듯하게 채우지 않는다.
    `done` 이 null 이면 `?` — 페이지가 제출 여부를 안 알려준 것이지 «안 했다»가 아니다
    (Moodle 퀴즈 목록이 그렇다. 확실히 알려면 그 퀴즈의 view.php 를 «열어만» 봐라).
    """
    rows = sorted(res["rows"], key=lambda r: str(r.get("due", "")))
    if not show_all:
        # 마감(`due`)이 있는 줄만 «끝났거나 지난 것»을 숨긴다. `due` 가 없는 레시피(뉴스 목록 등)는 전부 보인다.
        today = time.strftime("%Y-%m-%d")
        rows = [r for r in rows if "due" not in r
                or (not r.get("done") and str(r.get("due", ""))[:10] >= today)]
    head = "ok" if res["ok"] else "★미완: " + (res["note"] or "스텝 실패")
    if res.get("relogged"):
        head += " (세션이 끊겨 있어 다시 로그인함)"
    lines = [f"[{res['site']} · {res['recipe']}] {res.get('at', '')} {head}"]
    for r in rows:
        if "due" in r:
            mark = {True: "v", False: "x"}.get(r.get("done"), "?")
            line = f"  {str(r.get('due', '?')):16s} {mark} {r.get('course', '')} · {r.get('kind', '')} {r.get('title', '')}"
            if r.get("status"):
                line += f"  ({r['status']})"
        else:
            line = "  " + " · ".join(str(v) for v in r.values() if v not in (None, ""))
        lines.append(line)
    if not rows:
        lines.append("  (남은 것 없음)" if res["ok"] else "  (읽은 행 없음)")
    for r in res["raw"]:
        if not r.get("ok"):
            lines.append(f"  ! step {r['step']} {r['op']}: {r.get('error', '')}")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────
def _usage() -> None:
    print(__doc__.strip().splitlines()[0])
    print("""
usage:
  python -m module_browser fast tabs                     # 열린 탭 (WS 없이 11ms)
  python -m module_browser fast newtab <URL>             # 새 탭 — 남의 탭을 안 건드리는 시작점
  python -m module_browser fast eval  <JS>       [--tab STR]
  python -m module_browser fast text  [CSS]      [--tab STR]
  python -m module_browser fast links [부분문자열] [--tab STR]
  python -m module_browser fast alts             [--tab STR]
  python -m module_browser fast goto  <URL> [--until JS] [--tab STR]
  python -m module_browser fast shot  [PATH] [--full]    [--tab STR]
  python -m module_browser fast run   '<JSON 스텝배열>'  [--tab STR]   # 여러 스텝을 한 방에
  echo '<JSON>' | python -m module_browser fast run -    [--tab STR]
  python -m module_browser fast recipe <사이트> <이름> [--json] [--all]  # 지도의 recipes 를 이름으로
         크롬 기동·로그인 벽 감지·자동 로그인 1회·표 긁기까지 한 줄. 기본은 «남은 것»만 출력.

  --tab  은 URL/제목 부분문자열. **안 주면 첫 탭이 잡힌다 — 내가 연 탭이 아니다.**
  --port 는 붙을 크롬(기본 9222). **포트를 바꾸는 건 «프로필=로그인 계정»을 갈아끼우는 것이다.**
         남의 계정으로 도는 일은 자기 포트를 쓴다 (예: 9223).""")


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        _usage()
        return 0
    port = DEFAULT_PORT
    if "--port" in argv:
        i = argv.index("--port")
        port = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    match = None
    if "--tab" in argv:
        i = argv.index("--tab")
        match = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    full = "--full" in argv
    argv = [a for a in argv if a != "--full"]
    until = None
    if "--until" in argv:
        i = argv.index("--until")
        until = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]

    cmd, rest = argv[0], argv[1:]
    dump = lambda v: print(json.dumps(v, ensure_ascii=False, indent=2))  # noqa: E731

    if cmd == "tabs":
        dump([{"title": t.get("title", "")[:70], "url": t.get("url", "")[:110]}
              for t in tabs(port)])
        return 0
    if cmd == "newtab":
        t = new_tab(rest[0] if rest else "about:blank", port)
        dump({"url": t.get("url"), "id": t.get("id")})
        return 0
    if cmd == "recipe":
        want_json, show_all = "--json" in rest, "--all" in rest
        pos = [a for a in rest if not a.startswith("--")]
        if len(pos) < 2:
            _usage()
            return 2
        res = recipe(pos[0], pos[1], port)
        if want_json:
            dump(res)
        else:
            print(_brief(res, show_all))
        return 0 if res["ok"] else 1
    if cmd == "run":
        if not rest or rest[0] == "-":
            # ★ Windows 는 stdin 을 cp949 로 디코딩한다 — 한글이 든 조건문(`innerText.includes('상품')`)
            #   이 조용히 깨져서 영영 참이 안 되고 timeout 으로만 드러난다(실측으로 29초 날렸다).
            #   규약의 "CLI 진입점마다 UTF-8" 은 stdout 만이 아니라 stdin 에도 걸린다.
            try:
                sys.stdin.reconfigure(encoding="utf-8")
                raw = sys.stdin.read()
            except (AttributeError, OSError):
                raw = sys.stdin.buffer.read().decode("utf-8")
        else:
            raw = rest[0]
        dump(run(json.loads(raw), port, match))
        return 0

    c = CDP(port, match)
    try:
        if cmd == "eval":
            dump(c.eval(rest[0]))
        elif cmd == "text":
            print(c.text(rest[0] if rest else "body"))
        elif cmd == "links":
            dump(c.links(rest[0] if rest else ""))
        elif cmd == "alts":
            dump(c.alts())
        elif cmd == "goto":
            dump(c.goto(rest[0], until))
        elif cmd == "shot":
            p = c.shot(rest[0] if rest else None, full=full)
            print(p)
            print("url:", c.eval("location.href"))
        else:
            print(f"모르는 명령: {cmd}")
            _usage()
            return 2
    finally:
        c.close()
    return 0

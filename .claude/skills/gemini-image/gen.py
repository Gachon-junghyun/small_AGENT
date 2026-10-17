"""제미나이로 이미지를 «여러 장» 뽑아 PNG 로 떨군다. 크롬을 직접 몬다(API 아님).

    python .claude/skills/gemini-image/gen.py --out <폴더> --jobs <jobs.json> [--ref <레퍼런스.png>]
                                              [--port 9222] [--per-chat 5] [--wait 200] [--pause 8-25]

`jobs.json` = [{"name": "loading_bg", "prompt": "..."}, ...]  (없으면 stdin 에서 읽는다)

🔴 여기 박힌 것은 전부 **실측으로 밟은 함정**이다 (`LESSONS.md` §이미지 생성 · 이 스킬 SKILL.md 함정 표):
  ① 사이드바 「새 채팅」 클릭은 **조용히 씹힌다** → 주소로 간다
  ② 한 탭에 큰 이미지가 ~10장 쌓이면 **렌더러가 죽는다** → N장마다 대화를 새로 연다
  ③ 생성물을 `fetch(blob:)` 로 받으면 실패한다 → **canvas.toDataURL** 로 우회
  ④ 전송 클릭이 «성공 보고»하고도 안 보내진다 → **입력칸이 비었는지**로 판정하고 재클릭
  ⑤ 화풍 고정은 «텍스트»로 안 된다 → 레퍼런스 PNG 를 **첨부**한다
  ⑥ 마지막 탭을 닫으면 크롬이 통째로 죽는다 → 이 스크립트는 탭을 **안 닫는다**
  ⑭ 여러 세션이 한 크롬을 같이 쓴다 → **자기 탭을 새로 열어** 그 탭만 쓴다(남의 제미나이 탭을 집지 않는다)
  ⑮ 장과 장 사이를 기계처럼 똑같이 두드리지 않는다 → 보내기 전·받기 전에 **무작위로 쉰다**(--pause)
"""
import argparse
import base64
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from module_browser._fast import CDP, new_tab  # noqa: E402

APP = "https://gemini.google.com/app"
BOX = "div[contenteditable=true]"


try:   # 🔴 Windows cp949 콘솔은 «⚠» 한 글자에 죽는다 — 진입점마다 UTF-8 을 박는다 (CLAUDE.md 규약)
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def log(*a):
    print(*a, flush=True)


def rest(span, why):
    """🔴 무작위로 쉰다(함정 ⑮) — 사람도 한 장 보고 다음 걸 치기까지 틈이 있다. 계정을 아끼는 게 속도보다 싸다."""
    lo, hi = span
    if hi <= 0:
        return
    t = random.uniform(lo, hi)
    log("   (%s 전 %.0f초 쉼)" % (why, t))
    time.sleep(t)


def new_chat(c):
    """🔴 사이드바 버튼을 누르지 마라 — 조용히 씹힌다. 주소로 간다(함정 ①)."""
    # 🔴 `until` 에 «요소»를 돌려주는 JS 를 주지 마라 — 직렬화하려다
    #    `Object reference chain is too long` 으로 죽는다. 불리언으로 받아라.
    c.goto(APP, f"!!document.querySelector({BOX!r})", timeout=60)
    c.until(f"!!document.querySelector({BOX!r})", timeout=30)
    time.sleep(1.0)


def paste_attach(c, path):
    """🔴 첨부 메뉴가 안 열릴 때의 «진짜» 경로 (2026-09-08 실측).

    파일을 base64 로 실어 보내 페이지 안에서 `File` 을 만들고 `ClipboardEvent('paste')` 를
    컴포저에 쏜다. `data:` URL 은 `fetch` 가 되므로(함정 ③ 의 `blob:` 과 다르다) 이 길이 열린다.
    ⚠️ 큰 PNG 를 그대로 실으면 표현식이 수 MB 가 된다 — 레퍼런스는 1100px 이하로 줄여서 줘라.
    """
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    js = ("(async()=>{const box=document.querySelector(%r);if(!box)return 'no-box';"
          "const r=await fetch('data:image/png;base64,'+%s);const blob=await r.blob();"
          "const f=new File([blob],'ref.png',{type:'image/png'});"
          "const dt=new DataTransfer();dt.items.add(f);box.focus();"
          "box.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));"
          "return 'ok';})()" % (BOX, json.dumps(b64)))
    try:
        got = c.eval(js, timeout=90)
    except Exception as e:
        log("   ⚠️ 붙여넣기 첨부 실패 %s" % str(e)[:80])
        return False
    if got != "ok":
        log("   ⚠️ 붙여넣기 첨부 실패 (%s)" % got)
        return False
    time.sleep(4.0)
    log("   첨부(붙여넣기): %s" % os.path.basename(path))
    return True


def attach(c, path):
    """레퍼런스 PNG 를 첨부한다 — 화풍 고정은 이것 말고 방법이 없다(함정 ⑤).

    `input[type=file]` 이 처음엔 DOM 에 없다. 「업로드 및 도구」 메뉴를 «열기만» 해도 생긴다.
    """
    sel = 'input[type=file]'
    if not c.eval(f"!!document.querySelector({sel!r})"):
        for css in ('button[aria-label*="업로드"]', 'button[aria-label*="도구"]',
                    'button[aria-label*="Upload"]', 'button[aria-label*="add"]'):
            try:
                if c.eval(f"!!document.querySelector({css!r})"):
                    c.click(css)
                    time.sleep(1.2)
                    if c.eval(f"!!document.querySelector({sel!r})"):
                        break
            except Exception:
                continue
    if not c.eval(f"!!document.querySelector({sel!r})"):
        # 🔴 2026-09-08: 제미나이가 컴포저를 «알약형»으로 개편하면서 「업로드 및 도구」(+) 메뉴가
        #    합성 클릭에 «안 열린다» — CDP 마우스도 DOM `.click()` 도 오버레이가 안 뜬다.
        #    그래서 파일 input 자체가 영영 안 생긴다. → 붙여넣기로 우회한다(함정 ⑩).
        return paste_attach(c, path)
    # 🔴 `_fast.eval()` 은 항상 returnByValue 라 objectId 를 못 준다 — 이 대목만 call() 직접
    r = c.call("Runtime.evaluate", {"expression": f"document.querySelector({sel!r})",
                                    "returnByValue": False})
    oid = r.get("result", {}).get("objectId")
    if not oid:
        log("   ⚠️ objectId 를 못 얻었다")
        return False
    node = c.call("DOM.describeNode", {"objectId": oid})
    bid = node.get("node", {}).get("backendNodeId")
    c.call("DOM.setFileInputFiles", {"files": [os.path.abspath(path)], "backendNodeId": bid})
    time.sleep(2.5)
    log("   첨부: %s" % os.path.basename(path))
    return True


def idle(c, limit=240):
    """🔴 앞 그림이 «아직 그려지는 중»이면 보내기 버튼이 죽어 있다 (2026-09-07 실측).

    이걸 안 기다리면 재시도를 네 번 해도 전부 씹힌다 — 클릭이 실패한 게 아니라
    «누를 수 없는 버튼»을 누른 것이다. 중지 버튼이 사라질 때까지 기다린다.
    """
    js = ("(()=>{const q=s=>document.querySelector(s);"
          "return !!(q('button[aria-label*=\"중지\"]')||q('button[aria-label*=\"Stop\"]')"
          "||q('button[aria-label*=\"응답 중지\"]'));})()")
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            if not c.eval(js):
                return True
        except Exception:
            pass
        time.sleep(2.0)
    return False


def send(c, prompt):
    """입력칸에 넣고 보낸다. 🔴 «입력칸이 비었는지»로 판정한다(함정 ④)."""
    idle(c)
    # 🔴 2026-09-08 실측: 탭이 «배경»이면 보내기 클릭이 조용히 씹힌다 — 재시도 8회가 전부 무의미했다.
    #    CDP 마우스도 DOM .click() 도 진짜 Enter 키도 다 먹지 않다가, 이 한 줄을 넣자 한 번에 갔다(함정 ⑪).
    try:
        c.call("Page.bringToFront", {})
        time.sleep(0.8)
    except Exception:
        pass
    js_set = (
        "(()=>{const b=document.querySelector(%r);if(!b)return 'no-box';"
        "b.focus();b.innerText=%s;"
        "b.dispatchEvent(new InputEvent('input',{bubbles:true,data:'x'}));"
        "return b.innerText.length;})()" % (BOX, json.dumps(prompt))
    )
    got = c.eval(js_set)
    if got == "no-box":
        raise RuntimeError("입력칸이 없다 — 로그인이 풀렸을 수 있다")
    time.sleep(0.6)
    for attempt in range(8):
        for css in ('button[aria-label*="보내기"]', 'button[aria-label*="Send"]',
                    'button[mattooltip*="보내기"]', 'button.send-button'):
            try:
                if c.eval(f"!!document.querySelector({css!r})"):
                    c.click(css)
                    break
            except Exception:
                continue
        else:
            c.eval("(()=>{const b=document.querySelector(%r);b.focus();"
                   "b.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:13,bubbles:true}));"
                   "})()" % BOX)
        time.sleep(2.5)
        left = c.eval(f"((document.querySelector({BOX!r})||{{}}).innerText||'').trim().length")
        if not left:
            return True
        log("   전송이 씹혔다(%d회차) — 기다렸다 다시 누른다" % (attempt + 1))
        idle(c, 120)
        time.sleep(3.0)
    return False


def wait_image(c, before, limit):
    """새로 뜬 «큰» 이미지의 src 를 기다린다. 장당 18~36초가 정상이다."""
    js = ("(()=>{const s=%s;const o=Array.from(document.images)"
          ".filter(i=>i.naturalWidth>=400&&i.naturalHeight>=300&&!s.includes(i.currentSrc))"
          ".map(i=>i.currentSrc);return o.length?o[o.length-1]:null;})()" % json.dumps(before))
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            v = c.eval(js)
        except Exception as e:
            log("   (대기 중 예외 %s)" % str(e)[:70])
            v = None
        if v:
            return v
        time.sleep(2.0)
    return None


def big_srcs(c):
    return c.eval("Array.from(document.images).filter(i=>i.naturalWidth>=400)"
                  ".map(i=>i.currentSrc)") or []


def save_png(c, src, path):
    """🔴 `fetch(blob:)` 은 실패한다 — canvas 로 옮겨 담는다(함정 ③)."""
    js = ("(()=>{const im=Array.from(document.images).find(i=>i.currentSrc===%s);"
          "if(!im)return null;const cv=document.createElement('canvas');"
          "cv.width=im.naturalWidth;cv.height=im.naturalHeight;"
          "cv.getContext('2d').drawImage(im,0,0);"
          "try{return cv.toDataURL('image/png');}catch(e){return 'ERR:'+e.message;}})()"
          % json.dumps(src))
    data = c.eval(js, timeout=90)
    if not data or str(data).startswith("ERR:"):
        return None
    raw = base64.b64decode(str(data).split(",", 1)[1])
    with open(path, "wb") as f:
        f.write(raw)
    return len(raw)


DL_BTN = 'button[aria-label="원본 크기 이미지 다운로드"], button[aria-label*="Download full"]'


def click_download(c, out_dir, path, limit=90):
    """🔴 미리보기(1024px)를 캔버스로 뜨지 말고 «원본 크기 이미지 다운로드»를 눌러 받는다 (2026-10-03 실측:
    미리보기 1024×853 ↔ 원본 2266×1888 — 제미나이는 «화질»이 실제로 갈린다). 마지막 그림의 버튼을 누르고
    내려받은 파일을 `<이름>.png` 로 바꾼다. 못 받으면 None → 부르는 쪽이 캔버스로 대신 받는다."""
    d = os.path.abspath(out_dir)
    c.call("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": d})
    c.call("Page.bringToFront", {})
    b4 = set(os.listdir(d))
    if not c.eval("(()=>{const b=[...document.querySelectorAll(%s)].pop();if(!b)return false;"
                  "b.scrollIntoView({block:'center'});b.click();return true;})()" % json.dumps(DL_BTN)):
        return None
    got = None
    for _ in range(limit):
        time.sleep(1)
        new = [f for f in set(os.listdir(d)) - b4 if not f.endswith((".crdownload", ".tmp"))]
        if new:
            got = os.path.join(d, new[0]); break
    if not got:
        return None
    time.sleep(0.5)
    if os.path.abspath(got) != os.path.abspath(path):
        if os.path.exists(path):
            os.remove(path)
        os.replace(got, path)
    return os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs")
    ap.add_argument("--ref")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--per-chat", type=int, default=5)
    ap.add_argument("--wait", type=int, default=200)
    ap.add_argument("--pause", default="8-25", help="장마다 보내기 전 무작위 대기 초 범위(받기 전은 2~6초). 0 이면 안 쉰다")
    ap.add_argument("--skip-done", action="store_true", help="<이름>.png 가 이미 있으면 건너뛴다(끊긴 배치 이어 돌리기)")
    ap.add_argument("--reuse-tab", action="store_true", help="새 탭을 열지 않고 있는 제미나이 탭을 쓴다(옛 동작)")
    ap.add_argument("--canvas", action="store_true", help="옛 방식: 미리보기(1024px)를 캔버스로 뜬다. 기본은 «원본 크기 이미지 다운로드»를 누른다")
    a = ap.parse_args()
    lo, _, hi = a.pause.partition("-")
    pause = (float(lo), float(hi or lo))

    jobs = json.load(open(a.jobs, encoding="utf-8")) if a.jobs else json.load(sys.stdin)
    os.makedirs(a.out, exist_ok=True)
    mine = None
    if a.reuse_tab and _has_gemini_tab(a.port):
        c = CDP(a.port, "gemini.google.com")
    else:
        # 🔴 다른 세션이 같은 크롬에서 제미나이를 쓰고 있을 수 있다 — 자기 탭을 새로 열어 id 로 붙는다(함정 ⑭)
        mine = new_tab(APP, a.port)["id"]
        c = CDP(a.port, mine)
    log("탭: %s" % c.target.get("url", "")[:80])

    done, fail = [], []
    for i, job in enumerate(jobs):
        name, prompt = job["name"], job["prompt"]
        out = os.path.join(a.out, name + ".png")
        if a.skip_done and os.path.exists(out):
            log("[%d/%d] %s — 이미 있음, 건너뜀" % (i + 1, len(jobs), name))
            continue
        # 🔴 N장마다 대화를 새로 연다 — 안 그러면 렌더러가 죽는다(함정 ②)
        if i % a.per_chat == 0:
            log("[대화 새로 열기] %d번째부터" % (i + 1))
            new_chat(c)
            if a.ref and os.path.exists(a.ref):
                attach(c, a.ref)
                prompt = "The attached image is the style reference. " + prompt
        log("[%d/%d] %s" % (i + 1, len(jobs), name))
        if i:
            rest(pause, "보내기")
        before = big_srcs(c)
        t0 = time.time()
        if not send(c, prompt):
            fail.append((name, "전송 실패")); continue
        # 🔴 보내는 «순간» 첨부한 레퍼런스가 대화에 되비친다 — 그걸 결과로 집으면
        #    3초 만에 «성공»하고 레퍼런스가 그대로 저장된다 (2026-09-07 실측).
        #    생성은 18~70초라 10초 뒤에 목록을 다시 떠서 그때부터 «새로 뜬 것»만 본다.
        time.sleep(10)
        before = big_srcs(c)
        src = wait_image(c, before, a.wait)
        if not src:
            fail.append((name, "이미지 안 나옴(%ds)" % a.wait)); continue
        rest((min(2, pause[1]), min(6, pause[1])), "받기")
        # 🔴 쉬는 사이에 제미나이가 미리보기를 큰 그림으로 «갈아 끼운다» — 아까 본 src 는 이미 없다(2026-09-28, 저장 실패).
        #    받기 직전에 «새로 뜬 것 중 마지막»을 다시 잡는다.
        src = wait_image(c, before, 20) or src
        n = None if a.canvas else click_download(c, a.out, out)
        if not n:
            if not a.canvas:
                log("   ⚠️ 원본 다운로드를 못 받았다 — 미리보기로 대신 받는다(해상도 낮음)")
            n = save_png(c, src, out)
        if not n:
            fail.append((name, "저장 실패")); continue
        log("   ✅ %s  %dKB  %.0f초" % (os.path.basename(out), n // 1024, time.time() - t0))
        done.append(out)

    log("\n== 끝 == 성공 %d / 실패 %d" % (len(done), len(fail)))
    for n, why in fail:
        log("   🔴 %s : %s" % (n, why))
    # 🔴 남의 탭은 안 닫는다. 내가 연 탭만, 다른 탭이 남아 있을 때만 닫는다 — 마지막 탭을 닫으면 크롬이 통째로 죽는다(함정 ⑥)
    if mine:
        from module_browser._fast import close_tab, tabs
        c.close()
        if len(tabs(a.port)) > 1:
            close_tab(mine, a.port)


def _has_gemini_tab(port):
    from module_browser._fast import tabs
    return any("gemini.google.com" in (t.get("url") or "") for t in tabs(port))


if __name__ == "__main__":
    main()

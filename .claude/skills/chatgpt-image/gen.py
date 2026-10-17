"""ChatGPT 로 이미지를 «여러 장» 뽑아 PNG 로 떨군다. 크롬을 직접 몬다(API 아님).

    python .claude/skills/chatgpt-image/gen.py --out <폴더> --jobs <jobs.json> [--ref <레퍼런스.png>]
                                               [--port 9222] [--per-chat 3] [--wait 240] [--pause 8-25]

`jobs.json` = [{"name": "apple", "prompt": "..."}, ...]  (없으면 stdin 에서 읽는다) — 형식은 gemini-image 와 같다.

gemini-image/gen.py 의 짝이다. 쉬는 법(`rest`)·로그는 거기서 가져온다(복제 금지 — CLAUDE.md 규약).
여기 박힌 ChatGPT 고유 사실은 전부 2026-09-28 실측이다:
  · 입력칸은 ProseMirror(`#prompt-textarea`) — innerText 를 박지 말고 **`Input.insertText`** 로 친다
  · 보내기 버튼은 글자를 쳐야 생긴다(`aria-label="보내기"`). 생성 중엔 `aria-label="중지"`
  · 🔴 **중지 버튼이 «그림보다 먼저» 사라진다**(24초에 사라지고 27초에 그림) — idle 로 판정하지 마라
  · 결과는 `alt="생성된 이미지 N"` 의 `blob:` img — canvas.toDataURL 로 원본 해상도(1254²) 그대로 받힌다
  · 새 대화는 주소로 간다(`https://chatgpt.com/`)
"""
import argparse
import base64
import importlib.util
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
from module_browser._fast import CDP, new_tab, close_tab, tabs  # noqa: E402

# 같은 이름(gen.py)이라 `import gen` 으로는 못 부른다 — 경로로 불러 다른 이름을 준다
_spec = importlib.util.spec_from_file_location(
    "gemini_gen", os.path.join(ROOT, ".claude", "skills", "gemini-image", "gen.py"))
_g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_g)
log, rest = _g.log, _g.rest

APP = "https://chatgpt.com/"
BOX = "#prompt-textarea, div.ProseMirror[contenteditable=true]"
GEN_ALT = "/생성된 이미지|Generated image/"


def new_chat(c):
    c.goto(APP, f"!!document.querySelector({BOX!r})", timeout=60)
    time.sleep(1.5)


def has_attach(c):
    """입력칸 폼에 첨부가 «올라가 끝난» 채로 붙어 있나. 2026-10-08 실측 신호 두 개:
    `«파일명» 제거` 버튼이 생기고, 미리보기 img 가 `blob:` → 업로드가 끝나면 `data:` 로 바뀐다(약 3~6초)."""
    return c.eval(f"(()=>{{const b=document.querySelector({BOX!r});const f=b&&b.closest('form');if(!f)return false;"
                  "const x=[...f.querySelectorAll('button')].some(e=>/ 제거$|^Remove /.test(e.getAttribute('aria-label')||''));"
                  "const up=[...f.querySelectorAll('img')].some(i=>/^data:/.test(i.src||''));return x&&up;})()")


def attach(c, path, tries=3):
    """레퍼런스를 첨부한다. `input[type=file][accept="image/*"]` 가 처음부터 DOM 에 있다.
    🔴 «넣었다»가 아니라 «붙어서 올라갔다»를 확인한다(2026-10-08 — 둘째 대화에서 또 조용히 빠졌다)."""
    sel = 'input[type=file][accept="image/*"]'
    for k in range(tries):
        r = c.call("Runtime.evaluate", {"expression": f"document.querySelector({sel!r})",
                                        "returnByValue": False})
        oid = r.get("result", {}).get("objectId")
        if oid:
            bid = c.call("DOM.describeNode", {"objectId": oid}).get("node", {}).get("backendNodeId")
            c.call("DOM.setFileInputFiles", {"files": [os.path.abspath(path)], "backendNodeId": bid})
            for _ in range(15):
                time.sleep(1.0)
                if has_attach(c):
                    log("   첨부: %s" % os.path.basename(path))
                    return True
        log("   ⚠️ 첨부가 안 붙었다(%d회차) — 다시 넣는다" % (k + 1))
        time.sleep(2.0)
    return False


def busy(c):
    return c.eval("[...document.querySelectorAll('button')].some(b=>"
                  "/^(중지|Stop)/.test(b.getAttribute('aria-label')||'')"
                  "||b.getAttribute('data-testid')==='stop-button')")


def idle(c, limit=240):
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            if not busy(c):
                return True
        except Exception:
            pass
        time.sleep(2.0)
    return False


def box_len(c):
    return c.eval(f"((document.querySelector({BOX!r})||{{}}).innerText||'').trim().length")


def send(c, prompt):
    """쳐 넣고 보낸다. 판정은 «입력칸이 비었나»다 — 클릭 성공 보고는 믿지 않는다."""
    idle(c)
    c.call("Page.bringToFront", {})   # 배경 탭이면 전송이 씹힌다(gemini 함정 ⑪과 같은 계열)
    time.sleep(0.8)
    c.eval(f"(()=>{{const b=document.querySelector({BOX!r});b.focus();"
           "document.execCommand('selectAll');document.execCommand('delete');})()")
    c.call("Input.insertText", {"text": prompt})
    time.sleep(1.2)
    for attempt in range(5):
        for css in ('button[data-testid="send-button"]', 'button[aria-label="보내기"]',
                    'button[aria-label*="Send"]'):
            try:
                if c.eval(f"!!document.querySelector({css!r})"):
                    c.click(css)
                    break
            except Exception:
                continue
        time.sleep(2.5)
        if not box_len(c):
            return True
        log("   전송이 씹혔다(%d회차) — 다시 누른다" % (attempt + 1))
        idle(c, 120)
    return False


def gen_srcs(c):
    return c.eval(f"[...document.images].filter(i=>i.naturalWidth>=300&&{GEN_ALT}.test(i.alt))"
                  ".map(i=>i.currentSrc)") or []


def wait_image(c, before, limit):
    """새로 뜬 «생성된 이미지»를 기다린다. 🔴 중지 버튼이 먼저 사라지므로 그걸로 끝을 판정하지 않는다.
    그림이 뜬 뒤 src 가 두 번 연속 같고 중지 버튼이 없으면 끝난 것으로 본다(부분 미리보기 대비)."""
    t0, last, calm = time.time(), None, 0
    while time.time() - t0 < limit:
        try:
            new = [s for s in gen_srcs(c) if s not in before]
            cur = new[-1] if new else None
            if cur and cur == last and not busy(c):
                calm += 1
                if calm >= 2:
                    return cur
            else:
                calm = 0
            last = cur
        except Exception as e:
            log("   (대기 중 예외 %s)" % str(e)[:70])
        time.sleep(2.0)
    return None


def save_png(c, src, path):
    js = ("(()=>{const im=[...document.images].find(i=>i.currentSrc===%s);"
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


def click_download(c, out_dir, path, limit=60):
    """🔴 미리보기를 캔버스로 뜨지 말고 «눌러서» 받는다 (2026-10-03, 사람 지시: "미리보기로 다운받지 말고 눌러서 고화질로").
    새 그림의 미리보기 버튼(`generated-image-preview`) → 뷰어의 `다운로드` → 파일이 생기면 `<이름>.png` 로 바꾼다.
    ⚠️ 실측: ChatGPT 는 받은 파일도 1374×1145 로 캔버스와 «같은 해상도»였다 — 이 사이트에선 화질 차이가 아니라 «원본 파일 그대로»의 차이다."""
    d = os.path.abspath(out_dir)
    c.call("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": d})
    c.call("Page.bringToFront", {})
    if not c.eval("(()=>{const b=[...document.querySelectorAll('button[data-testid=\"generated-image-preview\"]')].pop();"
                  "if(!b)return false;b.scrollIntoView({block:'center'});b.click();return true;})()"):
        return None
    for _ in range(20):
        time.sleep(0.5)
        if c.eval("!!document.querySelector('button[aria-label=\"다운로드\"],button[aria-label=\"Download\"]')"):
            break
    b4 = set(os.listdir(d))
    # 🔴 뷰어가 «열리는 중»(html.opening-*)이면 버튼이 덮였다고 click 이 죽는다(2026-10-03 실측) — 애니메이션을 기다렸다 누르고, 그래도 덮이면 DOM 클릭
    for _ in range(10):
        if not c.eval("/opening/.test(document.documentElement.className)"):
            break
        time.sleep(0.4)
    try:
        c.click('button[aria-label="다운로드"], button[aria-label="Download"]')
    except Exception:
        c.eval("(()=>{const b=document.querySelector('button[aria-label=\"다운로드\"],button[aria-label=\"Download\"]');if(b)b.click();})()")
    got = None
    for _ in range(limit):
        time.sleep(1)
        new = [f for f in set(os.listdir(d)) - b4 if not f.endswith((".crdownload", ".tmp"))]
        if new:
            got = os.path.join(d, new[0]); break
    try:
        c.click('button[aria-label="뷰어 닫기"], button[aria-label*="Close"]')
    except Exception:
        pass
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
    ap.add_argument("--per-chat", type=int, default=3)
    ap.add_argument("--wait", type=int, default=240)
    ap.add_argument("--pause", default="8-25", help="장마다 보내기 전 무작위 대기 초 범위(받기 전은 2~6초). 0 이면 안 쉰다")
    ap.add_argument("--skip-done", action="store_true", help="<이름>.png 가 이미 있으면 건너뛴다")
    ap.add_argument("--canvas", action="store_true", help="옛 방식: 미리보기를 캔버스로 뜬다(기본은 «다운로드 버튼을 눌러» 받는다)")
    a = ap.parse_args()
    lo, _, hi = a.pause.partition("-")
    pause = (float(lo), float(hi or lo))

    jobs = json.load(open(a.jobs, encoding="utf-8")) if a.jobs else json.load(sys.stdin)
    os.makedirs(a.out, exist_ok=True)
    # 🔴 사람이 쓰던 ChatGPT 탭에 끼어들지 않는다 — 자기 탭을 새로 열어 id 로만 붙는다
    mine = new_tab(APP, a.port)["id"]
    c = CDP(a.port, mine)
    time.sleep(3)
    try:
        run(a, c, jobs, pause)
    finally:   # 도중에 죽어도 내 탭은 치운다. 마지막 탭을 닫으면 크롬이 통째로 죽으니 다른 탭이 있을 때만
        c.close()
        if len(tabs(a.port)) > 1:
            close_tab(mine, a.port)


def run(a, c, jobs, pause):
    done, fail = [], []
    for i, job in enumerate(jobs):
        name, prompt = job["name"], job["prompt"]
        out = os.path.join(a.out, name + ".png")
        if a.skip_done and os.path.exists(out):
            log("[%d/%d] %s — 이미 있음, 건너뜀" % (i + 1, len(jobs), name))
            continue
        ref = None
        if i % a.per_chat == 0:
            log("[대화 새로 열기] %d번째부터" % (i + 1))
            new_chat(c)
            if job.get("ref") and os.path.exists(job["ref"]):      # 장마다 다른 원본(렌더 다듬기 등) — 프롬프트가 쓰임새를 말한다
                ref = job["ref"]
            elif a.ref and os.path.exists(a.ref):
                ref = a.ref
                prompt = "The attached image is the style reference. " + prompt
            if ref and not attach(c, ref):
                fail.append((name, "첨부 실패 — 레퍼런스 없이 보내지 않았다")); continue
        log("[%d/%d] %s" % (i + 1, len(jobs), name))
        if i:
            rest(pause, "보내기")
        # 🔴 쉬는 사이 첨부가 빠질 수 있다 — 보내기 직전에 한 번 더 보고, 빠졌으면 다시 붙인다
        if ref and not has_attach(c) and not attach(c, ref):
            fail.append((name, "첨부 실패(보내기 직전 빠짐)")); continue
        before = gen_srcs(c)
        t0 = time.time()
        if not send(c, prompt):
            fail.append((name, "전송 실패")); continue
        src = wait_image(c, before, a.wait)
        if not src:
            fail.append((name, "이미지 안 나옴(%ds) — 되묻는 답이었을 수 있다" % a.wait)); continue
        rest((min(2, pause[1]), min(6, pause[1])), "받기")
        new = [s for s in gen_srcs(c) if s not in before]   # 쉬는 사이 갈아 끼워졌으면 새 걸 잡는다
        # 🔴 «어느 이미지가 더 마음에 드세요?» 두 장 비교가 뜨면 다운로드 버튼 길에서 멈췄다(2026-10-10).
        #    고르지 않고(사람 몫) 두 장 다 캔버스로 받는다 → <이름>_a.png · <이름>_b.png
        if len(new) >= 2 and c.eval("document.body.innerText.includes('이미지 선택')"):
            got = [save_png(c, s, os.path.join(a.out, "%s_%s.png" % (name, "ab"[k]))) for k, s in enumerate(new[-2:])]
            if all(got):
                log("   ✅ %s_a/_b.png  두 장 비교 화면 — 둘 다 받음  %.0f초" % (name, time.time() - t0))
                done.append(out); continue
        n = None if a.canvas else click_download(c, a.out, out)
        if not n:
            if not a.canvas:
                log("   ⚠️ 다운로드 버튼으로 못 받았다 — 미리보기로 대신 받는다")
            n = save_png(c, new[-1] if new else src, out)
        if not n:
            fail.append((name, "저장 실패")); continue
        log("   ✅ %s  %dKB  %.0f초" % (os.path.basename(out), n // 1024, time.time() - t0))
        done.append(out)

    log("\n== 끝 == 성공 %d / 실패 %d" % (len(done), len(fail)))
    for n, why in fail:
        log("   🔴 %s : %s" % (n, why))


if __name__ == "__main__":
    main()

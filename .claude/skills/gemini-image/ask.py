"""제미나이에게 **그림을 보여주고 «글»로 답을 받는다.** (`gen.py` 의 짝 — 저건 그림을 받아온다)

    python .claude/skills/gemini-image/ask.py --images a.png b.png --prompt "이 화면 뭘 고칠까"
    python .claude/skills/gemini-image/ask.py --images a.png --prompt-file q.txt --out 답.md

🔴 함정은 `gen.py` 와 같다(SKILL.md 함정 표) — 주소로 새 대화 · 생성 중엔 보내기 버튼이 죽어 있다 ·
   탭을 안 닫는다. 다른 점 하나: **여기는 이미지가 아니라 «답변 텍스트»가 끝나기를 기다린다.**
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from module_browser._fast import CDP  # noqa: E402
sys.path.insert(0, os.path.dirname(__file__))
from gen import APP, BOX, new_chat, send, idle, log, _has_gemini_tab  # noqa: E402


def attach_many(c, paths):
    """여러 장을 한 번에 붙인다 — `DOM.setFileInputFiles` 는 배열을 받는다."""
    sel = 'input[type=file]'
    if not c.eval(f"!!document.querySelector({sel!r})"):
        for css in ('button[aria-label*="업로드"]', 'button[aria-label*="도구"]',
                    'button[aria-label*="Upload"]'):
            try:
                if c.eval(f"!!document.querySelector({css!r})"):
                    c.click(css)
                    time.sleep(1.2)
                    if c.eval(f"!!document.querySelector({sel!r})"):
                        break
            except Exception:
                continue
    if not c.eval(f"!!document.querySelector({sel!r})"):
        log("🔴 파일 input 을 못 찾았다 — 그림 없이 물어본다")
        return False
    r = c.call("Runtime.evaluate", {"expression": f"document.querySelector({sel!r})",
                                    "returnByValue": False})
    oid = r.get("result", {}).get("objectId")
    bid = c.call("DOM.describeNode", {"objectId": oid}).get("node", {}).get("backendNodeId")
    c.call("DOM.setFileInputFiles",
           {"files": [os.path.abspath(p) for p in paths], "backendNodeId": bid})
    log("첨부 %d장: %s" % (len(paths), ", ".join(os.path.basename(p) for p in paths)))
    # 미리보기가 실제로 붙을 때까지 기다린다 — 바로 보내면 첨부 없이 나간다
    time.sleep(2.0 + 1.5 * len(paths))
    return True


def answer_text(c, limit=300):
    """답변이 «끝날 때까지» 기다렸다가 마지막 답을 읽는다."""
    t0 = time.time()
    time.sleep(4)
    if not idle(c, limit):
        log("⚠️ 상한까지 기다렸는데 아직 쓰고 있다 — 그때까지 나온 것만 가져온다")
    time.sleep(2)
    js = ("(()=>{const q=document.querySelectorAll('message-content, .model-response-text, "
          "[data-test-id=\"model-response-text\"]');"
          "if(q.length) return q[q.length-1].innerText;"
          "const t=document.body.innerText; return t.slice(-6000);})()")
    txt = c.eval(js, timeout=60) or ""
    log("답변 %d자 (%.0f초)" % (len(txt), time.time() - t0))
    return txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", nargs="*", default=[])
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--out")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--wait", type=int, default=300)
    a = ap.parse_args()

    prompt = a.prompt or open(a.prompt_file, encoding="utf-8").read()
    c = CDP(a.port, "gemini.google.com") if _has_gemini_tab(a.port) else CDP(a.port, "about:blank")
    log("탭: %s" % c.target.get("url", "")[:80])
    new_chat(c)
    if a.images:
        attach_many(c, a.images)
    if not send(c, prompt):
        log("🔴 전송 실패")
        sys.exit(1)
    txt = answer_text(c, a.wait)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt)
        log("→ %s" % a.out)
    else:
        print(txt)


if __name__ == "__main__":
    main()

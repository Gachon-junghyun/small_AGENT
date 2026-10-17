"""python -m module_browser <서브커맨드>

지도:      map | find | go | catalog          # 어디로 가서 뭘 하나 (크롬 안 띄운다)
크롬:      status | launch | open | shot | close
빠른 경로: fast <...>                        # CDP 직통 — 읽기·이동은 이쪽이 75배 빠르다
자격증명·로그인: set-cred | list-creds | sites | check | login

**심부름은 지도부터다.** `find` 로 목적지와 `access` 를 먼저 보고 크롬을 띄운다 —
api/blocked 인 곳에 브라우저로 들어가면 레이트리밋·캡차·약관 위반을 산다.

사람이 붙어 있는 Claude Code 세션에서 쓰는 도구다. 시행착오는 LESSONS.md 에 쌓는다.
"""
import sys

from . import _chrome, _login, _sitemap

CHROME = {"status", "launch", "open", "shot", "close"}
SITEMAP = {"map", "find", "go", "catalog"}


def main(argv: list[str]) -> int:
    # stdout 만이 아니라 stderr 도 고정한다 — 예외 메시지(열린 탭 목록 등)에 한글이 실리는데
    # cp949 콘솔에서 통째로 깨져 진단이 안 된다. stdin 은 `fast run` 쪽에서 따로 편다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    cmd = argv[0] if argv else ""
    if cmd == "fast":
        from . import _fast          # stdlib 만 쓰지만 여기도 지연 import 로 통일
        return _fast.main(argv[1:])
    if cmd in SITEMAP:
        return _sitemap.main(argv)
    if cmd in CHROME:
        return _chrome.main(argv)
    if cmd in ("", "-h", "--help", "help"):
        print(__doc__)
        return 0
    return _login.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

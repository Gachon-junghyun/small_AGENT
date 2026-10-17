"""module_browser — 실제 크롬을 조종한다. 검색으로 안 보이는 것, 로그인해야 보이는 것용.

**무거운 것은 함수 안에서 lazy import 한다.** `import module_browser` 만으로 selenium 이
끌려오면 안 된다 — 자격증명만 읽으려는 소비자까지 selenium 을
설치해야 하게 된다. `status()`/`wait_port()`/`launch()` 는 표준 라이브러리만 쓴다.
"""
from ._chrome import (  # noqa: F401
    DEFAULT_PORT, attach, click, close, ensure, find, goto, js, launch,
    screenshot, status, type_text, wait_port,
)
from ._login import (  # noqa: F401
    ensure_login, get_credential, is_logged_in, list_credentials, login,
    set_credential,
)
# 지도는 selenium 도 크롬도 안 쓴다 — JSON 한 장을 읽을 뿐이다.
# 그래서 "어디로 가야 하나" 만 알고 싶은 소비자는 브라우저를 안 띄우고도 부를 수 있다.
from ._sitemap import (  # noqa: F401
    catalog, find_site, get_site, load_map, site_url,
)

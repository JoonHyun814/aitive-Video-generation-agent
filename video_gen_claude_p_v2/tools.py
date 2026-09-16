"""Tool implementations + OpenAI-style function specs for the planning
agent's manual tool loop (agent_loop.py).

Two tools, adapted from scenario_agent_claude_litellm/tools.py (RAG tools
dropped -- this pipeline has no RAG server to query):
  - web_fetch: HTML -> text (+ discovered image URLs), or image URL -> vision
    analysis (OCR + appearance) via a LiteLLM vision call.
  - web_search: DuckDuckGo HTML endpoint scrape (no search API key
    configured for this project -- same documented caveat as the source
    module: fragile, blockable, not an official API).

This exact two-tool combination is what actually found and visually
verified the real card/logo images in
output/나라사랑카드_20260915_103041/trace.json -- reused verbatim rather
than inventing a new "image search" tool.
"""

from __future__ import annotations

import base64
import mimetypes
from urllib.parse import parse_qs, urljoin, urlparse

import litellm
import requests
from bs4 import BeautifulSoup

from . import config

_UA = "Mozilla/5.0 (compatible; video-gen-agent/1.0)"
_MAX_TEXT_CHARS = 6000
_MAX_IMAGES_LISTED = 15
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")

# web_fetch's vision path makes its own litellm call, outside agent_loop's
# normal request/response cycle -- so its cost/usage would otherwise be
# invisible in the run trace. Tool functions are single-threaded and called
# synchronously one at a time, so a plain module list is a safe place to
# stash "a sub-call happened" for agent_loop to drain after each dispatch().
_SUB_CALLS: list[dict] = []


def pop_sub_calls() -> list[dict]:
    calls = list(_SUB_CALLS)
    _SUB_CALLS.clear()
    return calls


def _vision_describe(image_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    resp = litellm.completion(
        model=config.litellm_model_id(),
        api_base=config.base_url(),
        api_key=config.api_key(),
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "이 이미지는 광고에 쓸 인물 복장/헤어, 장소, 소품, 제품, 로고 "
                            "등의 레퍼런스 후보일 수 있다. 다음을 한국어로 사실적으로 "
                            "보고하라: (1) 이미지 안에 인쇄/표시된 텍스트를 있는 그대로 "
                            "읽어서 verbatim으로 나열, (2) 피사체의 색상·형태·재질·구성 등 "
                            "외형을 관찰한 그대로 서술, (3) 실제 인물 얼굴이 보이는지 여부. "
                            "보이지 않는 내용은 추측하지 말고 '확인 불가'라고 답하라."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }
        ],
        timeout=60,
        num_retries=1,
    )
    _SUB_CALLS.append({"tool": "web_fetch:vision", "usage": config.usage_dict(resp.usage)})
    return resp.choices[0].message.content or "(이미지 분석 결과 없음)"


def web_fetch(url: str) -> str:
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        return f"URL 접근 실패: {type(e).__name__}: {e}"

    if resp.status_code >= 400:
        return f"HTTP {resp.status_code}: {url} 에 접근할 수 없습니다."

    content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()

    if content_type.startswith("image/") or url.lower().split("?")[0].endswith(_IMAGE_EXT):
        mime = content_type if content_type.startswith("image/") else (
            mimetypes.guess_type(url)[0] or "image/jpeg"
        )
        try:
            return _vision_describe(resp.content, mime)
        except Exception as e:
            return f"이미지 시각 분석 실패: {type(e).__name__}: {e}"

    if "html" not in content_type and "text" not in content_type:
        return f"지원하지 않는 콘텐츠 타입({content_type})입니다: {url}"

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())[:_MAX_TEXT_CHARS]

    images = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src:
            continue
        abs_url = urljoin(url, src)
        if abs_url not in images:
            images.append(abs_url)
        if len(images) >= _MAX_IMAGES_LISTED:
            break

    out = [f"[페이지 텍스트 ({len(text)}자, 잘렸을 수 있음)]", text or "(텍스트 없음)"]
    if images:
        out.append("\n[페이지에서 발견된 이미지 URL — 필요하면 web_fetch(url=이미지URL)로 분석]")
        out.extend(f"- {u}" for u in images)
    return "\n".join(out)


def web_search(query: str, num_results: int = 5) -> str:
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": _UA},
            timeout=20,
        )
    except requests.RequestException as e:
        return f"검색 실패: {type(e).__name__}: {e}"

    if resp.status_code >= 400:
        return f"검색 요청 실패: HTTP {resp.status_code}"

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for result in soup.select("div.result")[: max(num_results, 1)]:
        a = result.select_one("a.result__a")
        snippet_el = result.select_one("a.result__snippet, div.result__snippet")
        if not a:
            continue
        href = a.get("href", "")
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc or href.startswith("//duckduckgo.com"):
            qs = parse_qs(parsed.query)
            href = qs.get("uddg", [href])[0]
        results.append(
            {
                "title": a.get_text(strip=True),
                "url": href,
                "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
            }
        )

    if not results:
        return f"'{query}' 검색 결과가 없습니다(검색 엔진이 차단했을 수 있음)."
    lines = [f"'{query}' 검색 결과 {len(results)}건:"]
    for r in results:
        lines.append(f"- {r['title']} — {r['url']}\n  {r['snippet']}")
    return "\n".join(lines)


TOOL_FUNCS = {
    "web_fetch": web_fetch,
    "web_search": web_search,
}

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "URL의 콘텐츠를 가져온다. HTML 페이지면 텍스트로 요약하고 페이지 내 이미지 "
                "URL 목록도 함께 반환한다. 이미지 URL이면 시각적으로 분석해 텍스트 판독(OCR) "
                "결과, 외형(색상/형태/구성) 묘사, 실제 인물 얼굴 노출 여부를 반환한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "웹 검색을 수행해 관련 페이지의 제목/URL/스니펫을 반환한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "num_results": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]


def dispatch(name: str, arguments: dict) -> str:
    func = TOOL_FUNCS.get(name)
    if func is None:
        return f"알 수 없는 도구: {name}"
    try:
        return func(**arguments)
    except TypeError as e:
        return f"도구 인자 오류 ({name}): {e}"
    except Exception as e:
        return f"도구 실행 오류 ({name}): {type(e).__name__}: {e}"

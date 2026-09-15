"""Tool implementations + OpenAI-style function specs for the manual agent loop.

Three tool families, mirroring what the Claude Code CLI build gets for free
from its built-in harness:
  - web_fetch: HTML -> text (+ discovered image URLs), or image URL -> vision
    analysis (OCR + appearance) via a LiteLLM vision call. No separate OCR
    library needed -- Claude's vision does this directly on image bytes.
  - web_search: no search API key was provided, so this scrapes DuckDuckGo's
    HTML endpoint. Swap in a real search API (Tavily/Serper/Bing) if you have
    one -- this is a placeholder, documented as such in the README.
  - rag_*: thin wrappers over mcp_client.call_rag_tool for the 5 RAG server
    tools (docs/rag/rag_server_info.md).
"""

from __future__ import annotations

import base64
import mimetypes
from urllib.parse import parse_qs, urljoin, urlparse

import litellm
import requests
from bs4 import BeautifulSoup

from . import config, mcp_client

_UA = "Mozilla/5.0 (compatible; scenario-agent/1.0)"
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
                            "이 이미지는 제품 상세페이지의 일부일 수 있다. 다음을 한국어로 "
                            "사실적으로 보고하라: (1) 이미지 안에 인쇄/표시된 텍스트를 있는 "
                            "그대로 읽어서 verbatim으로 나열, (2) 제품의 색상·형태·재질 등 "
                            "외형을 관찰한 그대로 서술. 보이지 않는 내용은 추측하지 말고 "
                            "'확인 불가'라고 답하라."
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
        # DuckDuckGo's html endpoint wraps targets as //duckduckgo.com/l/?uddg=<encoded>
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


def rag_search_chromadb(collection: str, query_text: str, n_results: int = 5) -> str:
    return mcp_client.call_rag_tool(
        "search_chromadb",
        {"collection": collection, "query_text": query_text, "n_results": n_results},
    )


def rag_search_chromadb_hybrid(collection: str, query_text: str, n_results: int = 5) -> str:
    return mcp_client.call_rag_tool(
        "search_chromadb_hybrid",
        {"collection": collection, "query_text": query_text, "n_results": n_results},
    )


def rag_fetch_by_video_id(collection: str, video_id: int) -> str:
    return mcp_client.call_rag_tool(
        "fetch_by_video_id", {"collection": collection, "video_id": video_id}
    )


def rag_search_visual(query_text: str, n_results: int = 5, collection: str = "ad_visual_reference") -> str:
    return mcp_client.call_rag_tool(
        "search_visual",
        {"query_text": query_text, "n_results": n_results, "collection": collection},
    )


def rag_search_graph_pattern(role: str, top_k: int = 10) -> str:
    # persona_category is deliberately never exposed to the model -- see docs/rag/rag_server_info.md
    # "알려진 제약": it's always empty with the current data and would just waste a turn.
    return mcp_client.call_rag_tool("search_graph_pattern", {"role": role, "top_k": top_k})


TOOL_FUNCS = {
    "web_fetch": web_fetch,
    "web_search": web_search,
    "rag_search_chromadb": rag_search_chromadb,
    "rag_search_chromadb_hybrid": rag_search_chromadb_hybrid,
    "rag_fetch_by_video_id": rag_fetch_by_video_id,
    "rag_search_visual": rag_search_visual,
    "rag_search_graph_pattern": rag_search_graph_pattern,
}

_NARRATIVE_ROLES = [
    "HOOK", "ESTABLISH_CONTEXT", "PROBLEM", "EMOTIONAL_APPEAL", "FEATURE",
    "DEMO", "TESTIMONIAL", "SOCIAL_PROOF", "CTA", "BRAND_CLOSE",
]

_RAG_COLLECTIONS = [
    "category_analysis", "scenario_analysis", "ad_concept_reference", "ad_target",
    "ad_usp", "ad_creative", "ad_production_reference", "video_category",
]

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "URL의 콘텐츠를 가져온다. HTML 페이지면 텍스트로 요약하고 페이지 내 이미지 "
                "URL 목록도 함께 반환한다. 이미지 URL이면 시각적으로 분석해 텍스트 판독(OCR) "
                "결과와 외형(색상/형태) 묘사를 반환한다."
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
    {
        "type": "function",
        "function": {
            "name": "rag_search_chromadb",
            "description": "자연어 의미 유사도 검색(dense). 일반적인 컨셉/전략 탐색에 사용.",
            "parameters": {
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "enum": _RAG_COLLECTIONS},
                    "query_text": {"type": "string"},
                    "n_results": {"type": "integer"},
                },
                "required": ["collection", "query_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search_chromadb_hybrid",
            "description": (
                "의미 유사도 + BM25 키워드 매칭 결합 검색. 브랜드명/숫자/고유명사처럼 정확히 "
                "일치해야 하는 키워드가 쿼리에 있을 때 rag_search_chromadb 대신 사용."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "enum": _RAG_COLLECTIONS},
                    "query_text": {"type": "string"},
                    "n_results": {"type": "integer"},
                },
                "required": ["collection", "query_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_fetch_by_video_id",
            "description": (
                "검색으로 이미 찾은 특정 video_id의 원본 레코드 전체를 청킹 없이 가져온다. "
                "새 광고를 찾는 용도가 아니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "enum": _RAG_COLLECTIONS},
                    "video_id": {"type": "integer"},
                },
                "required": ["collection", "video_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search_visual",
            "description": (
                "색감/구도/소품/조명 등 순수 시각적 특징으로 키프레임을 찾는다. 반환되는 "
                "image_path는 서버 쪽 파일 경로이므로 열려고 시도하지 말고 텍스트 힌트로만 "
                "참고할 것."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query_text": {"type": "string"},
                    "n_results": {"type": "integer"},
                },
                "required": ["query_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search_graph_pattern",
            "description": (
                "개별 광고 검색이 아니라, 여러 캠페인에 걸쳐 특정 서사 역할(role)에서 자주 "
                "쓰인 크리에이티브 요소를 집계한 통계를 반환한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": _NARRATIVE_ROLES},
                    "top_k": {"type": "integer"},
                },
                "required": ["role"],
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

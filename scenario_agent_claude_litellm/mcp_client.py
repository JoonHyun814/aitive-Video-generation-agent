"""Synchronous wrapper around the RAG MCP server (Streamable HTTP transport).

The server is stateless_http (see docs/rag/rag_server_info.md), so opening a
fresh session per call -- rather than holding one open across the agent loop
-- matches how the server is designed to be used and keeps this wrapper simple.
"""

from __future__ import annotations

import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

RAG_SERVER_URL = "http://10.110.56.157:8765/mcp"


_TIMEOUT_S = 30
# Some RAG results (e.g. hybrid search over long documents) run 10-15K+ chars.
# Left uncapped, a handful of such results blow up the accumulated context the
# final structured-output call has to process, which is what turned one
# legitimately slow generation into repeated timeout+retry cycles that never
# finished. Cap and let the model ask a narrower follow-up query if it needs more.
_MAX_RESULT_CHARS = 4000


async def _call_async(server_url: str, tool_name: str, arguments: dict) -> str:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            texts = [block.text for block in result.content if hasattr(block, "text")]
            if result.is_error:
                return f"RAG 도구 오류: {' '.join(texts) or '알 수 없는 오류'}"
            combined = "\n".join(texts) if texts else "(빈 결과)"
            if len(combined) > _MAX_RESULT_CHARS:
                combined = combined[:_MAX_RESULT_CHARS] + (
                    f"\n...(총 {len(combined)}자 중 {_MAX_RESULT_CHARS}자만 표시, 잘림 — "
                    "더 필요하면 n_results를 줄이거나 쿼리를 좁혀 다시 검색할 것)"
                )
            return combined


def call_rag_tool(tool_name: str, arguments: dict, server_url: str = RAG_SERVER_URL) -> str:
    # The MCP streamable-http transport can stall indefinitely (SSE stream that
    # never resolves) with no client-side timeout of its own -- wrap the whole
    # call in a hard deadline so a stuck RAG call can't hang the entire agent run.
    try:
        return asyncio.run(
            asyncio.wait_for(_call_async(server_url, tool_name, arguments), timeout=_TIMEOUT_S)
        )
    except asyncio.TimeoutError:
        return f"RAG 도구 호출 타임아웃 ({tool_name}, {_TIMEOUT_S}초)"
    except Exception as e:
        return f"RAG 도구 호출 실패 ({tool_name}): {type(e).__name__}: {e}"

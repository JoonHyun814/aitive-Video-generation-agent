"""Renders a validated Scenario into human-readable markdown and HTML production docs."""

from __future__ import annotations

import html as _html

from .schema import Scenario, SourcedClaim


def render_markdown(scenario: Scenario) -> str:
    p = scenario.product
    lines: list[str] = []

    lines.append(f"# {p.name} — {scenario.duration_s:g}초 광고 시나리오\n")

    lines.append("## 크리에이티브 전략")
    lines.append(f"- FCB 사분면: **{scenario.creative_strategy.fcb_quadrant}**")
    lines.append(f"- 목표 단계(효과 계층 모델): **{scenario.creative_strategy.target_hierarchy_stage}**")
    lines.append(f"- 근거: {scenario.creative_strategy.rationale}\n")

    lines.append("## 제품 사실 (출처 기반)")
    lines.append(f"- 상세페이지: {p.source_url}")
    if p.unverifiable_notes:
        lines.append(f"- ⚠️ 확인 불가 항목: {p.unverifiable_notes}")
    for label, claims in (
        ("성분", p.ingredients),
        ("기능", p.features),
        ("색상/외형", p.appearance),
        ("기타", p.other_claims),
    ):
        if not claims:
            continue
        lines.append(f"\n**{label}**")
        for c in claims:
            mark = "✅" if c.support == "fully_supported" else f"⚠️({c.support})"
            lines.append(f"- {mark} {c.claim}")
            if c.source_quote:
                lines.append(f"  > {c.source_quote}  ({c.source_url})")
    lines.append("")

    lines.append("## 등장인물")
    for c in scenario.characters:
        lines.append(f"- **{c.id} / {c.name}** — {c.role_description}; 외형: {c.appearance}")
    lines.append("")

    lines.append("## 장소")
    for loc in scenario.locations:
        lines.append(f"- **{loc.id} / {loc.name}** — {loc.description}")
    lines.append("")

    lines.append("## 소품")
    for prop in scenario.props:
        extra = f" (근거: {prop.related_claim})" if prop.related_claim else ""
        lines.append(f"- **{prop.id} / {prop.name}** — {prop.description}{extra}")
    lines.append("")

    lines.append("## 씬 구성")
    lines.append(
        "| # | 시간 | 역할 | 장소 | 인물 | 소품 | 행동 | 대사 | 자막 | 음악/효과음 | 이펙트 | 카메라 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in sorted(scenario.scenes, key=lambda s: s.start_s):
        lines.append(
            f"| {s.scene_no} | {s.start_s:g}-{s.end_s:g}s | {s.narrative_role} | "
            f"{s.location_id} | {', '.join(s.character_ids)} | {', '.join(s.prop_ids)} | "
            f"{s.action} | {s.dialogue} | {s.subtitle} | {s.sound_music_sfx} | "
            f"{s.visual_effects} | {s.camera_notes} |"
        )
    lines.append("")

    if scenario.sources:
        lines.append("## 참고 출처")
        for src in scenario.sources:
            lines.append(f"- {src}")
        lines.append("")

    if scenario.rag_queries_used:
        lines.append("## 사용된 RAG 쿼리")
        for q in scenario.rag_queries_used:
            lines.append(f"- {q}")

    return "\n".join(lines)


_ROLE_COLORS = {
    "HOOK": "#e5484d",
    "ESTABLISH_CONTEXT": "#8e8e93",
    "PROBLEM": "#f5a623",
    "EMOTIONAL_APPEAL": "#d6409f",
    "FEATURE": "#0091ff",
    "DEMO": "#00a2c7",
    "TESTIMONIAL": "#6e56cf",
    "SOCIAL_PROOF": "#30a46c",
    "CTA": "#e5484d",
    "BRAND_CLOSE": "#1f2937",
}

_SUPPORT_STYLE = {
    "fully_supported": ("✓ 확인됨", "#e6f6ec", "#0a7a3d"),
    "partially_supported": ("△ 부분 확인", "#fff4e0", "#a15c00"),
    "no_support": ("✕ 미확인", "#fde8e8", "#b42318"),
}

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")


def _e(text: str) -> str:
    return _html.escape(str(text), quote=True)


def _claim_html(c: SourcedClaim) -> str:
    label, bg, fg = _SUPPORT_STYLE.get(c.support, ("?", "#eee", "#333"))
    source_link = ""
    if c.source_url:
        url = _e(c.source_url)
        source_link = f' — <a href="{url}" target="_blank" rel="noopener">{url}</a>'
    quote_html = f"<blockquote>{_e(c.source_quote)}{source_link}</blockquote>" if c.source_quote else ""
    return f"""\
<li class="claim">
  <span class="badge" style="background:{bg};color:{fg}">{label}</span>
  <span class="claim-text">{_e(c.claim)}</span>
  {quote_html}
</li>"""


def _image_thumb(url: str) -> str:
    if url.lower().split("?")[0].endswith(_IMAGE_EXT):
        return f'<a href="{_e(url)}" target="_blank" rel="noopener"><img class="thumb" src="{_e(url)}" loading="lazy" alt=""></a>'
    return ""


def render_html(scenario: Scenario) -> str:
    p = scenario.product
    scenes = sorted(scenario.scenes, key=lambda s: s.start_s)
    chars = {c.id: c for c in scenario.characters}
    locs = {l.id: l for l in scenario.locations}
    props = {pr.id: pr for pr in scenario.props}

    timeline = "".join(
        f'<div class="tl-seg" style="flex:{max(s.end_s - s.start_s, 0.01):g};'
        f'background:{_ROLE_COLORS.get(s.narrative_role, "#999")}" '
        f'title="{_e(s.narrative_role)} ({s.start_s:g}-{s.end_s:g}s)">'
        f'<span>{_e(s.narrative_role)}</span></div>'
        for s in scenes
    )

    facts_sections = "".join(
        f"""\
<div class="fact-group">
  <h3>{label}</h3>
  <ul class="claims">{"".join(_claim_html(c) for c in claims)}</ul>
</div>"""
        for label, claims in (
            ("성분", p.ingredients),
            ("기능", p.features),
            ("색상 / 외형", p.appearance),
            ("기타", p.other_claims),
        )
        if claims
    )

    cast_cards = "".join(
        f"""\
<div class="card">
  <h4>{_e(c.name)} <span class="id-tag">{_e(c.id)}</span></h4>
  <p>{_e(c.role_description)}</p>
  <p class="muted">외형: {_e(c.appearance)}</p>
</div>"""
        for c in scenario.characters
    )

    loc_cards = "".join(
        f"""\
<div class="card">
  <h4>{_e(loc.name)} <span class="id-tag">{_e(loc.id)}</span></h4>
  <p>{_e(loc.description)}</p>
</div>"""
        for loc in scenario.locations
    )

    prop_cards = "".join(
        f"""\
<div class="card">
  <h4>{_e(pr.name)} <span class="id-tag">{_e(pr.id)}</span></h4>
  <p>{_e(pr.description)}</p>
  {f'<p class="muted">근거: {_e(pr.related_claim)}</p>' if pr.related_claim else ""}
</div>"""
        for pr in scenario.props
    )

    def _names(ids: list[str], table: dict) -> str:
        return ", ".join(table[i].name if i in table else i for i in ids)

    scene_cards = "".join(
        f"""\
<div class="scene">
  <div class="scene-head">
    <span class="scene-no">#{s.scene_no}</span>
    <span class="scene-time">{s.start_s:g}s – {s.end_s:g}s</span>
    <span class="role-badge" style="background:{_ROLE_COLORS.get(s.narrative_role, "#999")}">{_e(s.narrative_role)}</span>
  </div>
  <div class="scene-grid">
    <div><span class="k">장소</span>{_e(locs[s.location_id].name if s.location_id in locs else s.location_id)}</div>
    <div><span class="k">인물</span>{_e(_names(s.character_ids, chars)) or "—"}</div>
    <div><span class="k">소품</span>{_e(_names(s.prop_ids, props)) or "—"}</div>
    <div class="span2"><span class="k">행동</span>{_e(s.action)}</div>
    {f'<div class="span2"><span class="k">대사</span>{_e(s.dialogue)}</div>' if s.dialogue else ""}
    {f'<div class="span2"><span class="k">자막</span>{_e(s.subtitle)}</div>' if s.subtitle else ""}
    {f'<div class="span2"><span class="k">음악/효과음</span>{_e(s.sound_music_sfx)}</div>' if s.sound_music_sfx else ""}
    {f'<div class="span2"><span class="k">이펙트</span>{_e(s.visual_effects)}</div>' if s.visual_effects else ""}
    {f'<div class="span2"><span class="k">카메라</span>{_e(s.camera_notes)}</div>' if s.camera_notes else ""}
  </div>
</div>"""
        for s in scenes
    )

    sources_html = "".join(
        f"""\
<li>
  {_image_thumb(src)}
  <a href="{_e(src)}" target="_blank" rel="noopener">{_e(src)}</a>
</li>"""
        for src in scenario.sources
    )

    rag_html = "".join(f"<li>{_e(q)}</li>" for q in scenario.rag_queries_used)

    notes_html = (
        f'<p class="notice">⚠️ 확인 불가 항목: {_e(p.unverifiable_notes)}</p>'
        if p.unverifiable_notes
        else ""
    )

    return f"""\
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(p.name)} — {scenario.duration_s:g}초 광고 시나리오</title>
<style>
  :root {{
    --bg: #f7f7f8; --card: #ffffff; --text: #1a1a1e; --muted: #6b6b72;
    --border: #e6e6e9; --accent: #3454d1;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, "Segoe UI", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
    line-height: 1.55;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; padding: 32px 20px 80px; }}
  header.hero {{
    background: linear-gradient(135deg, var(--accent), #7c3aed);
    color: #fff; border-radius: 16px; padding: 32px 28px; margin-bottom: 28px;
  }}
  header.hero h1 {{ margin: 0 0 8px; font-size: 26px; }}
  header.hero .meta {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }}
  .pill {{
    display: inline-block; padding: 4px 12px; border-radius: 999px;
    background: rgba(255,255,255,.18); font-size: 13px;
  }}
  section {{ margin-bottom: 36px; }}
  h2 {{ font-size: 19px; border-bottom: 2px solid var(--border); padding-bottom: 8px; margin-bottom: 16px; }}
  h3 {{ font-size: 15px; color: var(--muted); margin: 0 0 8px; }}
  .strategy-card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 20px; font-size: 14.5px;
  }}
  .notice {{
    background: #fff4e0; color: #a15c00; border-radius: 8px; padding: 10px 14px; font-size: 13.5px;
  }}
  .fact-group {{ margin-bottom: 18px; }}
  ul.claims {{ list-style: none; margin: 0; padding: 0; }}
  li.claim {{
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 12px 14px; margin-bottom: 8px; font-size: 14px;
  }}
  .claim-text {{ font-weight: 600; margin-left: 6px; }}
  .badge {{
    display: inline-block; font-size: 11.5px; font-weight: 700; padding: 2px 8px;
    border-radius: 999px; vertical-align: middle;
  }}
  blockquote {{
    margin: 8px 0 0; padding: 8px 12px; border-left: 3px solid var(--border);
    color: var(--muted); font-size: 13px; background: var(--bg);
  }}
  blockquote a {{ color: var(--muted); }}
  .grid3 {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px;
  }}
  .card h4 {{ margin: 0 0 6px; font-size: 14.5px; }}
  .card p {{ margin: 4px 0; font-size: 13.5px; }}
  .id-tag {{
    font-size: 11px; color: var(--muted); background: var(--bg); border-radius: 6px;
    padding: 1px 6px; margin-left: 4px;
  }}
  .muted {{ color: var(--muted); }}
  .timeline {{ display: flex; height: 40px; border-radius: 8px; overflow: hidden; margin-bottom: 20px; }}
  .tl-seg {{
    display: flex; align-items: center; justify-content: center; color: #fff;
    font-size: 11px; font-weight: 700; overflow: hidden; white-space: nowrap;
  }}
  .scene {{
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 16px 18px; margin-bottom: 14px;
  }}
  .scene-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }}
  .scene-no {{ font-weight: 800; font-size: 15px; }}
  .scene-time {{ color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }}
  .role-badge {{
    margin-left: auto; color: #fff; font-size: 11.5px; font-weight: 700;
    padding: 3px 10px; border-radius: 999px;
  }}
  .scene-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px 20px; font-size: 13.5px; }}
  .scene-grid .span2 {{ grid-column: 1 / -1; }}
  .scene-grid .k {{
    display: block; font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
    color: var(--muted); margin-bottom: 2px;
  }}
  ul.sources {{ list-style: none; padding: 0; margin: 0; }}
  ul.sources li {{
    display: flex; align-items: center; gap: 10px; padding: 6px 0; font-size: 13px;
    border-bottom: 1px solid var(--border);
  }}
  ul.sources a {{ color: var(--accent); word-break: break-all; }}
  img.thumb {{ width: 40px; height: 40px; object-fit: cover; border-radius: 6px; border: 1px solid var(--border); flex: none; }}
  ul.rag {{ font-size: 13px; color: var(--muted); }}
  footer {{ text-align: center; color: var(--muted); font-size: 12px; margin-top: 40px; }}
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <h1>{_e(p.name)}</h1>
    <div>{scenario.duration_s:g}초 광고 시나리오</div>
    <div class="meta">
      <span class="pill">FCB: {_e(scenario.creative_strategy.fcb_quadrant)}</span>
      <span class="pill">목표 단계: {_e(scenario.creative_strategy.target_hierarchy_stage)}</span>
      <span class="pill">씬 {len(scenes)}개</span>
    </div>
  </header>

  <section>
    <h2>크리에이티브 전략</h2>
    <div class="strategy-card">{_e(scenario.creative_strategy.rationale)}</div>
  </section>

  <section>
    <h2>제품 사실 (출처 기반)</h2>
    <p class="muted">상세페이지: <a href="{_e(p.source_url)}" target="_blank" rel="noopener">{_e(p.source_url)}</a></p>
    {notes_html}
    {facts_sections}
  </section>

  <section>
    <h2>등장인물</h2>
    <div class="grid3">{cast_cards}</div>
  </section>

  <section>
    <h2>장소</h2>
    <div class="grid3">{loc_cards}</div>
  </section>

  <section>
    <h2>소품</h2>
    <div class="grid3">{prop_cards}</div>
  </section>

  <section>
    <h2>씬 구성</h2>
    <div class="timeline">{timeline}</div>
    {scene_cards}
  </section>

  {"<section><h2>참고 출처</h2><ul class='sources'>" + sources_html + "</ul></section>" if scenario.sources else ""}

  {"<section><h2>사용된 RAG 쿼리</h2><ul class='rag'>" + rag_html + "</ul></section>" if scenario.rag_queries_used else ""}

  <footer>scenario_agent 자동 생성 문서 — 촬영 전 사람 검토 필요</footer>
</div>
</body>
</html>
"""

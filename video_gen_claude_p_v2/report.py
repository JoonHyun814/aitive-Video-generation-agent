"""Renders the execution manifest (+ plan + scenario) into a single HTML
report. Adapted from video_gen_claude_p (v1)'s report.py: adds an anchor-
frame section between cuts and the final video, and a link to trace.html
(the planning agent's step-by-step research/tool-call log, see
trace_render.py) since that log doesn't exist in v1's plan-then-execute
split.
"""

from __future__ import annotations

import html as _html

from .executor import Manifest, StepStatus
from .schema import GenerationPlan

_STATUS_STYLE = {
    "ok": ("완료", "#e6f6ec", "#0a7a3d"),
    "error": ("실패", "#fde8e8", "#b42318"),
    "skipped": ("건너뜀", "#f0f0f2", "#5b5b63"),
}


def _e(text: object) -> str:
    return _html.escape(str(text), quote=True)


def _badge(status: str) -> str:
    label, bg, fg = _STATUS_STYLE.get(status, ("?", "#eee", "#333"))
    return f'<span class="badge" style="background:{bg};color:{fg}">{label}</span>'


def _image_or_placeholder(step: StepStatus, alt: str, css_class: str = "thumb") -> str:
    if step.status == "ok" and step.path:
        return f'<img class="{css_class}" src="{_e(step.path)}" loading="lazy" alt="{_e(alt)}">'
    return f'<div class="{css_class} placeholder">{_badge(step.status)}</div>'


def render_html(manifest: Manifest, plan: GenerationPlan, scenario: dict, trace_html_path: str | None = None) -> str:
    product_name = scenario.get("product", {}).get("name", "product")
    duration_s = scenario.get("duration_s", 0)
    entity_lookup: dict[str, dict] = {}
    for kind in ("characters", "locations", "props"):
        for e in scenario.get(kind, []):
            entity_lookup[e["id"]] = {**e, "_kind": kind}

    entity_cards = ""
    for c in plan.character_assets:
        step = manifest.entities.get(c.entity_id, StepStatus("skipped"))
        info = entity_lookup.get(c.entity_id, {})
        name = info.get("name", c.entity_id)
        entity_cards += f"""\
<div class="card">
  {_image_or_placeholder(step, name)}
  <h4>{_e(name)} <span class="id-tag">{_e(c.entity_id)}</span></h4>
  <p class="muted">character (face/body + clothing + hair grid)</p>
  {_badge(step.status)}
  {f'<p class="detail">{_e(step.detail)}</p>' if step.detail else ""}
</div>"""
    for e in plan.entity_images:
        step = manifest.entities.get(e.entity_id, StepStatus("skipped"))
        info = entity_lookup.get(e.entity_id, {})
        name = info.get("name", e.entity_id)
        tag = ""
        if e.is_product:
            tag = ' <span class="id-tag">PRODUCT</span>'
        elif e.is_logo:
            tag = ' <span class="id-tag">LOGO</span>'
        entity_cards += f"""\
<div class="card">
  {_image_or_placeholder(step, name)}
  <h4>{_e(name)}{tag} <span class="id-tag">{_e(e.entity_id)}</span></h4>
  <p class="muted">{_e(e.entity_type)}</p>
  {_badge(step.status)}
  {f'<p class="detail">{_e(step.detail)}</p>' if step.detail else ""}
</div>"""

    scenes_by_no = {s["scene_no"]: s for s in scenario.get("scenes", [])}
    cut_cards = ""
    for sc in sorted(plan.scene_cuts, key=lambda sc: sc.scene_no):
        step = manifest.scene_cuts.get(sc.scene_no, StepStatus("skipped"))
        scene = scenes_by_no.get(sc.scene_no, {})
        in_final = sc.scene_no in plan.final_video.cut_scene_nos
        final_tag = ' <span class="id-tag final">FINAL CUT</span>' if in_final else ""
        groups = []
        if sc.character_ids:
            groups.append(f"인물 {len(sc.character_ids)}명")
        if sc.location_id:
            groups.append("장소 1")
        if sc.prop_ids:
            groups.append(f"소품 {len(sc.prop_ids)}개")
        cut_cards += f"""\
<div class="card cut-card">
  {_image_or_placeholder(step, f"scene {sc.scene_no}")}
  <h4>#{sc.scene_no}{final_tag} <span class="muted">{scene.get('start_s', '?')}s–{scene.get('end_s', '?')}s</span></h4>
  <p class="muted">참조 그룹: {', '.join(groups) or '없음'}</p>
  <p>{_e(scene.get('action', ''))}</p>
  {_badge(step.status)}
  {f'<p class="detail">{_e(step.detail)}</p>' if step.detail else ""}
</div>"""

    fv = plan.final_video
    anchor_step = manifest.anchor_frame
    anchor_block = f"""\
<div class="card">
  {_image_or_placeholder(anchor_step, "anchor frame", css_class="thumb anchor-thumb")}
  <h4>브랜드 앵커 프레임</h4>
  <p class="muted">인물: {', '.join(fv.anchor_character_ids) or '없음'} · 제품/로고: {', '.join(fv.anchor_prop_ids) or '없음'}</p>
  {_badge(anchor_step.status)}
  {f'<p class="detail">{_e(anchor_step.detail)}</p>' if anchor_step.detail else ""}
</div>"""

    fv_step = manifest.final_video
    if fv_step.status == "ok" and fv_step.path:
        video_block = f'<video controls class="final-video" src="{_e(fv_step.path)}"></video>'
    else:
        video_block = f'<div class="final-video placeholder">{_badge(fv_step.status)}<p>{_e(fv_step.detail)}</p></div>'

    health_line = (
        f"ComfyUI: {'연결됨' if manifest.comfy_available else '연결 불가'} — {_e(manifest.health)}"
    )
    trace_link = (
        f'<div class="health"><a href="{_e(trace_html_path)}">계획 수립 실행 로그(trace.html) 보기 →</a></div>'
        if trace_html_path else ""
    )

    return f"""\
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(product_name)} — 영상 생성 결과</title>
<style>
  :root {{ --bg:#f7f7f8; --card:#fff; --text:#1a1a1e; --muted:#6b6b72; --border:#e6e6e9; --accent:#3454d1; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font-family:-apple-system,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif; line-height:1.55; }}
  .wrap {{ max-width: 1080px; margin:0 auto; padding:32px 20px 80px; }}
  header.hero {{ background:linear-gradient(135deg,var(--accent),#7c3aed); color:#fff; border-radius:16px;
    padding:28px 26px; margin-bottom:24px; }}
  header.hero h1 {{ margin:0 0 6px; font-size:24px; }}
  header.hero a {{ color:#fff; }}
  .health {{ font-size:13px; opacity:.9; margin-top:6px; }}
  section {{ margin-bottom:32px; }}
  h2 {{ font-size:18px; border-bottom:2px solid var(--border); padding-bottom:8px; margin-bottom:14px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:14px; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:12px; }}
  .card h4 {{ margin:8px 0 4px; font-size:13.5px; }}
  .card p {{ margin:2px 0; font-size:12.5px; }}
  .thumb {{ width:100%; aspect-ratio:1/1; object-fit:cover; border-radius:8px; background:#eee; display:block; }}
  .cut-card .thumb {{ aspect-ratio:16/9; }}
  .anchor-thumb {{ aspect-ratio:16/9; }}
  .thumb.placeholder {{ display:flex; align-items:center; justify-content:center; }}
  .id-tag {{ font-size:10.5px; color:var(--muted); background:var(--bg); border-radius:6px; padding:1px 6px; margin-left:4px; }}
  .id-tag.final {{ color:#0a7a3d; background:#e6f6ec; font-weight:700; }}
  .muted {{ color:var(--muted); }}
  .detail {{ color:#b42318; font-size:11.5px; }}
  .badge {{ display:inline-block; font-size:11px; font-weight:700; padding:2px 8px; border-radius:999px; }}
  .final-video {{ width:100%; max-width:640px; border-radius:12px; background:#000; display:block; }}
  .final-video.placeholder {{ display:flex; flex-direction:column; gap:8px; align-items:flex-start;
    justify-content:center; padding:20px; border:1px dashed var(--border); border-radius:12px; min-height:120px; }}
  footer {{ text-align:center; color:var(--muted); font-size:12px; margin-top:40px; }}
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <h1>{_e(product_name)} — 영상 생성 결과</h1>
    <div>{_e(duration_s)}초 광고 · ComfyUI (Flux / Qwen-Edit / MiniMax H3)</div>
    <div class="health">{health_line}</div>
    {trace_link}
  </header>

  <section>
    <h2>1. 등장 요소 레퍼런스 이미지</h2>
    <div class="grid">{entity_cards}</div>
  </section>

  <section>
    <h2>2. 컷별 프레임 (스토리보드)</h2>
    <div class="grid">{cut_cards}</div>
  </section>

  <section>
    <h2>3. 브랜드 앵커 프레임</h2>
    <p class="muted">MiniMax reference_image로 쓰이는 단일 합성 이미지 — 제품/로고 정확성과 주인공 얼굴 정체성을 한 장에 담는다.</p>
    <div class="grid">{anchor_block}</div>
  </section>

  <section>
    <h2>4. 최종 영상</h2>
    <p class="muted">{_e(fv.prompt)}</p>
    <p class="muted">사용된 컷: {', '.join('#' + str(n) for n in fv.cut_scene_nos)} · {_e(fv.aspect_ratio)}</p>
    {video_block}
  </section>

  <footer>video_gen_claude_p_v2 자동 생성 결과 — 촬영/합성 전 사람 검토 필요</footer>
</div>
</body>
</html>
"""

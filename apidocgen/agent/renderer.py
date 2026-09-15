"""Render use-case specifications as a self-contained RTL HTML document."""
from __future__ import annotations

import html as _html
from typing import Any, List

from ..config import Config
from .models import AlternateFlow, FlowStep, UseCaseSpec


def _esc(s: str) -> str:
    return _html.escape(s or "")


def _mark(is_new: bool) -> str:
    return ' class="new-req"' if is_new else ""


def _list(items: List[str], impacts: List[str] | None = None) -> str:
    if not items:
        return '<p class="muted">—</p>'
    impacts = impacts or []
    lis = []
    for x in items:
        cls = ' class="new-req"' if x in impacts else ""
        lis.append(f"<li{cls}>{_esc(x)}</li>")
    return "<ul>" + "".join(lis) + "</ul>"


def _steps(steps: List[FlowStep]) -> str:
    if not steps:
        return '<p class="muted">—</p>'
    rows = [
        f"<tr{_mark(s.is_new)}><td>{s.number}</td><td>{_esc(s.actor)}</td><td>{_esc(s.action)}</td></tr>"
        for s in steps
    ]
    return '<table class="t"><tr><th>شماره</th><th>عامل</th><th>اقدام</th></tr>' + "".join(rows) + "</table>"


def _alts(flows: List[AlternateFlow], title: str) -> str:
    if not flows:
        return ""
    blocks = [f"<h4>{_esc(title)}</h4>"]
    for flow in flows:
        extra = " new-req" if flow.is_new else ""
        blocks.append(
            f'<div class="flow{extra}"><b>{_esc(flow.name)}</b>'
            f'<div class="muted">شرط: {_esc(flow.condition)}</div>{_list(flow.steps)}</div>'
        )
    return "".join(blocks)


def render_ucs_html(cfg: Config, specs: List[UseCaseSpec], req: Any) -> str:
    system = _esc(cfg.get("project", "name") or cfg.get("doc", "system_name") or "")
    requirement = getattr(req, "new_requirement", "") or ""
    previous = getattr(req, "previous_analysis", "") or ""
    mode = getattr(req, "mode", "new")
    banner = ""
    if requirement.strip():
        banner = (
            '<section class="banner new-req"><h2>نیازمندی جدید</h2>'
            f"<p>{_esc(requirement.strip())}</p>"
            '<p class="hint">بندهایی که با رنگ متمایز مشخص شده‌اند تحت تأثیر این نیازمندی هستند.</p></section>'
        )
    prev = ""
    if previous.strip():
        prev = f'<section class="card"><h2>تحلیل قبلی</h2><pre>{_esc(previous.strip())}</pre></section>'
    cards = []
    for i, uc in enumerate(specs, 1):
        cards.append(f"""
<article class="uc" id="{_esc(uc.use_case_id)}">
  <header>
    <span class="id">{_esc(uc.use_case_id)}</span>
    <h2>{i}. {_esc(uc.name)}</h2>
    <div class="meta"><code>{_esc(uc.endpoint_id)}</code></div>
  </header>
  <p>{_esc(uc.description)}</p>
  <div class="kv"><div>بازیگران</div><div>{_esc("، ".join(uc.actors) or "—")}</div>
  <div>محرک</div><div>{_esc(uc.trigger)}</div></div>
  <h3>پیش‌شرط‌ها</h3>{_list(uc.preconditions, uc.new_requirement_impacts)}
  <h3>پس‌شرط‌ها</h3>{_list(uc.postconditions, uc.new_requirement_impacts)}
  <h3>جریان اصلی موفقیت</h3>{_steps(uc.main_flow)}
  {_alts(uc.alternative_flows, "جریان‌های جایگزین")}
  {_alts(uc.exception_flows, "جریان‌های استثنا")}
  <h3>قواعد کسب‌وکار</h3>{_list(uc.business_rules, uc.new_requirement_impacts)}
  <h3>نیازمندی‌های ویژه</h3>{_list(uc.special_requirements, uc.new_requirement_impacts)}
  {"<h3>اثر نیازمندی جدید</h3>" + _list(uc.new_requirement_impacts) if uc.new_requirement_impacts else ""}
  <h3>متدهای مرتبط (از کنترلر)</h3>
  <ul class="mono">{"".join(f"<li>{_esc(m)}</li>" for m in uc.related_methods) or '<li class="muted">—</li>'}</ul>
</article>""")
    toc = "".join(f'<li><a href="#{_esc(uc.use_case_id)}">{_esc(uc.name)}</a></li>' for uc in specs)
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<title>مشخصات موارد کاربرد — {system}</title>
<style>
:root {{ --green:#2C673E; --new:#c45c16; --new-bg:#fff4e5; --border:#d9e1dc; --muted:#66716b; }}
body {{ font: 15px/1.7 Vazirmatn, Tahoma, sans-serif; margin: 0; background:#f6f8f7; color:#1d2420; }}
main {{ max-width: 920px; margin: 0 auto; padding: 28px 20px 80px; }}
h1 {{ color: var(--green); }}
.banner, .card, .uc {{ background:#fff; border:1px solid var(--border); border-radius:12px; padding:18px 22px; margin: 16px 0; }}
.new-req, tr.new-req td, li.new-req, .flow.new-req {{ background: var(--new-bg) !important; box-shadow: inset 4px 0 0 var(--new); }}
.banner.new-req {{ border-color: var(--new); }}
.id {{ color: var(--green); font-weight: 700; }}
.meta code {{ direction: ltr; display:inline-block; }}
.kv {{ display:grid; grid-template-columns: 140px 1fr; gap: 6px 12px; }}
.kv div:nth-child(odd) {{ color: var(--muted); }}
table.t {{ width:100%; border-collapse: collapse; }}
table.t th, table.t td {{ border:1px solid var(--border); padding:6px 8px; text-align:right; }}
table.t th {{ background:#eef3f0; }}
.mono {{ direction:ltr; text-align:left; font-family: ui-monospace, Menlo, Consolas, monospace; font-size:12px; }}
.muted {{ color: var(--muted); }}
.hint {{ font-size: 13px; color: var(--new); }}
pre {{ white-space: pre-wrap; background:#f2f5f3; padding:12px; border-radius:8px; }}
</style>
</head>
<body>
<main>
  <h1>مشخصات موارد کاربرد</h1>
  <p class="muted">{system} · حالت: {"سرویس موجود" if mode == "existing" else "تحلیل جدید"} · {len(specs)} مورد کاربرد</p>
  {banner}{prev}
  <section class="card"><h2>فهرست</h2><ol>{toc or '<li class="muted">—</li>'}</ol></section>
  {''.join(cards) or '<p class="muted">مورد کاربردی یافت نشد. ابتدا اسکن کنید.</p>'}
</main>
</body>
</html>"""

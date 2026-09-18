"""HTML renderer for the Persian service document."""
from __future__ import annotations

import base64
import hashlib
import html as _html
import mimetypes
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import __version__
from ..config import Config
from ..docmodel.model import EndpointDoc
from ..graph.store import GraphStore
from ..util.jalali import today_jalali

TEMPLATES = Path(__file__).parent / "templates"

DEFAULT_INTRO = """<p>این مستند به منظور معرفی سرویس‌های <b>{system}</b> تهیه شده است و برای هر سرویس، آدرس و متد فراخوانی،
پارامترهای هدر، پارامترهای درخواست و پاسخ، ساختار خروجی موفق و ناموفق، مراحل فراخوانی و کدهای خطای مرتبط را شرح می‌دهد.</p>
<p>فیلدهای ستاره‌دار در جداول، اجباری هستند. مقادیر و نام فیلدها باید دقیقاً مطابق با جدول هر سرویس ارسال شوند؛ در غیر این صورت
سرویس با کد خطای مربوطه پاسخ می‌دهد.</p>
<p class="muted">این سند به صورت خودکار از روی کد منبع تولید شده است ({count} سرویس، تاریخ تولید {date}).</p>
"""


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _load_fragment(path: Optional[Path]) -> str:
    if not path or not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".md", ".markdown"):
        try:
            import markdown  # type: ignore

            return markdown.markdown(text, extensions=["tables"])
        except Exception:
            return "<pre>" + _html.escape(text) + "</pre>"
    return text


class HtmlRenderer:
    def __init__(self, cfg: Config, store: Optional[GraphStore] = None) -> None:
        self.cfg = cfg
        self.store = store
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "j2"]),
                               trim_blocks=False, lstrip_blocks=False)
        self.intro_override: Optional[str] = None

    # ------------------------------------------------------------------ meta
    def _font(self) -> Tuple[str, str, str]:
        """Return (link href, css, font stack)."""
        family = self.cfg.get("doc", "font_google") or ""
        font_file = self.cfg.resolve_path(self.cfg.get("doc", "font_file"))
        stack = "'Vazirmatn', 'Vazir', 'IRANSans', 'Sahel', Tahoma, 'Segoe UI', Arial, sans-serif"
        if font_file and font_file.exists():
            fmt = {"ttf": "truetype", "otf": "opentype", "woff": "woff", "woff2": "woff2"}.get(font_file.suffix.lower().lstrip("."), "truetype")
            css = f"@font-face {{ font-family: 'DocFont'; src: url({_data_uri(font_file)}) format('{fmt}'); font-weight: 100 900; }}"
            return "", css, "'DocFont', " + stack
        if family:
            link = f"https://fonts.googleapis.com/css2?family={family.replace(' ', '+')}:wght@300;400;500;700;900&display=swap"
            return link, "", f"'{family}', " + stack
        return "", "", stack

    def build_meta(self, docs: List[EndpointDoc]) -> Dict[str, Any]:
        d = self.cfg.get("doc", default={}) or {}
        date = d.get("date") or "auto"
        if str(date).lower() == "auto":
            date = today_jalali()
        link, css, stack = self._font()
        logo_path = self.cfg.resolve_path(d.get("logo"))
        logo = _data_uri(logo_path) if logo_path and logo_path.exists() else ""
        intro = _load_fragment(self.cfg.resolve_path(d.get("intro_file"))) or (self.intro_override or "")
        if not intro:
            intro = DEFAULT_INTRO.format(system=_html.escape(d.get("system_name", "")), count=len(docs), date=date)
        appendix = _load_fragment(self.cfg.resolve_path(d.get("appendix_file")))
        changelog = list(d.get("changelog", []) or [])
        auto_row = self._auto_changelog(docs, date, d)
        if auto_row:
            changelog.append(auto_row)
        return {
            "title": d.get("title", ""), "system_name": d.get("system_name", ""), "organization": d.get("organization", ""),
            "classification": d.get("classification", "عمومی"), "author": d.get("author", ""), "date": date,
            "version": d.get("version", "1.0"), "logo": logo, "font_link": link, "font_css": css, "font_stack": stack,
            "identity": d.get("identity", {}) or {}, "approvals": d.get("approvals", []) or [], "changelog": changelog,
            "intro_html": intro, "appendix_html": appendix, "footer": d.get("footer", ""),
            "show_samples": bool(d.get("show_samples", True)), "show_call_steps": bool(d.get("show_call_steps", False)),
            "show_error_table": bool(d.get("show_error_table", True)), "show_status": bool(d.get("show_analysis_status", False)),
            "generator_version": __version__, "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        }

    def _auto_changelog(self, docs: List[EndpointDoc], date: str, d: Dict[str, Any]) -> Optional[Dict[str, str]]:
        if not d.get("changelog_auto", True) or self.store is None:
            return None
        prev = self.store.get_snapshots()
        current = {doc.spec.id: (hashlib.sha256(doc.doc_hash_source().encode("utf-8")).hexdigest(), doc.title) for doc in docs}
        if not prev:
            return None
        added = [t for eid, (h, t) in current.items() if eid not in prev]
        changed = [t for eid, (h, t) in current.items() if eid in prev and prev[eid][0] != h]
        removed = [t for eid, (h, t) in prev.items() if eid not in current]
        parts = []
        if added:
            parts.append("افزودن سرویس‌های: " + "، ".join(added))
        if changed:
            parts.append("به‌روزرسانی سرویس‌های: " + "، ".join(changed))
        if removed:
            parts.append("حذف سرویس‌های: " + "، ".join(removed))
        if not parts:
            return None
        return {"date": date, "version": d.get("version", ""), "editor": d.get("author", "") or "تولید خودکار",
                "description": "؛ ".join(parts)}

    def save_snapshots(self, docs: List[EndpointDoc]) -> None:
        if self.store is None:
            return
        self.store.save_snapshots({doc.spec.id: (hashlib.sha256(doc.doc_hash_source().encode("utf-8")).hexdigest(), doc.title)
                                   for doc in docs})

    # ------------------------------------------------------------------ numbering
    @staticmethod
    def _prepare(docs: List[EndpointDoc], endpoint_analyses: Optional[Dict[str, Dict[str, Any]]] = None,
                 show_samples: bool = True, show_call_steps: bool = True) -> List[Dict[str, Any]]:
        table_no = 0
        tables_index: List[Dict[str, Any]] = []
        for doc in docs:
            table_no += 1
            doc.table_numbers = {"main": table_no}  # type: ignore[attr-defined]
            tables_index.append({"number": table_no, "title": f"ورودی و خروجی {doc.title}", "anchor": f"{doc.anchor}-tbl"})
            doc.request_tables = [t for t in doc.nested_tables if t.side == "request"]  # type: ignore[attr-defined]
            doc.response_tables = [t for t in doc.nested_tables if t.side == "response"]  # type: ignore[attr-defined]
            doc.failure_tables = [t for t in doc.nested_tables if t.side == "failure"]  # type: ignore[attr-defined]
            nums: Dict[str, int] = {}
            for t in doc.response_tables + doc.failure_tables:  # type: ignore[attr-defined]
                table_no += 1
                nums[t.qname] = table_no
                tables_index.append({"number": table_no, "title": f"{t.name} – {doc.title}", "anchor": f"{doc.anchor}-{t.name}"})
            doc.response_table_numbers = nums  # type: ignore[attr-defined]
            if doc.error_rows:
                table_no += 1
                doc.table_numbers["errors"] = table_no  # type: ignore[attr-defined]
                tables_index.append({"number": table_no, "title": f"لیست خطاهای {doc.title}", "anchor": f"{doc.anchor}-errors"})
            extra = []
            if endpoint_analyses and doc.spec.id in endpoint_analyses:
                extra = endpoint_analyses[doc.spec.id].get("response_fields_extra") or []
            doc.response_extra = extra if not doc.response_rows else []  # type: ignore[attr-defined]
            toc = [{"title": "پارامترهای ورودی و خروجی سرویس", "anchor": f"{doc.anchor}-params"}]
            if doc.business_rules:
                toc.append({"title": "شروط کسب‌وکار", "anchor": f"{doc.anchor}-rules"})
            if show_samples and doc.success_sample:
                toc.append({"title": "ساختار خروجی موفق", "anchor": f"{doc.anchor}-success"})
                if doc.failure_samples:
                    toc.append({"title": "ساختار خروجی ناموفق", "anchor": f"{doc.anchor}-failure"})
            if show_call_steps:
                toc.append({"title": "مراحل فراخوانی سرویس", "anchor": f"{doc.anchor}-steps"})
            doc.toc = toc  # type: ignore[attr-defined]
        return tables_index

    # ------------------------------------------------------------------ render
    def render(self, docs: List[EndpointDoc], endpoint_analyses: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
        d = self.cfg.get("doc", default={}) or {}
        tables_index = self._prepare(docs, endpoint_analyses, bool(d.get("show_samples", True)), bool(d.get("show_call_steps", False)))
        meta = self.build_meta(docs)
        tpl = self.env.get_template("document.html.j2")
        return tpl.render(docs=docs, meta=meta, tables_index=tables_index)

    def render_to_file(self, docs: List[EndpointDoc], out_path: Path,
                       endpoint_analyses: Optional[Dict[str, Dict[str, Any]]] = None) -> Path:
        html_text = self.render(docs, endpoint_analyses)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html_text, encoding="utf-8")
        self.save_snapshots(docs)
        return out_path

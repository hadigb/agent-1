"""Command line interface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional

from . import __version__
from .config import CONFIG_FILENAME, Config, write_template
from .pipeline import do_analyze, do_render, do_scan, do_ucs, impact, make_llm, status_info
from .project import Project


def _log(msg: str) -> None:
    print(msg, flush=True)


def _load(args: argparse.Namespace) -> Project:
    cfg = Config.load(args.config)
    return Project(cfg)


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path or ".") / CONFIG_FILENAME
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    write_template(path)
    print(f"wrote {path}\nEdit project.paths, llm.* and doc.* then run: apidocgen run")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    p = _load(args)
    do_scan(p, _log, force=args.force)
    return 0


def cmd_endpoints(args: argparse.Namespace) -> int:
    p = _load(args)
    eps = p.endpoints()
    if args.json:
        print(json.dumps([e.to_dict() for e in eps], ensure_ascii=False, indent=1))
        return 0
    if not eps:
        print("no endpoints detected (run `apidocgen scan` first, or check detectors / custom rules)")
        return 0
    w = max(len(e.id) for e in eps)
    for e in eps:
        body = e.body_type.canonical() if e.body_type else "-"
        resp = e.response_type.canonical() if e.response_type else "-"
        print(f"{e.id:<{w}}  [{e.framework}]  body={body}  response={resp}")
        if args.verbose:
            print(f"    handler: {e.handler_qname}")
            for prm in e.params:
                print(f"    - {prm.location:<7} {prm.name} : {prm.type.canonical()}{' *' if prm.required else ''}")
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    p = _load(args)
    st = p.store
    if args.graph_cmd == "stats":
        print(json.dumps(st.graph_stats(), ensure_ascii=False, indent=1))
    elif args.graph_cmd == "export":
        nodes = [{"id": s.qname, "kind": s.kind, "name": s.name, "type_kind": s.type_kind, "file": s.file_path,
                  "parent": s.parent, "line": s.start_line} for s in st.all_symbols()]
        edges = [{"src": a, "dst": b, "kind": k, "meta": m} for a, b, k, m in st.all_edges()]
        if args.format == "dot":
            lines = ["digraph code {", "  rankdir=LR; node [shape=box, fontsize=9];"]
            for n in nodes:
                if n["kind"] == "type" or args.all:
                    lines.append(f'  "{n["id"]}" [label="{n["name"]}", color="{ {"type": "darkgreen", "method": "gray40", "field": "gray", "constant": "orange"}[n["kind"]] }"];')
            for e in edges:
                if e["dst"].startswith("ext:") and not args.all:
                    continue
                if not args.all and e["kind"] not in ("extends", "implements", "field_type", "returns", "param_type", "calls", "uses_const"):
                    continue
                lines.append(f'  "{e["src"]}" -> "{e["dst"]}" [label="{e["kind"]}", fontsize=7];')
            lines.append("}")
            text = "\n".join(lines)
        else:
            text = json.dumps({"nodes": nodes, "edges": edges, "endpoints": [r.data for r in st.list_endpoints()]},
                              ensure_ascii=False, indent=1)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"wrote {args.out} ({len(nodes)} nodes, {len(edges)} edges)")
        else:
            print(text)
    elif args.graph_cmd == "impact":
        res = impact(p, args.files)
        if not res:
            print("no endpoint depends on the given files")
        for eid, files in res.items():
            print(f"{eid}\n    via " + ", ".join(files))
    elif args.graph_cmd == "deps":
        q = args.symbol
        sym = st.get_symbol(q)
        if sym is None:
            cands = [s.qname for s in st.find_symbols(name_like=f"%{q}%")][:20]
            print("symbol not found; candidates: " + ", ".join(cands))
            return 1
        print(f"{sym.kind} {sym.qname} ({sym.file_path}:{sym.start_line})")
        print("outgoing:")
        for dst, kind, meta in st.edges_from(q):
            print(f"  --{kind}--> {dst}" + (f" {meta}" if meta else ""))
        print("incoming:")
        for src, kind, meta in st.edges_to(q):
            print(f"  <--{kind}-- {src}" + (f" {meta}" if meta else ""))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    p = _load(args)
    if args.scan:
        do_scan(p, _log)
    client = None if args.dry_run else make_llm(p.cfg, args.provider)
    if args.model and client is not None:
        client.model = args.model
    res = do_analyze(p, client, _log, dry_run=args.dry_run, only=args.only, prune=args.prune)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if not res.get("failed_units") else 2


def cmd_render(args: argparse.Namespace) -> int:
    p = _load(args)
    res = do_render(p, _log, out=args.out, only=args.only, archive=not args.no_archive, label=args.label or "")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    p = _load(args)
    do_scan(p, _log)
    client = None if args.no_llm else make_llm(p.cfg, args.provider)
    if args.model and client is not None:
        client.model = args.model
    do_analyze(p, client, _log, dry_run=args.no_llm, only=args.only)
    do_render(p, _log, out=args.out, only=args.only, label=args.label or "")
    return 0


def cmd_ucs(args: argparse.Namespace) -> int:
    p = _load(args)
    client = None if args.dry_run else make_llm(p.cfg, args.provider)
    if args.model and client is not None:
        client.model = args.model
    res = do_ucs(p, client, _log, mode=args.mode, endpoint_id=args.endpoint, previous_analysis=args.previous or "",
                 new_requirement=args.requirement or "", dry_run=args.dry_run, out=args.out)
    if args.json:
        print(json.dumps({k: v for k, v in res.items() if k != "html"}, ensure_ascii=False, indent=1))
    return 0 if not res.get("failed") else 2


def cmd_status(args: argparse.Namespace) -> int:
    p = _load(args)
    info = status_info(p)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=1, default=str))
        return 0
    g, c, u = info["graph"], info["cache"], info["usage"]
    print(f"config:   {info['config']}\ndb:       {info['db']}")
    print(f"graph:    {g['files']} files, {g['symbols']} symbols, {g['edges']} edges, {g['endpoints']} endpoints")
    print(f"cache:    {c['entries']} analyses ({c['by_kind']}), {c['hits']} hits so far")
    print(f"usage:    {u['calls']} LLM calls, {u['input_tokens']} input / {u['output_tokens']} output tokens, "
          f"{u['cache_read_tokens']} prompt-cache read, {u['failed']} failed")
    for row in info["usage_by_model"]:
        print(f"          {row['provider']}/{row['model']}: {row['calls']} calls, {row['input_tokens']} in, {row['output_tokens']} out")
    if info["parse_errors"]:
        print(f"parse warnings in {len(info['parse_errors'])} files (see `apidocgen status --json`)")
    return 0


def cmd_cache(args: argparse.Namespace) -> int:
    p = _load(args)
    if args.cache_cmd == "stats":
        print(json.dumps(p.store.cache_stats(), ensure_ascii=False, indent=1))
    elif args.cache_cmd == "clear":
        n = p.store.cache_clear(unit_kind=args.kind, unit_id=args.unit)
        print(f"removed {n} cache entries")
    elif args.cache_cmd == "prune":
        from .analysis.analyzer import Analyzer

        an = Analyzer(p, None)
        n = an.prune_cache(an.plan())
        print(f"pruned {n} stale cache entries")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from .analysis.discover import discover_endpoints, write_endpoints_file

    p = _load(args)
    client = make_llm(p.cfg, args.provider)
    eps = discover_endpoints(p, client, class_regex=args.class_regex, progress=_log, min_confidence=args.min_confidence)
    if not eps:
        print("nothing discovered")
        return 0
    for e in eps:
        print(f"  {e['method']} {e['path']}  <- {e['handler']}  (confidence {e.get('confidence', 1):.2f})")
    out = Path(args.out) if args.out else (p.cfg.resolve_path(p.cfg.get("project", "endpoints_file")) or (p.cfg.root / "endpoints.yaml"))
    n = write_endpoints_file(out, eps, merge=True)
    print(f"wrote {n} new entries to {out}. Review the file, then set project.endpoints_file: {out.name} and re-run `apidocgen scan`.")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .ui.server import serve

    return serve(config=args.config, workspace=args.workspace, host=args.host, port=args.port, open_browser=not args.no_open)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="apidocgen", description="Scan Java code, keep a code graph and generate Persian API documents with cached LLM help.")
    ap.add_argument("--version", action="version", version=f"apidocgen {__version__}")
    ap.add_argument("-c", "--config", default=None, help=f"path to {CONFIG_FILENAME} (default: ./{CONFIG_FILENAME})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write a commented configuration template")
    s.add_argument("path", nargs="?")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("scan", help="scan the configured paths and (re)build the code graph")
    s.add_argument("--force", action="store_true", help="re-parse every file even if unchanged")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("endpoints", help="list detected endpoints")
    s.add_argument("--json", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_endpoints)

    s = sub.add_parser("graph", help="code graph queries and export")
    gs = s.add_subparsers(dest="graph_cmd", required=True)
    gs.add_parser("stats")
    e = gs.add_parser("export")
    e.add_argument("--format", choices=["json", "dot"], default="json")
    e.add_argument("--out")
    e.add_argument("--all", action="store_true", help="include members and unresolved/external nodes")
    i = gs.add_parser("impact", help="which endpoints depend on the given files")
    i.add_argument("files", nargs="+")
    d = gs.add_parser("deps", help="show edges of a symbol (qualified name)")
    d.add_argument("symbol")
    s.set_defaults(func=cmd_graph)

    s = sub.add_parser("analyze", help="send un-analysed code units to the LLM (cache aware)")
    s.add_argument("--dry-run", action="store_true", help="only report what would be sent")
    s.add_argument("--scan", action="store_true", help="scan first")
    s.add_argument("--only", nargs="*", help="endpoint id patterns (fnmatch), e.g. 'POST /API/*'")
    s.add_argument("--provider", help="override llm.provider for this run")
    s.add_argument("--model", help="override llm.model for this run")
    s.add_argument("--prune", action="store_true", help="drop cache entries for code that no longer exists in this form")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("render", help="render the HTML document from the graph and cached analyses (no LLM calls)")
    s.add_argument("--out")
    s.add_argument("--only", nargs="*")
    s.add_argument("--no-archive", action="store_true")
    s.add_argument("--label", help="label stored with the archive entry")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("run", help="scan + analyze + render")
    s.add_argument("--no-llm", action="store_true", help="skip the LLM (use cached / deterministic descriptions)")
    s.add_argument("--only", nargs="*")
    s.add_argument("--out")
    s.add_argument("--provider")
    s.add_argument("--model")
    s.add_argument("--label")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("ucs", help="scan then produce use-case specifications (senior analyst agent)")
    s.add_argument("--mode", choices=["new", "existing"], default="new")
    s.add_argument("--endpoint", help="endpoint id or path when mode=existing")
    s.add_argument("--previous", default="", help="previous analysis text (existing service)")
    s.add_argument("--requirement", default="", help="new requirement to overlay")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--provider")
    s.add_argument("--model")
    s.add_argument("--out")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_ucs)

    s = sub.add_parser("status", help="graph, cache and token usage statistics")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("cache", help="manage the LLM analysis cache")
    cs = s.add_subparsers(dest="cache_cmd", required=True)
    cs.add_parser("stats")
    c = cs.add_parser("clear")
    c.add_argument("--kind", choices=["type", "endpoint", "discover"])
    c.add_argument("--unit", help="unit id (type qname or endpoint id)")
    cs.add_parser("prune")
    s.set_defaults(func=cmd_cache)

    s = sub.add_parser("discover", help="LLM-assisted discovery of endpoints in classes without framework annotations")
    s.add_argument("--class-regex", default=r".*(Handler|Service|Resource|Api|Endpoint|Controller|Processor|Action)$")
    s.add_argument("--min-confidence", type=float, default=0.5)
    s.add_argument("--provider")
    s.add_argument("--out", help="endpoints yaml to write (default: project.endpoints_file or ./endpoints.yaml)")
    s.set_defaults(func=cmd_discover)

    s = sub.add_parser("ui", help="start the local web UI")
    s.add_argument("--workspace", help="optional workspace.yaml (default: persistent project list)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-open", action="store_true")
    s.set_defaults(func=cmd_ui)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:  # output piped into head etc.
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

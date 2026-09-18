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
    return Project(Config.load(args.config))


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
    do_scan(_load(args), _log, force=args.force)
    return 0


def cmd_endpoints(args: argparse.Namespace) -> int:
    endpoints = _load(args).endpoints()
    if args.json:
        print(json.dumps([endpoint.to_dict() for endpoint in endpoints], ensure_ascii=False, indent=1))
        return 0
    if not endpoints:
        print("no endpoints detected (run `apidocgen scan` first, or check detectors / custom rules)")
        return 0
    width = max(len(endpoint.id) for endpoint in endpoints)
    for endpoint in endpoints:
        body = endpoint.body_type.canonical() if endpoint.body_type else "-"
        response = endpoint.response_type.canonical() if endpoint.response_type else "-"
        print(f"{endpoint.id:<{width}}  [{endpoint.framework}]  body={body}  response={response}")
        if not args.verbose:
            continue
        print(f"    handler: {endpoint.handler_qname}")
        for param in endpoint.params:
            required = " *" if param.required else ""
            print(f"    - {param.location:<7} {param.name} : {param.type.canonical()}{required}")
    return 0


_DOT_NODE_COLORS = {
    "type": "darkgreen",
    "method": "gray40",
    "field": "gray",
    "constant": "orange",
}
_INTERESTING_EDGE_KINDS = (
    "extends", "implements", "field_type", "returns", "param_type", "calls", "uses_const",
)
_MAX_SYMBOL_CANDIDATES = 20


def _graph_stats(project: Project, args: argparse.Namespace) -> int:
    print(json.dumps(project.store.graph_stats(), ensure_ascii=False, indent=1))
    return 0


def _graph_export(project: Project, args: argparse.Namespace) -> int:
    store = project.store
    nodes = [
        {
            "id": symbol.qname,
            "kind": symbol.kind,
            "name": symbol.name,
            "type_kind": symbol.type_kind,
            "file": symbol.file_path,
            "parent": symbol.parent,
            "line": symbol.start_line,
        }
        for symbol in store.all_symbols()
    ]
    edges = [
        {"src": src, "dst": dest, "kind": kind, "meta": meta}
        for src, dest, kind, meta in store.all_edges()
    ]
    if args.format == "dot":
        text = _render_dot(nodes, edges, include_all=args.all)
    else:
        payload = {
            "nodes": nodes,
            "edges": edges,
            "endpoints": [row.data for row in store.list_endpoints()],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(nodes)} nodes, {len(edges)} edges)")
    else:
        print(text)
    return 0


def _render_dot(nodes: List[dict], edges: List[dict], include_all: bool) -> str:
    lines = ["digraph code {", "  rankdir=LR; node [shape=box, fontsize=9];"]
    for node in nodes:
        if node["kind"] == "type" or include_all:
            color = _DOT_NODE_COLORS[node["kind"]]
            lines.append(f'  "{node["id"]}" [label="{node["name"]}", color="{color}"];')
    for edge in edges:
        if not include_all and (edge["dst"].startswith("ext:") or edge["kind"] not in _INTERESTING_EDGE_KINDS):
            continue
        lines.append(f'  "{edge["src"]}" -> "{edge["dst"]}" [label="{edge["kind"]}", fontsize=7];')
    lines.append("}")
    return "\n".join(lines)


def _graph_impact(project: Project, args: argparse.Namespace) -> int:
    result = impact(project, args.files)
    if not result:
        print("no endpoint depends on the given files")
    for endpoint_id, files in result.items():
        print(f"{endpoint_id}\n    via " + ", ".join(files))
    return 0


def _graph_deps(project: Project, args: argparse.Namespace) -> int:
    store = project.store
    symbol_name = args.symbol
    symbol = store.get_symbol(symbol_name)
    if symbol is None:
        candidates = [
            row.qname for row in store.find_symbols(name_like=f"%{symbol_name}%")
        ][:_MAX_SYMBOL_CANDIDATES]
        print("symbol not found; candidates: " + ", ".join(candidates))
        return 1
    print(f"{symbol.kind} {symbol.qname} ({symbol.file_path}:{symbol.start_line})")
    print("outgoing:")
    for dest, kind, meta in store.edges_from(symbol_name):
        extra = f" {meta}" if meta else ""
        print(f"  --{kind}--> {dest}{extra}")
    print("incoming:")
    for src, kind, meta in store.edges_to(symbol_name):
        extra = f" {meta}" if meta else ""
        print(f"  <--{kind}-- {src}{extra}")
    return 0


_GRAPH_SUBCOMMANDS = {
    "stats": _graph_stats,
    "export": _graph_export,
    "impact": _graph_impact,
    "deps": _graph_deps,
}


def cmd_graph(args: argparse.Namespace) -> int:
    handler = _GRAPH_SUBCOMMANDS.get(args.graph_cmd)
    if handler is None:
        return 0
    return handler(_load(args), args)


def _maybe_override_model(client, model: Optional[str]):
    if model and client is not None:
        client.model = model
    return client


def cmd_analyze(args: argparse.Namespace) -> int:
    project = _load(args)
    if args.scan:
        do_scan(project, _log)
    client = None if args.dry_run else make_llm(project.cfg, args.provider)
    client = _maybe_override_model(client, args.model)
    result = do_analyze(
        project, client, _log, dry_run=args.dry_run, only=args.only, prune=args.prune,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if not result.get("failed_units") else 2


def cmd_render(args: argparse.Namespace) -> int:
    do_render(
        _load(args),
        _log,
        out=args.out,
        only=args.only,
        archive=not args.no_archive,
        label=args.label or "",
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    project = _load(args)
    do_scan(project, _log)
    client = None if args.no_llm else make_llm(project.cfg, args.provider)
    client = _maybe_override_model(client, args.model)
    do_analyze(project, client, _log, dry_run=args.no_llm, only=args.only)
    do_render(project, _log, out=args.out, only=args.only, label=args.label or "")
    return 0


def cmd_ucs(args: argparse.Namespace) -> int:
    project = _load(args)
    client = None if args.dry_run else make_llm(project.cfg, args.provider)
    client = _maybe_override_model(client, args.model)
    result = do_ucs(
        project,
        client,
        _log,
        mode=args.mode,
        endpoint_id=args.endpoint,
        previous_analysis=args.previous or "",
        new_requirement=args.requirement or "",
        dry_run=args.dry_run,
        out=args.out,
    )
    if args.json:
        printable = {key: value for key, value in result.items() if key != "html"}
        print(json.dumps(printable, ensure_ascii=False, indent=1))
    return 0 if not result.get("failed") else 2


def cmd_status(args: argparse.Namespace) -> int:
    info = status_info(_load(args))
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=1, default=str))
        return 0
    graph, cache, usage = info["graph"], info["cache"], info["usage"]
    print(f"config:   {info['config']}\ndb:       {info['db']}")
    print(
        f"graph:    {graph['files']} files, {graph['symbols']} symbols, "
        f"{graph['edges']} edges, {graph['endpoints']} endpoints"
    )
    print(f"cache:    {cache['entries']} analyses ({cache['by_kind']}), {cache['hits']} hits so far")
    print(
        f"usage:    {usage['calls']} LLM calls, "
        f"{usage['input_tokens']} input / {usage['output_tokens']} output tokens, "
        f"{usage['cache_read_tokens']} prompt-cache read, {usage['failed']} failed"
    )
    for row in info["usage_by_model"]:
        print(
            f"          {row['provider']}/{row['model']}: {row['calls']} calls, "
            f"{row['input_tokens']} in, {row['output_tokens']} out"
        )
    if info["parse_errors"]:
        print(f"parse warnings in {len(info['parse_errors'])} files (see `apidocgen status --json`)")
    return 0


def cmd_cache(args: argparse.Namespace) -> int:
    project = _load(args)
    if args.cache_cmd == "stats":
        print(json.dumps(project.store.cache_stats(), ensure_ascii=False, indent=1))
    elif args.cache_cmd == "clear":
        removed = project.store.cache_clear(unit_kind=args.kind, unit_id=args.unit)
        print(f"removed {removed} cache entries")
    elif args.cache_cmd == "prune":
        from .analysis.analyzer import Analyzer

        analyzer = Analyzer(project, None)
        pruned = analyzer.prune_cache(analyzer.plan())
        print(f"pruned {pruned} stale cache entries")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from .analysis.discover import discover_endpoints, write_endpoints_file

    project = _load(args)
    client = make_llm(project.cfg, args.provider)
    endpoints = discover_endpoints(
        project,
        client,
        class_regex=args.class_regex,
        progress=_log,
        min_confidence=args.min_confidence,
    )
    if not endpoints:
        print("nothing discovered")
        return 0
    for endpoint in endpoints:
        confidence = endpoint.get("confidence", 1)
        print(
            f"  {endpoint['method']} {endpoint['path']}  <- {endpoint['handler']}  "
            f"(confidence {confidence:.2f})"
        )
    default_out = project.cfg.resolve_path(project.cfg.get("project", "endpoints_file"))
    out_path = Path(args.out) if args.out else (default_out or (project.cfg.root / "endpoints.yaml"))
    added = write_endpoints_file(out_path, endpoints, merge=True)
    print(
        f"wrote {added} new entries to {out_path}. Review the file, then set "
        f"project.endpoints_file: {out_path.name} and re-run `apidocgen scan`."
    )
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .ui.server import serve

    return serve(
        config=args.config,
        workspace=args.workspace,
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
    )


def _add_graph_command(subcommands: Any) -> None:
    graph = subcommands.add_parser("graph", help="code graph queries and export")
    queries = graph.add_subparsers(dest="graph_cmd", required=True)

    queries.add_parser("stats")

    export = queries.add_parser("export")
    export.add_argument("--format", choices=["json", "dot"], default="json")
    export.add_argument("--out")
    export.add_argument("--all", action="store_true", help="include members and unresolved/external nodes")

    impact_query = queries.add_parser("impact", help="which endpoints depend on the given files")
    impact_query.add_argument("files", nargs="+")

    deps = queries.add_parser("deps", help="show edges of a symbol (qualified name)")
    deps.add_argument("symbol")

    graph.set_defaults(func=cmd_graph)


def _add_cache_command(subcommands: Any) -> None:
    cache = subcommands.add_parser("cache", help="manage the LLM analysis cache")
    actions = cache.add_subparsers(dest="cache_cmd", required=True)

    actions.add_parser("stats")

    clear = actions.add_parser("clear")
    clear.add_argument("--kind", choices=["type", "endpoint", "discover"])
    clear.add_argument("--unit", help="unit id (type qname or endpoint id)")

    actions.add_parser("prune")

    cache.set_defaults(func=cmd_cache)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apidocgen",
        description="Scan Java code, keep a code graph and generate Persian API documents with cached LLM help.",
    )
    parser.add_argument("--version", action="version", version=f"apidocgen {__version__}")
    parser.add_argument(
        "-c", "--config",
        default=None,
        help=f"path to {CONFIG_FILENAME} (default: ./{CONFIG_FILENAME})",
    )
    subcommands = parser.add_subparsers(dest="cmd", required=True)

    init = subcommands.add_parser("init", help="write a commented configuration template")
    init.add_argument("path", nargs="?")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    scan = subcommands.add_parser("scan", help="scan the configured paths and (re)build the code graph")
    scan.add_argument("--force", action="store_true", help="re-parse every file even if unchanged")
    scan.set_defaults(func=cmd_scan)

    endpoints = subcommands.add_parser("endpoints", help="list detected endpoints")
    endpoints.add_argument("--json", action="store_true")
    endpoints.add_argument("-v", "--verbose", action="store_true")
    endpoints.set_defaults(func=cmd_endpoints)

    _add_graph_command(subcommands)

    analyze = subcommands.add_parser("analyze", help="send un-analysed code units to the LLM (cache aware)")
    analyze.add_argument("--dry-run", action="store_true", help="only report what would be sent")
    analyze.add_argument("--scan", action="store_true", help="scan first")
    analyze.add_argument("--only", nargs="*", help="endpoint id patterns (fnmatch), e.g. 'POST /API/*'")
    analyze.add_argument("--provider", help="override llm.provider for this run")
    analyze.add_argument("--model", help="override llm.model for this run")
    analyze.add_argument("--prune", action="store_true", help="drop cache entries for code that no longer exists")
    analyze.add_argument("--json", action="store_true")
    analyze.set_defaults(func=cmd_analyze)

    render = subcommands.add_parser("render", help="render the HTML document from the graph and cached analyses")
    render.add_argument("--out")
    render.add_argument("--only", nargs="*")
    render.add_argument("--no-archive", action="store_true")
    render.add_argument("--label", help="label stored with the archive entry")
    render.set_defaults(func=cmd_render)

    run = subcommands.add_parser("run", help="scan + analyze + render")
    run.add_argument("--no-llm", action="store_true", help="skip the LLM (use cached / deterministic descriptions)")
    run.add_argument("--only", nargs="*")
    run.add_argument("--out")
    run.add_argument("--provider")
    run.add_argument("--model")
    run.add_argument("--label")
    run.set_defaults(func=cmd_run)

    ucs = subcommands.add_parser("ucs", help="scan then produce use-case specifications")
    ucs.add_argument("--mode", choices=["new", "existing"], default="new")
    ucs.add_argument("--endpoint", help="endpoint id or path when mode=existing")
    ucs.add_argument("--previous", default="", help="previous analysis text (existing service)")
    ucs.add_argument("--requirement", default="", help="new requirement to overlay")
    ucs.add_argument("--dry-run", action="store_true")
    ucs.add_argument("--provider")
    ucs.add_argument("--model")
    ucs.add_argument("--out")
    ucs.add_argument("--json", action="store_true")
    ucs.set_defaults(func=cmd_ucs)

    status = subcommands.add_parser("status", help="graph, cache and token usage statistics")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    _add_cache_command(subcommands)

    discover = subcommands.add_parser(
        "discover",
        help="LLM-assisted discovery of endpoints in classes without framework annotations",
    )
    discover.add_argument(
        "--class-regex",
        default=r".*(Handler|Service|Resource|Api|Endpoint|Controller|Processor|Action)$",
    )
    discover.add_argument("--min-confidence", type=float, default=0.5)
    discover.add_argument("--provider")
    discover.add_argument("--out", help="endpoints yaml to write (default: project.endpoints_file or ./endpoints.yaml)")
    discover.set_defaults(func=cmd_discover)

    ui = subcommands.add_parser("ui", help="start the local web UI")
    ui.add_argument("--workspace", help="optional workspace.yaml (default: persistent project list)")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-open", action="store_true")
    ui.set_defaults(func=cmd_ui)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

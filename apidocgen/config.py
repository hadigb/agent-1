"""Configuration loading (YAML) with documented defaults."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

CONFIG_FILENAME = "apidocgen.yaml"

DEFAULTS: Dict[str, Any] = {
    "project": {
        "name": "سامانه بانکداری باز",
        "paths": ["./src/main/java"],
        "include": ["**/*.java"],
        "exclude": ["**/test/**", "**/tests/**", "**/build/**", "**/target/**", "**/out/**", "**/generated/**",
                    "**/generated-sources/**", "**/.git/**", "**/node_modules/**"],
        "db": ".apidocgen/graph.db",
        "endpoints_file": None,        # optional YAML with manually listed endpoints (see `discover`)
        "follow_symlinks": False,
    },
    "detectors": {
        "spring": True,
        "jaxrs": True,
        "servlet": True,
        "micronaut": False,
        "spring_unannotated_object_as_body": False,
        "custom": [],
    },
    "analysis": {
        "call_depth": 1,                      # how deep to follow calls when slicing an endpoint for the LLM
        "include_callee_bodies": True,        # include bodies of called project methods (up to call_depth)
        "max_slice_tokens": 6000,             # per endpoint slice budget (estimated tokens)
        "max_type_tokens": 2500,              # per DTO slice budget
        "type_batch_tokens": 9000,            # several DTO units are batched into one call up to this size
        "response_wrappers": None,            # None = built-in list (ResponseEntity, Mono, ...)
        "error_catalog_regex": r"(?i).*(ErrorCode|ErrorCodes|ResultCode|RsCode|ResponseCode|StatusCode|ErrorType|ErrorMessage).*",
        "exception_regex": r"(?i).*(Exception|Error)$",
        "envelope_success_fields": ["isSuccess", "success", "ok"],
        "envelope_code_fields": ["rsCode", "code", "status", "statusCode", "resultCode", "errorCode"],
        "envelope_message_fields": ["message", "msg", "description"],
        "envelope_errors_fields": ["errorList", "errors", "errorItems", "validationErrors"],
        "envelope_data_fields": ["resultData", "data", "result", "payload", "body", "content"],
        "cache_key_includes_model": False,    # True = switching models re-analyses everything
        "stale_fallback": True,               # when a unit changed and --no-llm: reuse the previous analysis
    },
    "llm": {
        "provider": "anthropic",              # anthropic | openai | ollama | mock
        "model": "claude-sonnet-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_key": None,
        "api_key_file": None,                # optional local secret file, relative to config
        "base_url": None,                     # openai: https://api.openai.com/v1 ; ollama: http://localhost:11434
        "temperature": 0.2,
        "max_output_tokens": 8000,
        "timeout_seconds": 180,
        "retries": 2,
        "prompt_cache": True,                 # Anthropic prompt caching for the static system prompt
        "json_mode": True,                    # OpenAI-compatible response_format=json_object (disable if unsupported)
        "max_calls_per_run": None,            # hard budget per `analyze` run
        "max_input_tokens_per_run": None,     # estimated budget per run
        "extra_headers": {},
        "language": "fa",
        "style_notes": "",                    # extra instructions appended to the system prompt (e.g. house terminology)
    },
    "doc": {
        "title": "مستند سرویس‌ها",
        "system_name": "سامانه بانکداری باز",
        "organization": "",
        "classification": "عمومی",
        "author": "",
        "date": "auto",                       # auto = today's Jalali date, or e.g. 1403/10/22
        "version": "1.0",
        "logo": None,                         # path to an image (embedded as data URI)
        "font_google": "Vazirmatn",           # Google Fonts family to link (needs internet when viewing)
        "font_file": None,                    # path to a .ttf/.woff2 to embed instead
        "base_url": "http://[IP]:[PORT]",
        "identity": {"domain": "سامانه بانکداری باز", "review_time": "", "obsolete_date": ""},
        "approvals": [
            {"role": "تدوین کننده", "name": "", "position": "", "date": ""},
            {"role": "تأییدکننده", "name": "", "position": "", "date": ""},
            {"role": "تصویب‌کننده", "name": "", "position": "", "date": ""},
        ],
        "changelog": [],
        "changelog_auto": True,
        "intro_file": None,                   # markdown or html file rendered as the introduction
        "intro_llm": False,                   # ask the LLM for a short overview when no intro file is given
        "appendix_file": None,
        "common_headers": [],
        "common_error_codes": [],
        "success_code": None,
        "validation_error": None,
        "failure_type": None,              # optional project-wide error DTO (qname or simple name)
        "call_steps_file": None,
        "show_call_steps": False,
        "show_samples": True,
        "show_error_table": True,
        "endpoint_order": "path",             # path | source | config
        "include": [],                        # endpoint id patterns to include (fnmatch) - empty = all
        "exclude": [],
        "footer": "",
        "output": "docs/api-document.html",
    },
    "overrides": {},                          # per endpoint id: title, description, notes, error_codes, samples ...
}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None) -> None:
        self.data = deep_merge(DEFAULTS, data or {})
        self.path = path
        self.root = path.parent.resolve() if path else Path.cwd().resolve()

    # convenient access ------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def resolve_path(self, value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        path = Path(os.path.expanduser(str(value)))
        return path if path.is_absolute() else (self.root / path).resolve()

    @property
    def db_path(self) -> Path:
        return self.resolve_path(self.get("project", "db")) or (self.root / ".apidocgen/graph.db")

    @property
    def scan_paths(self) -> List[Path]:
        return [self.resolve_path(p) for p in self.get("project", "paths", default=[]) or []]

    # loading ----------------------------------------------------------
    @staticmethod
    def load(path: Optional[str] = None) -> "Config":
        p = Path(path) if path else Path.cwd() / CONFIG_FILENAME
        if not p.exists():
            raise FileNotFoundError(f"config file not found: {p} (run `apidocgen init` first)")
        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return Config(data, p.resolve())

    def llm_api_key(self) -> Optional[str]:
        key = self.get("llm", "api_key")
        if key:
            return str(key)
        env = self.get("llm", "api_key_env")
        if env and os.environ.get(env):
            return os.environ[env]
        provider = (self.get("llm", "provider") or "").lower()
        for fallback in {"anthropic": ["ANTHROPIC_API_KEY"], "openai": ["OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"]}.get(provider, []):
            if os.environ.get(fallback):
                return os.environ[fallback]
        key_file = self.resolve_path(self.get("llm", "api_key_file"))
        if key_file is not None and key_file.exists():
            return key_file.read_text(encoding="utf-8").strip() or None
        return None


CONFIG_TEMPLATE = """# apidocgen configuration
# ---------------------------------------------------------------------------
# Everything here has a sensible default; the keys you will most likely edit
# are project.paths, llm.provider/model and the doc.* metadata.

project:
  name: "سامانه بانکداری باز"
  # Folders (or single files) to scan. Absolute paths or relative to this file.
  paths:
    - ./src/main/java
  include: ["**/*.java"]
  exclude: ["**/test/**", "**/build/**", "**/target/**", "**/generated/**"]
  db: .apidocgen/graph.db          # the code graph + LLM cache live here
  endpoints_file: null             # optional: endpoints.yaml written by `apidocgen discover` or by hand

detectors:
  spring: true                     # @RestController / @RequestMapping ... and RouterFunction routes
  jaxrs: true                      # @Path / @GET / @POST ... (Jersey, RESTEasy, Quarkus)
  servlet: true                    # HttpServlet subclasses / @WebServlet
  micronaut: false
  spring_unannotated_object_as_body: false
  custom: []
  # Example for an in-house dispatcher framework:
  # custom:
  #   - name: legacy
  #     class:  { annotation: ServiceHandler }         # or extends: BaseHandler / name_regex: ".*Handler$"
  #     method: { annotation: Handles }                 # or name_regex: "^execute$"
  #     http_method: { annotation_arg: method, default: POST }
  #     path: { annotation_arg: value, template: "/API/{class_name}", strip_suffix: Handler }
  #     request: { param_index: 0 }
  #     response: { return_type: true }

analysis:
  call_depth: 1                    # follow calls this deep when building an endpoint's code slice
  include_callee_bodies: true
  max_slice_tokens: 6000
  max_type_tokens: 2500
  type_batch_tokens: 9000
  error_catalog_regex: "(?i).*(ErrorCode|ErrorCodes|ResultCode|RsCode|ResponseCode|ErrorType).*"
  cache_key_includes_model: false  # true: switching llm.model re-analyses everything
  stale_fallback: true

llm:
  provider: anthropic              # anthropic | openai | ollama | mock
  model: claude-sonnet-5
  api_key_env: ANTHROPIC_API_KEY   # or set api_key: "..." (not recommended)
  base_url: null                   # openai-compatible servers: e.g. https://api.openai.com/v1 or http://localhost:1234/v1
  temperature: 0.2                 # set to null for models that do not accept it
  max_output_tokens: 8000
  prompt_cache: true               # Anthropic prompt caching for the static instructions
  json_mode: true                  # OpenAI-compatible response_format=json_object
  max_calls_per_run: null          # optional hard budget per `analyze` run
  max_input_tokens_per_run: null
  style_notes: ""                  # extra Persian style/terminology instructions for the model

doc:
  title: "مستند سرویس‌ها"
  system_name: "سامانه بانکداری باز"
  organization: "داتین"
  classification: "عمومی"
  author: "نام تهیه‌کننده"
  date: auto                       # today's Jalali date, or e.g. 1403/10/22
  version: "1.0"
  logo: null                       # e.g. ./logo.png
  font_google: Vazirmatn
  font_file: null                  # e.g. ./fonts/Vazirmatn-Regular.ttf (embedded, works offline)
  base_url: "http://[IP]:[PORT]"
  identity:
    domain: "سامانه بانکداری باز"
    review_time: ""
    obsolete_date: ""
  approvals:
    - { role: "تدوین کننده", name: "", position: "کارشناس", date: "" }
    - { role: "تأییدکننده", name: "", position: "کارشناس ارشد", date: "" }
    - { role: "تصویب‌کننده", name: "", position: "", date: "" }
  changelog: []                    # [{date: 1403/10/22, version: "1.1", editor: "...", description: "..."}]
  changelog_auto: true             # append a row describing endpoints added/changed since the last render
  intro_file: null                 # markdown/html file for the مقدمه section
  intro_llm: false
  appendix_file: null
  common_headers: []
  common_error_codes: []
  success_code: null
  failure_type: null               # کلاس خطای عمومی پروژه، مثلا BaseResponse
  show_call_steps: false
  show_samples: true
  show_error_table: true
  endpoint_order: path             # path | source
  include: []                      # e.g. ["POST /API/*"]
  exclude: []
  output: docs/api-document.html

# Manual corrections that survive regeneration, keyed by endpoint id ("METHOD /path"):
overrides: {}
#  "POST /API/IssueDocument":
#    title: "سرویس ثبت سند حسابداری"
#    notes: ["..."]
#    error_codes: [{code: 1038, title: "اطلاعات ورودی اشتباه است"}]
#    fields:                       # per wire-name description overrides
#      TransactionId: "شناسه یکتا تراکنش"
"""


def write_template(path: Path) -> None:
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")

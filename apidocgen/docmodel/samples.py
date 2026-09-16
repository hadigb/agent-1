"""Deterministic JSON sample generation from document rows."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .model import EnumTable, FieldRow, TypeTable

_ID_SAMPLE = "C5163-E322762b7c12c4f7a9b14bf5b15761f6e-20220131181208029-3391"


def _lower(s: str) -> str:
    return s.lower()


# ---------------------------------------------------------------------- #
# coerce_example: validate/convert an LLM-supplied example against the
# field's type. One small converter per type family, tried in order - the
# first whose type predicate matches wins (mirrors the original if/elif
# chain, just with each branch given a name).
# ---------------------------------------------------------------------- #
def _coerce_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    return {"true": True, "false": False}.get(str(value).lower())


def _coerce_int(value: Any) -> Any:
    if isinstance(value, bool):
        return None
    return int(str(value).replace(",", ""))


def _coerce_float(value: Any) -> Any:
    if isinstance(value, bool):
        return None
    f = float(str(value).replace(",", ""))
    return int(f) if f.is_integer() else f


def _coerce_string(value: Any) -> Any:
    return str(value) if not isinstance(value, (dict, list)) else None


def _coerce_map(value: Any) -> Any:
    return value if isinstance(value, dict) else None


def _coerce_list(value: Any) -> Any:
    return value if isinstance(value, list) else None


_COERCERS: List[Tuple[Callable[[str], bool], Callable[[Any], Any]]] = [
    (lambda t: t == "Boolean", _coerce_bool),
    (lambda t: t in ("Int", "Long", "BigInteger"), _coerce_int),
    (lambda t: t in ("Double", "BigDecimal"), _coerce_float),
    (lambda t: t.startswith("String"), _coerce_string),
    (lambda t: t.startswith("Map"), _coerce_map),
    (lambda t: t.startswith("List"), _coerce_list),
]


def coerce_example(value: Any, type_str: str, enum: Optional[EnumTable]) -> Any:
    """Validate/convert an LLM supplied example against the field type; None when unusable."""
    if value is None or value == "":
        return None
    try:
        if enum is not None:
            names = {v.name for v in enum.values}
            return value if str(value) in names else None
        for type_matches, coerce in _COERCERS:
            if type_matches(type_str):
                return coerce(value)
    except (ValueError, TypeError):
        return None
    return value


# ---------------------------------------------------------------------- #
# scalar_example: heuristic example value for a scalar row, based on the
# field's type and Persian/English naming conventions seen in banking APIs.
# Each type family's name-matching heuristics live in their own function so
# the family-to-family dispatch in scalar_example() stays short.
# ---------------------------------------------------------------------- #
def _int_example(n: str) -> Any:
    if "amount" in n or "mablagh" in n or "price" in n or "balance" in n:
        return 100000
    if "count" in n or "size" in n or "page" in n:
        return 1
    if "code" in n:
        return 1
    return 1


def _float_example(n: str) -> Any:
    if "amount" in n or "price" in n or "balance" in n:
        return 100000
    return 19


def _string_example(n: str) -> Any:
    if "transactionid" in n or n.endswith("id") and "transaction" in n:
        return _ID_SAMPLE
    if "date" in n or "time" in n or "tarikh" in n:
        return "1400/11/11 - 06:11:16"
    if "iban" in n or "sheba" in n or "shaba" in n:
        return "IR890570000198900015002802"
    if "deposit" in n or "account" in n:
        return "1.20.10504.1"
    if "mobile" in n or "phone" in n:
        return "09121234567"
    if "national" in n or "melli" in n:
        return "0012345678"
    if "email" in n:
        return "user@example.com"
    if "key" in n:
        return "key"
    if "value" in n:
        return "value"
    if "title" in n or "name" in n or "desc" in n or "comment" in n:
        return "شرح نمونه"
    if "code" in n:
        return "1"
    if n.endswith("id") or "identifier" in n or "serial" in n or "number" in n:
        return "12081651201"
    return "string"


def scalar_example(row: FieldRow, enums: Dict[str, EnumTable]) -> Any:
    """Heuristic example value for a scalar row."""
    if row.example not in (None, ""):
        coerced = coerce_example(row.example, row.type_str, enums.get(row.enum_qname) if row.enum_qname else None)
        if coerced is not None:
            return coerced
    t = row.type_str
    n = _lower(row.name)
    if row.enum_qname and row.enum_qname in enums and enums[row.enum_qname].values:
        return enums[row.enum_qname].values[0].name
    if t == "Boolean":
        return True
    if t in ("Int", "Long", "BigInteger"):
        return _int_example(n)
    if t in ("Double", "BigDecimal"):
        return _float_example(n)
    if t.startswith("String"):
        return _string_example(n)
    if t.startswith("Map"):
        return {"key": "value"}
    if t == "Object":
        return {}
    if t == "File":
        return "<binary>"
    return "string"


class SampleBuilder:
    def __init__(self, tables: Sequence[TypeTable], enums: Dict[str, EnumTable], max_depth: int = 5) -> None:
        self.tables = {t.qname: t for t in tables}
        self.enums = enums
        self.max_depth = max_depth

    def value_for_row(self, row: FieldRow, depth: int = 0, visiting: Optional[set] = None) -> Any:
        visiting = visiting or set()
        if row.nested_qname:
            if depth >= self.max_depth or row.nested_qname in visiting:
                inner: Any = {}
            else:
                table = self.tables.get(row.nested_qname)
                inner = self.object_for_rows(table.fields, depth + 1, visiting | {row.nested_qname}) if table else {}
            return [inner] if row.is_list else inner
        v = scalar_example(row, self.enums)
        return [v] if row.is_list else v

    def object_for_rows(self, rows: Sequence[FieldRow], depth: int = 0, visiting: Optional[set] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for r in rows:
            out[r.name] = self.value_for_row(r, depth, visiting)
        return out


def _find(rows: Sequence[FieldRow], names: Sequence[str]) -> Optional[FieldRow]:
    lowered = [n.lower() for n in names]
    for r in rows:
        if r.name.lower() in lowered or r.java_name.lower() in lowered:
            return r
    return None


# ---------------------------------------------------------------------- #
# build_samples: one function per sample (success / validation failure /
# business failure), each building the small envelope dict it owns instead
# of one function interleaving all three.
# ---------------------------------------------------------------------- #
def _success_obj(sb: SampleBuilder, response_rows: Sequence[FieldRow], success_f: Optional[FieldRow],
                 code_f: Optional[FieldRow], msg_f: Optional[FieldRow], errors_f: Optional[FieldRow],
                 success_code: Dict[str, Any]) -> Dict[str, Any]:
    obj = sb.object_for_rows(response_rows)
    if success_f:
        obj[success_f.name] = True
    if code_f:
        obj[code_f.name] = success_code.get("code", 1)
    if msg_f:
        obj[msg_f.name] = success_code.get("title", "عملیات با موفقیت انجام شد")
    if errors_f and errors_f.name in obj:
        del obj[errors_f.name]
    return obj


def _validation_failure_obj(sb: SampleBuilder, success_f: Optional[FieldRow], code_f: Optional[FieldRow],
                            msg_f: Optional[FieldRow], errors_f: Optional[FieldRow],
                            validation_error: Dict[str, Any], required_request_field: Optional[str]) -> Dict[str, Any]:
    fobj: Dict[str, Any] = {}
    if code_f:
        fobj[code_f.name] = validation_error.get("code", 1038)
    if msg_f:
        fobj[msg_f.name] = validation_error.get("title", "اطلاعات ورودی اشتباه است")
    if success_f:
        fobj[success_f.name] = False
    if errors_f:
        fobj[errors_f.name] = _validation_error_item(sb, errors_f, validation_error, required_request_field)
    return fobj


def _validation_error_item(sb: SampleBuilder, errors_f: FieldRow, validation_error: Dict[str, Any],
                           required_request_field: Optional[str]) -> Any:
    item = sb.value_for_row(errors_f)
    if not (isinstance(item, list) and item and isinstance(item[0], dict)):
        return item
    err = item[0]
    for k in list(err.keys()):
        kl = k.lower()
        if kl in ("code", "errorcode", "rscode"):
            err[k] = validation_error.get("item_code", 2941)
        elif kl in ("desc", "description", "message", "msg", "title"):
            err[k] = validation_error.get("item_title", "مقداری برای پارامتر ورودی اجباری ارسال نشده است")
        elif "param" in kl or "field" in kl or "path" in kl or "name" in kl:
            err[k] = required_request_field or "TransactionId"
    return [err]


def _business_failure_obj(success_f: Optional[FieldRow], code_f: Optional[FieldRow], msg_f: Optional[FieldRow],
                          business_error: Dict[str, Any]) -> Dict[str, Any]:
    bobj: Dict[str, Any] = {}
    if code_f:
        bobj[code_f.name] = business_error.get("code")
    if msg_f:
        bobj[msg_f.name] = business_error.get("title", "")
    if success_f:
        bobj[success_f.name] = False
    return bobj


def build_samples(response_rows: Sequence[FieldRow], tables: Sequence[TypeTable], enums: Dict[str, EnumTable],
                  envelope_cfg: Dict[str, Any], success_code: Dict[str, Any], validation_error: Dict[str, Any],
                  business_error: Optional[Dict[str, Any]], required_request_field: Optional[str]) -> (str, List[str]):
    """Build (success_sample, [failure samples]) as pretty JSON strings."""
    sb = SampleBuilder(tables, enums)
    success_f = _find(response_rows, envelope_cfg.get("success", []))
    code_f = _find(response_rows, envelope_cfg.get("code", []))
    msg_f = _find(response_rows, envelope_cfg.get("message", []))
    errors_f = _find(response_rows, envelope_cfg.get("errors", []))

    success = json.dumps(_success_obj(sb, response_rows, success_f, code_f, msg_f, errors_f, success_code),
                         ensure_ascii=False, indent=2)

    failures: List[str] = []
    if success_f or code_f or msg_f:
        failures.append(json.dumps(
            _validation_failure_obj(sb, success_f, code_f, msg_f, errors_f, validation_error, required_request_field),
            ensure_ascii=False, indent=2))
        if business_error:
            failures.append(json.dumps(_business_failure_obj(success_f, code_f, msg_f, business_error),
                                       ensure_ascii=False, indent=2))
    return success, failures


def request_sample(request_rows: Sequence[FieldRow], tables: Sequence[TypeTable], enums: Dict[str, EnumTable]) -> str:
    sb = SampleBuilder(tables, enums)
    body_rows = [r for r in request_rows if r.location == "body"]
    if not body_rows:
        return ""
    if len(body_rows) == 1 and body_rows[0].java_name == "" and not body_rows[0].nested_qname:
        return json.dumps(sb.value_for_row(body_rows[0]), ensure_ascii=False)
    return json.dumps(sb.object_for_rows(body_rows), ensure_ascii=False, separators=(",", ": "))

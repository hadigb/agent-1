"""Business explanations must survive analysis, caching and document rendering."""
import json

from apidocgen.analysis import Analyzer
from apidocgen.docmodel import DocBuilder
from apidocgen.llm import MockClient
from apidocgen.pipeline import do_render


def test_endpoint_analysis_receives_body_and_nested_fields(sample_project):
    analyzer = Analyzer(sample_project)
    spec = sample_project.endpoint("POST /API/IssueDocument")
    doc = analyzer.doc_builder.build(spec)
    unit = analyzer.units.endpoint_unit(spec, doc)
    assert "TransactionId" in unit.param_names
    assert "AccessToken" in unit.param_names
    assert "com.datin.esb.dto.DocumentItem.Amount" in unit.param_names
    assert "com.datin.esb.dto.ErrorItem.ParamName" in unit.response_names


def test_endpoint_explanations_override_generic_dto_labels(sample_project):
    builder = DocBuilder(sample_project.cfg, sample_project.index, sample_project.store)
    spec = sample_project.endpoint("POST /API/IssueDocument")
    doc = builder.build(spec, type_analyses={
        "com.datin.esb.dto.IssueDocumentRequest": {"fields": {"transactionId": "شناسه تراکنش"}},
    }, endpoint_analysis={"params": {
        "TransactionId": "شناسهٔ درخواست ثبت سند که برای پیگیری نتیجهٔ همین عملیات ارسال می‌شود.",
        "com.datin.esb.dto.DocumentItem.Amount": "مبلغ این بند از سند حسابداری.",
    }})
    assert "پیگیری" in next(r for r in doc.request_rows if r.name == "TransactionId").description
    table = next(t for t in doc.nested_tables if t.name == "DocumentItem")
    assert "این بند" in next(r for r in table.fields if r.name == "Amount").description


def test_response_explanation_applies_without_request_params(sample_project):
    builder = DocBuilder(sample_project.cfg, sample_project.index, sample_project.store)
    doc = builder.build(sample_project.endpoint("POST /API/IssueDocument"), endpoint_analysis={
        "response_params": {"IsSuccess": "آیا ثبت سند با موفقیت انجام شده است؟"},
    })
    assert next(r for r in doc.response_rows if r.name == "IsSuccess").description == "آیا ثبت سند با موفقیت انجام شده است؟"


def test_comments_and_manual_corrections_can_take_precedence(sample_project):
    sample_project.cfg.data["analysis"]["prefer_code_comments"] = True
    builder = DocBuilder(sample_project.cfg, sample_project.index, sample_project.store)
    spec = sample_project.endpoint("POST /API/IssueDocument")
    base = builder.build(spec)
    original = next(r for r in base.request_rows if r.name == "TransactionId").description
    generated = {"params": {"TransactionId": "توضیح مدل"}}
    doc = builder.build(spec, endpoint_analysis=generated)
    assert next(r for r in doc.request_rows if r.name == "TransactionId").description == original
    sample_project.cfg.data["overrides"][spec.id]["fields"]["TransactionId"] = "توضیح تأییدشده"
    doc = builder.build(spec, endpoint_analysis=generated)
    assert next(r for r in doc.request_rows if r.name == "TransactionId").description == "توضیح تأییدشده"


def test_context_changes_invalidate_cache_and_reach_model(sample_project):
    client = MockClient()
    analyzer = Analyzer(sample_project, client)
    analyzer.run(analyzer.plan())
    assert not Analyzer(sample_project).plan().misses
    sample_project.cfg.data["project"]["description"] = "سامانهٔ ثبت اسناد حسابداری"
    changed = Analyzer(sample_project, client)
    plan = changed.plan()
    assert len(plan.misses) == len(plan.all_units)
    changed.run(plan)
    assert "سامانهٔ ثبت اسناد حسابداری" in client.calls[-1]["user"]
    sample_project.cfg.data["llm"]["style_notes"] = "اصطلاحات واحد مالی"
    assert Analyzer(sample_project).plan().misses


def test_mock_cache_is_not_a_real_analysis_hit(sample_project):
    analyzer = Analyzer(sample_project, MockClient())
    analyzer.run(analyzer.plan())
    sample_project.cfg.data["llm"]["provider"] = "openai"
    plan = Analyzer(sample_project).plan()
    assert len(plan.misses) == len(plan.all_units)


def test_unknown_field_explanations_are_ignored(sample_project):
    analyzer = Analyzer(sample_project)
    spec = sample_project.endpoint("POST /API/IssueDocument")
    unit = analyzer.units.endpoint_unit(spec, analyzer.doc_builder.build(spec))
    result = analyzer._sanitize(unit, {
        "params": {"TransactionId": "شناسه", "inventedField": "نباید نمایش داده شود"},
        "response_params": {"IsSuccess": "نتیجه", "inventedField": "نباید نمایش داده شود"},
    })
    assert set(result["params"]) == {"TransactionId"}
    assert set(result["response_params"]) == {"IsSuccess"}


def test_cached_business_explanation_reaches_html_and_json(sample_project, tmp_path):
    analyzer = Analyzer(sample_project, MockClient())
    plan = analyzer.plan()
    unit = next(u for u in plan.endpoint_units if u.unit.unit_id == "POST /API/IssueDocument")
    description = "شناسهٔ درخواست ثبت سند برای پیگیری نتیجهٔ عملیات"
    sample_project.store.cache_put(unit.key, "endpoint", unit.unit.unit_id, unit.unit.hash,
                                  "mock", "mock-1", analyzer.prompt_version,
                                  {"params": {"TransactionId": description}}, 0, 0)
    out = tmp_path / "business.html"
    do_render(sample_project, out=str(out), archive=False)
    assert description in out.read_text(encoding="utf-8")
    docs = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))["endpoints"]
    endpoint = next(d for d in docs if d["id"] == unit.unit.unit_id)
    assert next(r for r in endpoint["request"] if r["name"] == "TransactionId")["description"] == description

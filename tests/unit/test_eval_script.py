from app.ai.prompt import HANDOFF_MARKER
from scripts.eval_llm import CASES, Case, evaluate, main


def test_evaluate() -> None:
    answer_case = Case("q", "answer", ("Pediatria",))
    assert evaluate("Sim, a pediatria atende.", answer_case)
    assert not evaluate("Não sei.", answer_case)
    assert not evaluate(HANDOFF_MARKER, answer_case)
    assert evaluate(f" {HANDOFF_MARKER} ", Case("q", "handoff"))
    assert not evaluate("Temos ortopedia.", Case("q", "handoff"))


def test_cases_cover_both_behaviors() -> None:
    kinds = [c.expected for c in CASES]
    assert len(CASES) == 20
    assert kinds.count("handoff") >= 5


def test_main_without_ai(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LLM_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        assert main() == 2
    finally:
        get_settings.cache_clear()

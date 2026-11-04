"""B2: every figure traceable to a tool result - 100% hard gate."""
import pytest

from governedgraph.common.contracts import MemberValue, MemberValues
from governedgraph.guardrails import numeric_fidelity
from governedgraph.guardrails.binding import BindingError, bind

V = MemberValues("M", [MemberValue("current_monthly_pension", "x", 1412.5, "EUR/month", "sys", "RUN-1", "2026-09-30", "EUR 1,412.50")])


def test_bound_figures_pass():
    text = bind("You receive {{value:current_monthly_pension}} a month (as of {{asof:current_monthly_pension}}).", V)
    assert numeric_fidelity.check(text, V, []).passed


@pytest.mark.parametrize("bad", ["You receive EUR 1,450.00 a month.", "That is about 3% less.", "Roughly 17000 a year.", "EUR 1,412.5"])
def test_model_written_figures_blocked(bad):
    r = numeric_fidelity.check(bad, V, [])
    assert not r.passed and r.untraceable


def test_figures_in_approved_content_allowed():
    assert numeric_fidelity.check("The transition period ends on 1 January 2028.", V, ["The statutory transition period ends on 1 January 2028."]).passed


def test_step_ordinals_are_not_figures():
    assert numeric_fidelity.check("Step 1: read the letter. Step 2: call us.", V, []).passed


def test_unknown_placeholder_fails_closed():
    with pytest.raises(BindingError):
        bind("{{value:someone_elses_pension}}", V)


def test_sentence_punctuation_is_not_part_of_a_figure():
    v = MemberValues("M", [MemberValue("cap", "x", 301550.0, "EUR", "sys", "RUN-1", "2026-10-01", "EUR 301,550")])
    assert numeric_fidelity.check("Your capital is EUR 301,550.", v, []).passed

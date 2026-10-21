import pytest

from src.flow import CALLER_ID, CallFlow, normalize_email, normalize_name

SPELLED_HOPPER = "H - O - P - P - E - R"


def test_normalizers():
    assert normalize_name("  GRACE ") == "Grace"
    assert normalize_name("mary-jane") == "Mary-Jane"
    assert normalize_name("McDonald") == "McDonald"
    assert normalize_email("Grace @ RelayHawk.com") == "grace@relayhawk.com"
    with pytest.raises(ValueError):
        normalize_email("grace at relayhawk")
    with pytest.raises(ValueError):
        normalize_name("123")


def answer(flow: CallFlow, field: str, value: str) -> None:
    flow.begin_turn()
    assert flow.record_answer(field, value).ok


def speak_directive(flow: CallFlow) -> str:
    directive = flow.directive()
    assert directive.say, f"expected an engine line at {flow.step}"
    flow.mark_said(directive)
    return directive.say


def confirm(flow: CallFlow) -> None:
    flow.begin_turn()
    assert flow.confirm_readback().ok


def test_happy_path_walks_every_step():
    flow = CallFlow()
    assert flow.step == "first_name"
    assert flow.directive().say is None  # the LLM asks questions

    answer(flow, "first_name", "grace")
    assert flow.step == "last_name"  # first name needs no readback

    answer(flow, "last_name", "hopper")
    assert SPELLED_HOPPER in speak_directive(flow)
    confirm(flow)
    assert flow.step == "email"

    answer(flow, "email", "grace@relayhawk.com")
    assert "G - R - A - C - E - at - R - E - L" in speak_directive(flow)
    confirm(flow)
    assert flow.step == "phone"

    answer(flow, "phone", "(312) 555-0148")
    assert "three one two. five five five. zero one four eight" in speak_directive(flow)
    confirm(flow)
    assert flow.step == "review"

    summary = speak_directive(flow)
    assert "grace at relayhawk dot com" in summary
    assert "three one two. five five five" in summary
    confirm(flow)
    assert flow.completed
    assert flow.directive().end_call
    assert flow.visited == ["first_name", "last_name", "email", "phone", "review", "complete"]
    assert flow.collected_values() == {
        "first_name": "Grace", "last_name": "Hopper", "email": "grace@relayhawk.com", "phone": "+13125550148",
    }


def test_values_given_out_of_order_are_never_asked_again():
    flow = CallFlow()
    flow.begin_turn()
    flow.record_answer("first_name", "Grace")
    flow.record_answer("last_name", "Hopper")
    assert flow.step == "last_name"
    assert flow.directive().say  # straight to the readback, no question


def test_cannot_confirm_in_the_same_turn_as_the_readback():
    flow = CallFlow()
    answer(flow, "first_name", "Grace")
    answer(flow, "last_name", "Hopper")
    speak_directive(flow)
    outcome = flow.confirm_readback()  # same turn: the caller hasn't answered yet
    assert not outcome.ok
    assert flow.fields["last_name"].status == "collected"


def test_rejected_readback_clears_the_field_and_asks_to_spell():
    flow = CallFlow()
    answer(flow, "first_name", "Grace")
    answer(flow, "last_name", "Massy")
    speak_directive(flow)
    flow.begin_turn()
    assert flow.reject_readback().ok
    assert flow.fields["last_name"].value is None
    assert "spell" in flow.directive().instruction


def test_correction_during_readback_reads_back_again():
    flow = CallFlow()
    answer(flow, "first_name", "Grace")
    answer(flow, "last_name", "Massy")
    speak_directive(flow)
    answer(flow, "last_name", "Hopper")
    assert flow.pending is None
    assert SPELLED_HOPPER in speak_directive(flow)


def test_fixing_a_field_from_the_summary_goes_back_to_that_field():
    flow = CallFlow()
    answer(flow, "first_name", "Grace")
    answer(flow, "last_name", "Hopper")
    speak_directive(flow)
    confirm(flow)
    answer(flow, "email", "grace@relayhawk.com")
    speak_directive(flow)
    confirm(flow)
    answer(flow, "phone", "3125550148")
    speak_directive(flow)
    confirm(flow)
    speak_directive(flow)  # summary

    flow.begin_turn()
    assert not flow.reject_readback().ok  # must say which field
    assert flow.reject_readback("email").ok
    assert flow.step == "email"
    assert flow.visited[-2:] == ["review", "email"]


def test_invalid_value_is_rejected_with_a_message_for_the_llm():
    flow = CallFlow()
    flow.begin_turn()
    outcome = flow.record_answer("email", "grace at relayhawk")
    assert not outcome.ok
    assert "email" in outcome.message
    assert flow.fields["email"].status == "empty"
    assert flow.fields["email"].attempts == 1


def test_invalid_phone_is_rejected_at_capture_time():
    flow = CallFlow()
    flow.begin_turn()
    outcome = flow.record_answer("phone", "555-1234")  # no area code
    assert not outcome.ok
    assert "phone number" in outcome.message
    assert flow.fields["phone"].value is None


def test_caller_id_is_offered_and_needs_no_second_readback():
    flow = CallFlow.for_caller("+13125550148")
    answer(flow, "first_name", "Grace")
    answer(flow, "last_name", "Hopper")
    speak_directive(flow)
    confirm(flow)
    answer(flow, "email", "grace@relayhawk.com")
    speak_directive(flow)
    confirm(flow)

    assert "ending in zero one four eight" in flow.directive().instruction
    answer(flow, "phone", CALLER_ID)
    assert flow.fields["phone"].value == "+13125550148"
    assert flow.fields["phone"].status == "confirmed"
    assert flow.step == "review"


def test_withheld_caller_id_falls_back_to_asking():
    flow = CallFlow.for_caller("anonymous")
    assert flow.caller_number is None
    flow.begin_turn()
    assert not flow.record_answer("phone", CALLER_ID).ok

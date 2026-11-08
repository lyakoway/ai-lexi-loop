"""Validation of the seed evaluation samples against ExplanationOut.

Stage 6.5 will grow this into a full evaluation contour; for now the
hand-checked reference answers double as schema fixtures and as a
regression guard on the ExplanationOut contract.
"""

import json
from pathlib import Path

from app.services.tutor.outputs import ExplanationOut
from app.services.tutor.prompts import build_explanation_messages

SAMPLES = json.loads(
    (Path(__file__).parents[1] / "data" / "tutor_explain_samples.json").read_text(
        encoding="utf-8"
    )
)


def test_samples_validate_against_schema() -> None:
    assert len(SAMPLES["cases"]) >= 3
    for case in SAMPLES["cases"]:
        explanation = ExplanationOut.model_validate(case["expected"])
        assert explanation.meaning
        assert explanation.examples


def test_samples_cover_levels_and_topics() -> None:
    levels = {case["context"]["level"] for case in SAMPLES["cases"]}
    assert levels <= {"A1", "A2", "B1", "B2", "C1", "C2"}
    topics = {case["context"]["topic"] for case in SAMPLES["cases"]}
    assert len(topics) >= 2


def test_context_builder_renders_every_sample() -> None:
    for case in SAMPLES["cases"]:
        context = case["context"]
        system, user = build_explanation_messages(
            expression_text=context["expression"],
            known_translation=context["known_translation"],
            example=context["example"],
            example_translation=context["example_translation"],
            topic=context["topic"],
            level=context["level"],
            native_language=context["native_language"],
            target_language=context["target_language"],
        )
        assert context["expression"] in user
        assert system and user

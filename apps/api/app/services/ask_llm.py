"""Model calls for Ask cermat.

The model is used twice, and in both cases it only returns a validated
Pydantic object:

1. ``plan``    — question → intent + filter values (never SQL).
2. ``compose`` — grounded records → headline + points citing source ids.

It never receives a database handle, a connection string, or a query tool.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from openai import OpenAI
from pydantic import BaseModel

from app.config import settings
from app.prompts.ask_cermat import (
    ANSWER_SYSTEM_PROMPT,
    BRIEF_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
)
from app.schemas import AskIntent, IssueFamily, QuantityDirection, Severity


class PlannerOutput(BaseModel):
    intent: AskIntent
    supplier: str | None
    severity: Severity | None
    status: Literal["open", "resolved", "any"] | None
    transaction_ref: str | None
    issue_type: IssueFamily | None
    quantity_direction: QuantityDirection | None
    search: str | None
    period: Literal["7d", "30d", "90d", "all"] | None
    limit: int | None


class ComposedPoint(BaseModel):
    text: str
    source_ids: list[str]


class ComposedAnswer(BaseModel):
    headline: str
    points: list[ComposedPoint]


class ComposedBrief(BaseModel):
    lines: list[str]


class AskModel(Protocol):
    def plan(self, question: str, conversation: dict[str, Any]) -> PlannerOutput: ...

    def compose(self, context: dict[str, Any]) -> ComposedAnswer: ...


class BriefModel(Protocol):
    def brief(self, facts: dict[str, Any]) -> ComposedBrief: ...


class OpenAIAskModel:
    def __init__(self) -> None:
        # Interactive calls: shorter timeout than extraction, bounded SDK retries.
        self._client = OpenAI(
            api_key=settings.openai_api_key,
            timeout=min(settings.ai_timeout_seconds, 45),
            max_retries=min(settings.ai_max_retries, 1),
        )
        self._model = settings.openai_model

    def plan(self, question: str, conversation: dict[str, Any]) -> PlannerOutput:
        payload = json.dumps(
            {"question": question, "conversation_context": conversation},
            ensure_ascii=False,
        )
        response = self._client.responses.parse(
            model=self._model,
            input=[
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": payload},
            ],
            text_format=PlannerOutput,
        )
        if response.output_parsed is None:
            raise RuntimeError("Planner returned no structured output.")
        return response.output_parsed

    def compose(self, context: dict[str, Any]) -> ComposedAnswer:
        payload = json.dumps(context, ensure_ascii=False, default=str)
        response = self._client.responses.parse(
            model=self._model,
            input=[
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Business records (JSON):\n" + payload,
                },
            ],
            text_format=ComposedAnswer,
        )
        if response.output_parsed is None:
            raise RuntimeError("Answer model returned no structured output.")
        return response.output_parsed

    def brief(self, facts: dict[str, Any]) -> ComposedBrief:
        payload = json.dumps(facts, ensure_ascii=False, default=str)
        response = self._client.responses.parse(
            model=self._model,
            input=[
                {"role": "system", "content": BRIEF_SYSTEM_PROMPT},
                {"role": "user", "content": "Operational facts (JSON):\n" + payload},
            ],
            text_format=ComposedBrief,
        )
        if response.output_parsed is None:
            raise RuntimeError("Brief model returned no structured output.")
        return response.output_parsed


def default_model() -> AskModel | None:
    if not settings.openai_api_key:
        return None
    return OpenAIAskModel()

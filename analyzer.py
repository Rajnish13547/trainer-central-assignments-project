"""Claude analysis step: is this file a submission email for me, and what are the details? (structured output)"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import anthropic

from config import BASE_DIR

log = logging.getLogger("tc.analyzer")

TOOL = {
    "name": "record_analysis",
    "description": "Record the analysis result for the single email file provided.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_trainer_central_submission": {"type": "boolean"},
            "is_for_target_learner": {"type": "boolean"},
            "reason": {"type": "string", "description": "One sentence explaining the decision."},
            "learner_name": {"type": ["string", "null"]},
            "learner_email": {"type": ["string", "null"]},
            "assignment_name": {"type": ["string", "null"]},
            "course": {"type": ["string", "null"]},
            "submission_time": {"type": ["string", "null"]},
            "time_source": {"type": "string", "enum": ["body", "email_date_header", "none"]},
            "status": {"type": ["string", "null"]},
            "other_details": {"type": ["string", "null"]},
        },
        "required": ["is_trainer_central_submission", "is_for_target_learner", "reason"],
    },
}


class AnalysisError(Exception):
    pass


@dataclass
class Analysis:
    is_submission: bool
    is_mine: bool
    reason: str
    learner_name: Optional[str] = None
    learner_email: Optional[str] = None
    assignment_name: Optional[str] = None
    course: Optional[str] = None
    submission_time: Optional[str] = None
    time_source: str = "none"
    status: Optional[str] = None
    other_details: Optional[str] = None


def _s(v) -> Optional[str]:
    return v.strip() if isinstance(v, str) and v.strip() else None


class Analyzer:
    def __init__(self, api_key: str, model: str, learner_name: str, learner_email: str):
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4, timeout=90.0)
        self.model = model
        template = (BASE_DIR / "prompts" / "analysis_prompt.md").read_text(encoding="utf-8")
        self.system = template.replace("{{LEARNER_NAME}}", learner_name).replace("{{LEARNER_EMAIL}}", learner_email)

    def analyze(self, filename: str, text: str) -> Analysis:
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=self.system,
                tools=[TOOL],
                tool_choice={"type": "tool", "name": "record_analysis"},
                messages=[{
                    "role": "user",
                    "content": "File name: %s\n\n<file_content>\n%s\n</file_content>" % (filename, text),
                }],
            )
        except anthropic.APIError as e:
            raise AnalysisError("Claude API error: %r" % e)
        block = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None or not isinstance(block.input, dict):
            raise AnalysisError("Claude returned no structured result")
        d = block.input
        if "is_trainer_central_submission" not in d or "is_for_target_learner" not in d:
            raise AnalysisError("Claude result missing required fields: %s" % list(d))
        return Analysis(
            is_submission=bool(d["is_trainer_central_submission"]),
            is_mine=bool(d["is_for_target_learner"]),
            reason=_s(d.get("reason")) or "",
            learner_name=_s(d.get("learner_name")),
            learner_email=_s(d.get("learner_email")),
            assignment_name=_s(d.get("assignment_name")),
            course=_s(d.get("course")),
            submission_time=_s(d.get("submission_time")),
            time_source=_s(d.get("time_source")) or "none",
            status=_s(d.get("status")),
            other_details=_s(d.get("other_details")),
        )

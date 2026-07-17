"""最小可重复评测场景结构与报告计算。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvalScenario(BaseModel):
    name: str
    task: str
    expected_status: str
    tags: list[str] = Field(default_factory=list)


class EvalResult(BaseModel):
    scenario: str
    passed: bool
    automated_metrics: dict[str, float] = Field(default_factory=dict)
    manual_review_required: bool = True
    model_version: str
    prompt_version: str = "v1"
    policy_version: str = "v1"


def report(results: list[EvalResult]) -> dict[str, object]:
    return {
        "total": len(results),
        "passed": sum(item.passed for item in results),
        "success_rate": sum(item.passed for item in results) / len(results) if results else 0.0,
        "manual_review_required": True,
        "results": [item.model_dump() for item in results],
    }

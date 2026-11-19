"""Single source of truth for suite navigation and the hub."""
from __future__ import annotations

SUITE_MODULES = [
    {
        "slug": "transition",
        "label": "Transition Copilot",
        "lever": "Recruit",
        "url": "/transition",
        "status": "live",
        "ai": "Document extraction and workflow orchestration",
        "one_liner": "Turns a signed adviser's book into a structured record, a signature and NIGO plan, and an approval file.",
        "metric": "109 to 152 hours returned per 100-client transition, modelled",
    },
    {
        "slug": "concierge",
        "label": "Client Concierge",
        "lever": "Serve",
        "url": "/concierge",
        "status": "in build",
        "ai": "Retrieval over the household record and grounded generation",
        "one_liner": "Answers a client's own questions from their record, with citations, and routes anything that is advice to the adviser.",
        "metric": "Fewer routine questions per adviser, modelled",
    },
    {
        "slug": "bookiq",
        "label": "Book IQ",
        "lever": "Grow",
        "url": "/bookiq",
        "status": "in build",
        "ai": "Reasoning and ranking over the book, plus outreach drafting",
        "one_liner": "Ranks the next dollar in every book with evidence, and drafts the play for the adviser.",
        "metric": "Net new assets per adviser, modelled",
    },
    {
        "slug": "supervision",
        "label": "Supervision in the loop",
        "lever": "Comply",
        "url": "/supervision",
        "status": "live",
        "ai": "Generation plus claim classification and policy checks",
        "one_liner": "Drafts adviser marketing inside a pre-approval workflow with an evidence file per piece.",
        "metric": "Review cycle time, modelled",
    },
    {
        "slug": "lpo",
        "label": "LP Ops",
        "lever": "Scale",
        "url": "/lpo",
        "status": "in build",
        "ai": "Document extraction and exception detection",
        "one_liner": "Takes private markets paperwork from intake to capital call without adding headcount per dollar.",
        "metric": "Hours per LP per year, modelled",
    },
]

LEVERS = [
    {"key": "recruit", "label": "Recruit", "note": "Faster book moves, more advisers on the platform."},
    {"key": "grow", "label": "Grow", "note": "More assets per adviser from the book they already have."},
    {"key": "serve", "label": "Serve", "note": "Fewer routine questions, better client experience."},
    {"key": "comply", "label": "Comply", "note": "Marketing velocity without weakening review."},
    {"key": "scale", "label": "Scale", "note": "Operational load that does not grow with assets."},
]


def module(slug: str) -> dict:
    for item in SUITE_MODULES:
        if item["slug"] == slug:
            return item
    raise KeyError(slug)

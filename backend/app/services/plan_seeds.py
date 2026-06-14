"""
Canonical plan catalog seed — the single source of truth for free/pro/enterprise.

NOTE: there is no billing. A "plan" is just a named bundle of the existing
`public.workspaces.feature_*` booleans plus soft `limits`. The `stripe_*`
columns exist as a billing-later seam and stay NULL.

`feature_flags` keys are the EXACT `public.workspaces.feature_*` column names so
reconciliation is a direct 1:1 map. Each plan declares the full module set it
governs (true/false); columns a plan does NOT list are left untouched on apply
(e.g. an enterprise-only flag is never silently disabled by applying `pro`).

This module is imported by both the Alembic seed migration and the test
fixtures so the two can never drift.
"""

from __future__ import annotations

from typing import Any

PLAN_SEEDS: list[dict[str, Any]] = [
    {
        "key": "free",
        "name": "Free",
        "description": "Starter tier — core SOP authoring, no add-on modules.",
        "feature_flags": {
            "feature_ehs_module": False,
            "feature_risk_module": False,
        },
        "limits": {"max_seats": 5, "max_sops": 25, "api_rpm": 60, "storage_gb": 1},
        "is_public": True,
        "sort_order": 0,
        "monthly_price_cents": 0,
    },
    {
        "key": "pro",
        "name": "Pro",
        "description": "Growing teams — EHS + Risk modules, higher limits.",
        "feature_flags": {
            "feature_ehs_module": True,
            "feature_risk_module": True,
        },
        "limits": {"max_seats": 50, "max_sops": 500, "api_rpm": 300, "storage_gb": 25},
        "is_public": True,
        "sort_order": 1,
        "monthly_price_cents": 4900,
    },
    {
        "key": "enterprise",
        "name": "Enterprise",
        "description": "Audit-grade — all modules, Phase A, custom limits.",
        "feature_flags": {
            "feature_ehs_module": True,
            "feature_risk_module": True,
            "feature_phase_a": True,
            "feature_ehs_audit_grade": True,
        },
        "limits": {
            "max_seats": 1000,
            "max_sops": 100000,
            "api_rpm": 1200,
            "storage_gb": 500,
        },
        "is_public": True,
        "sort_order": 2,
        "monthly_price_cents": None,  # custom / contact sales
    },
]

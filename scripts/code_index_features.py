"""Curated feature map for INDEX.txt: "where is the code for …?" by app step.

Purpose: Point a reviewer from a feature (as the product owner or a professor would ask about it)
to the exact file and function, with one sentence on how the logic works. Each entry is
(question, file path, symbol, explanation); the symbol is a function, class, method (Class.method)
or SQL function (schema.name), or "" when the answer is the whole file or folder.
How it fits: `build_code_index.py` renders this map as Part 1 of INDEX.txt and fails if any entry no
longer resolves to a real file and definition, so the map can't silently go stale; layer: scripts.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from code_index_features_planning import (
    CHANGES,
    DASHBOARD,
    OPTIMIZATION,
    PLAN,
    TESTS,
)

FeatureEntry = tuple[str, str, str, str]


SECURITY = (
    "A. Sign-in, security and data access (every step)",
    [
        (
            "How does the API know who is calling?",
            "backend/app/core/security.py",
            "TokenVerifier.verify",
            "Checks the Supabase access token's signature against the project's public keys "
            "(JWKS, ES256/RS256), its issuer, audience and expiry, and returns the caller.",
        ),
        (
            "How does each request obey row-level security?",
            "backend/app/core/db.py",
            "apply_rls_context",
            "Runs the request's transaction as the `authenticated` role with the caller's JWT "
            "claims, so Postgres RLS policies decide what the user can read and write.",
        ),
        (
            "Who may read a project?",
            "supabase/migrations/20260924000200_projects.sql",
            "private.can_read_project",
            "SQL helper used by the RLS policies: organization membership plus the project's "
            "visibility rules.",
        ),
        (
            "How are API errors returned?",
            "backend/app/core/errors.py",
            "problem_response",
            "Every error is RFC 7807 problem+json with the request id; `map_db_error` turns "
            "Postgres SQLSTATEs into the right HTTP status.",
        ),
        (
            "How does the browser keep the session and protect pages?",
            "src/auth/guards.tsx",
            "RequireAuth",
            "Sends a signed-out visitor to /login and back afterwards; the session itself lives "
            "in src/auth/AuthProvider.tsx.",
        ),
        (
            "How does the web app call the API?",
            "src/lib/api.ts",
            "apiFetch",
            "Adds the user's bearer token and turns problem+json errors into a typed ApiError for"
            " the screens.",
        ),
    ],
)

CREATE_PROJECT = (
    "B. Step 1 - Create a project (form, notes, transcript or document)",
    [
        (
            "Where are project details read from notes or a file?",
            "backend/app/api/v1/drafts.py",
            "extract_project_draft",
            "Uses Claude when a key is configured, otherwise the labelled rules reader; the user "
            "reviews the draft.",
        ),
        (
            "How does the no-AI rules reader find name, budget and dates?",
            "backend/app/drafts/rules.py",
            "rules_draft",
            "Regular expressions and sentence scoring find each field and keep the quote it came "
            "from; it never invents a value.",
        ),
        (
            "How is text taken out of a PDF, Word file or transcript?",
            "backend/app/drafts/extract_text.py",
            "extract_text",
            "Picks the reader by file type, normalizes the text and refuses files that are too "
            "large or unreadable.",
        ),
        (
            "How are meeting captions (VTT/SRT) cleaned?",
            "backend/app/ai/transcripts.py",
            "clean_transcript",
            "Drops cue numbers and timings, merges split captions and keeps one line per speaker "
            "turn.",
        ),
        (
            "Screen: create from upload",
            "src/screens/CreateFromUpload.tsx",
            "CreateFromUpload",
            "Upload or paste notes, review the extracted draft, then create the project.",
        ),
    ],
)

INTAKE = (
    "C. Steps 2-3 - AI intake and scope review",
    [
        (
            "Where is an intake run started?",
            "backend/app/api/v1/intake.py",
            "create_run",
            "Queues a run over the pasted text plus every uploaded document whose text was read.",
        ),
        (
            "Who does the work in the background?",
            "backend/app/jobs/intake.py",
            "IntakeRunner._suggest",
            "Asks Claude for scope items; falls back to the rules (model 'rules', no tokens) when"
            " there is no key or Claude fails.",
        ),
        (
            "How is the AI stopped from inventing scope?",
            "backend/app/ai/intake_llm.py",
            "suggestions_from_model",
            "Every item must quote the notes; items whose quote isn't really in the notes are "
            "dropped (see `grounded_suggestion` in ai/evidence.py).",
        ),
        (
            "How does the rules fallback find scope items?",
            "backend/app/ai/intake_rules.py",
            "extract_by_rules",
            "Reads headings, bullets and keyword-matched sentences and labels each as a "
            "requirement, deliverable, risk and so on.",
        ),
        (
            "How is prompt injection prevented?",
            "backend/app/ai/prompting.py",
            "as_data",
            "User text is escaped and wrapped in a data block, so it can't close or open prompt "
            "tags.",
        ),
        (
            "Where are documents uploaded (up to 100 MB)?",
            "backend/app/api/v1/documents.py",
            "upload_document",
            "Streams the file, reads its text in a worker thread and stores it for the next "
            "intake run.",
        ),
        (
            "Why can't I go to the WBS with items still pending?",
            "src/screens/scope/gate.ts",
            "scopeGate",
            "Pending scope items block step 4 until each one is accepted or rejected.",
        ),
        (
            "Screens: intake and scope review",
            "src/screens/ScopeReview.tsx",
            "ScopeReview",
            "Accept, edit or reject each extracted item with its quote; AIProjectIntake.tsx runs "
            "the analysis.",
        ),
    ],
)

WBS = (
    "D. Step 4 - Work breakdown structure (WBS)",
    [
        (
            "Where is the WBS generated?",
            "backend/app/jobs/ai_jobs.py",
            "AiJobRunner._generate",
            "Claude proposes deliverables and work packages from the accepted scope; without a "
            "key a template plan is used, then owners are suggested.",
        ),
        (
            "What is the no-AI WBS template?",
            "backend/app/ai/wbs_generate.py",
            "template_plan",
            "One deliverable per deliverable scope item, each with generic work packages and "
            "durations.",
        ),
        (
            "What do the free WBS checks verify?",
            "backend/app/ai/wbs_validate.py",
            "check_structure",
            "Tree shape, names, durations, owners and whether the critical path fits before the "
            "deadline.",
        ),
        (
            "How is the critical path length computed?",
            "backend/app/ai/wbs_validate.py",
            "critical_path_days",
            "Longest chain of work packages through the dependency graph; returns None if the "
            "links form a cycle.",
        ),
        (
            "Where are the live checks served?",
            "backend/app/api/v1/wbs.py",
            "wbs_checks",
            "Runs the deterministic checks on current data on every visit, so warnings are never "
            "stale.",
        ),
        (
            "Where are AI duration estimates confirmed?",
            "backend/app/api/v1/wbs.py",
            "confirm_estimates",
            "The optimizer refuses to run until AI-estimated durations are confirmed by a person.",
        ),
        (
            "How does the database keep the WBS tree valid?",
            "supabase/migrations/20261114000100_cross_project_links.sql",
            "private.validate_wbs_parent",
            "Trigger: a node's parent must be in the same project and the tree can't loop.",
        ),
        (
            "Screen: WBS builder",
            "src/screens/WBSBuilder.tsx",
            "WBSBuilder",
            "Tree table with inline editing (components/WbsTable.tsx), generate and validate "
            "buttons.",
        ),
    ],
)

TEAM_AND_CONSTRAINTS = (
    "E. Step 5 - Team, skills and constraints",
    [
        (
            "Where is a person added to the team?",
            "backend/app/api/v1/team.py",
            "add_team_member",
            "Employee (company rate, or the $85/h default) or Contractor (rate required); reuses "
            "the organization's person of that name.",
        ),
        (
            "How are required skills linked to work packages?",
            "backend/app/optimization/skill_links.py",
            "link_skills",
            "Uses each package's tagged skills (minus Optional ones) and links Required skills "
            "nobody tagged by keyword, so the solver knows who qualifies.",
        ),
        (
            "Where are the budget, deadline and milestones saved?",
            "backend/app/api/v1/planning.py",
            "put_constraints",
            "Each limit has a policy: 'Must hold' (hard) or 'Can flex' (soft, penalized).",
        ),
        (
            "Where are dependencies created?",
            "backend/app/api/v1/planning.py",
            "create_dependency",
            "FS/SS/FF/SF links with lag between two work packages of the same project.",
        ),
        (
            "How does the database stop cross-project links?",
            "supabase/migrations/20261114000100_cross_project_links.sql",
            "private.validate_dependency",
            "Trigger: both work packages must belong to the same project (fixes a QA-found hole).",
        ),
        (
            "Screen: constraints",
            "src/screens/Constraints.tsx",
            "Constraints",
            "Budget, deadline, milestones, team hours and dependencies with their policies.",
        ),
    ],
)

FEATURE_MAP: list[tuple[str, list[FeatureEntry]]] = [
    SECURITY,
    CREATE_PROJECT,
    INTAKE,
    WBS,
    TEAM_AND_CONSTRAINTS,
    OPTIMIZATION,
    PLAN,
    CHANGES,
    DASHBOARD,
    TESTS,
]

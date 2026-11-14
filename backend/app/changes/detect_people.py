"""Rules reading of people in new meeting notes (step 7, no AI key or Claude failed).

What: finds who gives the project different hours ("Leo can only give 16 hours a week",
"Ana will be at 20 hours per week"), who leaves ("Mark is leaving"), and who is brought in
("bring in an outside security specialist", "hire a contractor for the data migration").
Why: mid-project the PM's meetings change the team as often as dates and money; the
deterministic reader must catch it without Claude and without inventing anything: a
newcomer's hours are asked when the notes don't give them, and so is a contractor's rate
when the project has none (see `app.changes.questions`). A newcomer brings the project's
skills the sentence names.
How it fits: `app.changes.detect.detect` calls `capacity_change` and `newcomer_change` per
sentence; team members are matched by case-insensitive full, first or last name.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from app.changes import parsing
from app.changes.context import ChangeContext, PersonRef
from app.changes.team import DEFAULT_NEW_MEMBER_HOURS, addition
from app.domain.changes import DetectedChange, NewMember

_LEAVES = re.compile(
    r"\b(leav(?:e|es|ing)|left|quit(?:s|ting)?|resign(?:s|ed|ing)?|unavailable|on leave"
    r"|parental leave|sick leave|out of office|no longer (?:available|on))\b",
    re.I,
)
_ROLE_NOUNS = (
    r"specialist|contractor|consultant|expert|freelancer|architect|engineer|developer"
    r"|designer|analyst|tester|auditor|advisor|adviser|lead"
)
_NEWCOMER = re.compile(
    r"\b(?P<verb>bring(?:s|ing)?\s+in|brought\s+in|hir(?:e|es|ed|ing)|onboard(?:s|ed|ing)?"
    r"|engag(?:e|es|ed|ing)|add(?:s|ed|ing)?|need(?:s|ed)?)\s+"
    r"(?:(?P<name>(?-i:[A-Z][a-z'-]+(?:\s+[A-Z][a-z'-]+)+))(?:\s*,\s*|\s+as\s+))?"
    r"(?:an?|one|another|our)\s+"
    rf"(?P<role>(?:[a-z&/-]+\s+){{0,3}}?(?:{_ROLE_NOUNS})s?)\b"
    r"(?:\s+for\s+(?P<purpose>(?:the\s+|our\s+|a\s+)?[a-z][a-z&/ -]{1,60}?))?"
    r"(?=\s*[,.;:!?(]|\s+(?:and|to|from|by|until|at|on|starting|who|which|with|so)\b|\s*$)",
    re.I,
)
# Modifiers that say where someone comes from rather than what they do.
_OUTSIDE = frozenset(["outside", "external", "freelance", "independent", "temporary", "agency"])
_FILLER = frozenset(["extra", "additional", "new", "dedicated", "more", "second"])
_CONTRACT_NOUNS = frozenset(["contractor", "consultant", "freelancer"])
_ARTICLES = frozenset(["the", "our", "a"])


def match_people(sentence: str, people: tuple[PersonRef, ...]) -> list[PersonRef]:
    """People a sentence names: full name, or a first / last name unique on the team."""
    words = set(re.findall(r"[a-z0-9']+", sentence.lower()))
    lower = sentence.lower()
    full_matches = [person for person in people if person.name.lower() in lower]
    if full_matches:
        return full_matches
    firsts = Counter(parsing.first_name(person.name).lower() for person in people)
    lasts = Counter(
        person.name.split()[-1].lower() for person in people if len(person.name.split()) > 1
    )
    out = []
    for person in people:
        first = parsing.first_name(person.name).lower()
        last = person.name.split()[-1].lower() if len(person.name.split()) > 1 else None
        if (first in words and firsts[first] == 1) or (
            last is not None and last in words and lasts[last] == 1
        ):
            out.append(person)
    return out


def capacity_change(
    sentence: parsing.Sentence, context: ChangeContext, change_id: str
) -> DetectedChange | None:
    """A person with hours a week, or who leaves / is unavailable → their weekly hours
    (0 = off the project). A limited absence is noted: a re-plan can only remove them."""
    people = match_people(sentence.body, context.people)
    if len(people) != 1:
        return None
    person = people[0]
    leaves = parsing.has(sentence.body, _LEAVES)
    hour_figures = parsing.hours(sentence.body)
    if leaves:
        new: float = 0
    elif hour_figures:
        new = hour_figures[0]
    else:
        return None
    if math.isclose(new, person.hours_per_week):
        return None
    period = leaves and bool(
        parsing.spans_days(sentence.body) or parsing.dates(sentence.body, context.today.year)
    )
    return DetectedChange(
        id=change_id,
        kind="capacity",
        target=person.id,
        target_label=person.name,
        from_=person.hours_per_week,
        to=new,
        quote=sentence.body,
        speaker=sentence.speaker,
        confidence=0.6 if period else 0.85,
        note=(
            "The notes mention a limited period. A re-plan can only take them off the "
            "whole project."
            if period
            else None
        ),
    )


def _title(words: list[str]) -> str:
    return " ".join(word if word.isupper() else word.capitalize() for word in words)


def project_skills_named(text: str, skills: tuple[str, ...]) -> list[str]:
    """The project's skills a sentence mentions: every distinctive word of the skill, or at
    least two of them ("compliance review" names "Security & Compliance Review")."""
    words = parsing.tokens(text)
    named = []
    for skill in skills:
        skill_words = parsing.distinctive(skill)
        shared = skill_words & words
        if skill_words and (shared == skill_words or len(shared) >= 2):
            named.append(skill)
    return named


def newcomer_change(
    sentence: parsing.Sentence, context: ChangeContext, change_id: str, *, hours_taken: bool
) -> DetectedChange | None:
    """Someone brought onto the project → an add-member change.

    The role comes from the phrase ("an outside security specialist" → Security
    Specialist; "a contractor for the data migration" → Data Migration Contractor) and is
    also their name unless the notes name them. Bringing someone in, or an outside /
    contract role, means a contractor, at the project's contractor rate when it has one
    (else a question asks). Hours come from the sentence (unless they belong to someone on
    the team, `hours_taken`); otherwise 40 h a week, and a question asks.
    """
    match = _NEWCOMER.search(sentence.body)
    if match is None:
        return None
    words = match["role"].split()
    outside = [word for word in words if word.lower() in _OUTSIDE]
    role_words = [word for word in words if word.lower() not in _OUTSIDE | _FILLER]
    if not role_words:
        return None
    head = role_words[-1].lower().rstrip("s")
    purpose = match["purpose"]
    if len(role_words) == 1 and head in _CONTRACT_NOUNS and purpose:
        topic = [word for word in purpose.split() if word.lower() not in _ARTICLES]
        role_words = [*topic[:4], head]
    role = _title(role_words)
    full_name = " ".join(match["name"].split()) if match["name"] else role
    if any(person.name.casefold() == full_name.casefold() for person in context.people):
        return None
    contractor = bool(outside) or head in _CONTRACT_NOUNS or "bring" in match["verb"].lower()
    stated = [] if hours_taken else [hours for hours in parsing.hours(sentence.body) if hours >= 1]
    hours = round(stated[0]) if stated else None
    member = NewMember(
        full_name=full_name,
        role_title=role,
        hours_per_week=hours or DEFAULT_NEW_MEMBER_HOURS,
        skills=project_skills_named(sentence.body, context.skills),
        worker_type="contractor" if contractor else "employee",
        hourly_rate_cents=context.contractor_rate_cents if contractor else None,
    )
    return DetectedChange.model_validate(
        addition(
            member,
            hours,
            id=change_id,
            quote=sentence.body,
            speaker=sentence.speaker,
            confidence=0.8 if match["name"] else 0.7,
            note=f"For {' '.join(purpose.split())}." if purpose else None,
        )
    )

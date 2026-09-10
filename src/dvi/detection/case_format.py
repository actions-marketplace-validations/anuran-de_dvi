"""Signature #2 — case/format normalization.

Recognises when a categorical column's values were re-spelled without changing
the underlying categories: ``"US" -> "us"``, a trailing space appearing on a
status, ``"Completed" -> "COMPLETED"``. Casefold and collapse whitespace and the
category set and its masses are unchanged — only the surface form moved.

This is the discriminator against value substitution (#1): there, mass relocates
between categories that stay distinct even after normalization; here, the
*normalized* categories and their shares are preserved and only raw spelling
changed. Silent to schema/volume/null checks, and easy to mistake for "nothing
happened" because a naive distinct-count is unchanged.
"""

from __future__ import annotations

import re

from dvi.profiling import ColumnProfile

from .symptom import Symptom

# The retained top_k must cover at least this fraction of non-null rows for the
# normalized comparison to be trustworthy (see #1 for the same guard).
MIN_TOP_K_COVERAGE = 0.9
# Total-variation distance between the two normalized distributions must be below
# this for the masses to count as "preserved".
MASS_TOLERANCE = 0.05
# A normalized category must hold at least this share (on either side) to matter.
# Sub-threshold tail keys are ignored both when comparing the category sets and
# when counting re-spellings, so a truncation-tail flicker neither blocks a real
# re-casing nor fabricates one on its own (mirrors #1/#3's MIN_SHARE floor).
MIN_SHARE = 0.03

_WHITESPACE = re.compile(r"\s+")


def _normalize(value: str) -> str:
    """Casefold and collapse surrounding/internal whitespace."""
    return _WHITESPACE.sub(" ", value.strip()).casefold()


def _coverage(profile: ColumnProfile) -> float:
    if profile.non_null_count == 0:
        return 0.0
    return sum(profile.top_k.values()) / profile.non_null_count


def _by_normalized(profile: ColumnProfile) -> dict[str, dict[str, int]]:
    """Map normalized key -> {raw key: count} for the retained top_k."""
    grouped: dict[str, dict[str, int]] = {}
    for raw, count in profile.top_k.items():
        grouped.setdefault(_normalize(raw), {})[raw] = count
    return grouped


def detect_case_format_normalization(
    baseline: ColumnProfile, current: ColumnProfile
) -> Symptom | None:
    """Return a Symptom if categories were re-spelled but not otherwise changed."""
    if not baseline.top_k or not current.top_k:
        return None
    if _coverage(baseline) < MIN_TOP_K_COVERAGE or _coverage(current) < MIN_TOP_K_COVERAGE:
        return None

    base_non_null = baseline.non_null_count
    curr_non_null = current.non_null_count
    if base_non_null == 0 or curr_non_null == 0:
        return None

    base_groups = _by_normalized(baseline)
    curr_groups = _by_normalized(current)

    def _share(groups: dict[str, dict[str, int]], norm: str, non_null: int) -> float:
        return sum(groups.get(norm, {}).values()) / non_null

    # Pure re-spelling preserves the underlying category set. A real new/removed
    # category means substitution/split territory, so abstain — but only when a
    # category is *significant* (>= MIN_SHARE) on one side and *entirely absent*
    # on the other. A low-share category that merely jitters across MIN_SHARE
    # between two samples (e.g. diamonds `cut` "Fair" at ~3%: 3.2% here, 2.6%
    # there) is the *same* category present on both sides, not a new/removed one,
    # and must not block detection. Sub-threshold tail keys that surface in only
    # one truncated top_k are ignored the same way (they are not significant).
    base_sig = {n for n in base_groups if _share(base_groups, n, base_non_null) >= MIN_SHARE}
    curr_sig = {n for n in curr_groups if _share(curr_groups, n, curr_non_null) >= MIN_SHARE}
    appeared = {n for n in curr_sig if n not in base_groups}
    disappeared = {n for n in base_sig if n not in curr_groups}
    if appeared or disappeared:
        return None

    changed_mass = 0.0
    tv_distance = 0.0
    changes: list[tuple[float, str, str, str]] = []
    for norm in set(base_groups) | set(curr_groups):
        base_raw = base_groups.get(norm, {})
        curr_raw = curr_groups.get(norm, {})
        base_share = sum(base_raw.values()) / base_non_null
        curr_share = sum(curr_raw.values()) / curr_non_null
        tv_distance += abs(curr_share - base_share)

        # Only a category with real mass can drive a re-casing symptom; a flicker
        # on a sub-threshold tail value must not fabricate one.
        if base_share < MIN_SHARE or not base_raw or not curr_raw:
            continue
        # Representative raw spelling on each side (dominant, lexicographic tie-break).
        base_rep = min(base_raw.items(), key=lambda item: (-item[1], item[0]))[0]
        curr_rep = min(curr_raw.items(), key=lambda item: (-item[1], item[0]))[0]
        if base_rep != curr_rep:
            changed_mass += base_share
            changes.append((base_share, norm, base_rep, curr_rep))

    tv_distance /= 2.0
    if tv_distance > MASS_TOLERANCE:
        return None
    if not changes:
        # Same normalized keys, same spelling: nothing actually changed.
        return None

    # Report the largest re-spelled category (lexicographic tie-break on norm key).
    _, _, from_value, to_value = min(changes, key=lambda c: (-c[0], c[1]))
    magnitude = min(1.0, changed_mass)

    return Symptom(
        signature="case_format_normalization",
        column=baseline.name,
        magnitude=magnitude,
        from_value=from_value,
        to_value=to_value,
        description=(
            f"Categories in {baseline.name!r} were re-spelled without changing the "
            f"underlying set (e.g. {from_value!r} -> {to_value!r}); "
            f"{changed_mass:.1%} of rows changed surface form."
        ),
        evidence={
            "changed_share": round(changed_mass, 4),
            "respelled_count": len(changes),
            "example_from": from_value,
            "example_to": to_value,
            "total_variation": round(tv_distance, 4),
        },
    )

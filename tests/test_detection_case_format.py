from dvi.detection import Symptom, detect_case_format_normalization
from dvi.profiling import ColumnProfile


def _cat(name: str, top_k: dict[str, int]) -> ColumnProfile:
    total = sum(top_k.values())
    return ColumnProfile(
        name=name,
        row_count=total,
        null_count=0,
        distinct_count=len(top_k),
        top_k=dict(top_k),
    )


def test_detects_lowercasing_of_all_categories():
    baseline = _cat("country", {"US": 600, "UK": 200, "DE": 200})
    current = _cat("country", {"us": 600, "uk": 200, "de": 200})

    symptom = detect_case_format_normalization(baseline, current)

    assert isinstance(symptom, Symptom)
    assert symptom.signature == "case_format_normalization"
    # Every category was re-cased, so essentially all mass moved spelling.
    assert symptom.magnitude == 1.0


def test_detects_trailing_whitespace_on_one_category():
    baseline = _cat("status", {"active": 700, "inactive": 300})
    current = _cat("status", {"active ": 700, "inactive": 300})

    symptom = detect_case_format_normalization(baseline, current)

    assert symptom is not None
    assert symptom.from_value == "active"
    assert symptom.to_value == "active "
    assert abs(symptom.magnitude - 0.7) < 1e-6


def test_genuine_substitution_is_not_a_case_format_change():
    # "UK" -> "United Kingdom": different even after casefolding, so #2 must abstain.
    baseline = _cat("country", {"US": 620, "UK": 200, "DE": 180})
    current = _cat("country", {"US": 620, "United Kingdom": 198, "DE": 182})

    assert detect_case_format_normalization(baseline, current) is None


def test_stable_categories_do_not_fire():
    top_k = {"US": 600, "UK": 200, "DE": 200}
    stable = detect_case_format_normalization(_cat("country", top_k), _cat("country", dict(top_k)))
    assert stable is None


def test_noise_sized_tail_category_does_not_block_detection():
    # The dominant categories are an obvious re-casing. A single noise-sized tail
    # value ("zz", 0.5% of rows) surfaces in current's top_k but not baseline's.
    # Exact normalized-set equality would bail on that tail difference; a
    # significance-aware set comparison ignores sub-threshold keys and still
    # reports the re-spelling of the dominant categories.
    baseline = _cat("country", {"US": 600, "UK": 200, "DE": 195})
    current = _cat("country", {"us": 600, "uk": 200, "de": 190, "zz": 5})

    symptom = detect_case_format_normalization(baseline, current)

    assert symptom is not None
    assert symptom.signature == "case_format_normalization"


def test_respelling_of_only_a_noise_sized_category_does_not_fire():
    # The only surface-form change is on a 0.5%-share tail value (zz -> ZZ). That
    # is below the relevance floor the other categorical detectors enforce, so a
    # tail flicker must not fabricate a case/format symptom on its own.
    baseline = _cat("country", {"US": 600, "UK": 395, "zz": 5})
    current = _cat("country", {"US": 600, "UK": 395, "ZZ": 5})

    assert detect_case_format_normalization(baseline, current) is None


def test_boundary_category_jitter_does_not_block_detection():
    # A low-share category ("fair", ~3%) sits right on the MIN_SHARE floor and
    # jitters across it between two disjoint real samples: 3.2% in baseline,
    # 2.6% in current. It is *present with real mass on both sides* — the same
    # category, not a new/removed one — while the dominant categories are an
    # obvious re-casing. A hard significant-set equality bails on that jitter
    # (this is the diamonds `cut` "Fair" flicker); an appeared/disappeared test
    # ignores it and still reports the re-spelling.
    baseline = _cat("cut", {"Ideal": 400, "Premium": 340, "Good": 228, "Fair": 32})
    current = _cat("cut", {"IDEAL": 402, "PREMIUM": 338, "GOOD": 234, "FAIR": 26})

    symptom = detect_case_format_normalization(baseline, current)

    assert symptom is not None
    assert symptom.signature == "case_format_normalization"


def test_new_significant_category_still_blocks():
    # A genuinely new significant category ("CA", 10%, absent in baseline) is
    # substitution/split territory, not re-casing — the detector must still
    # abstain even though "US" -> "us" looks like a re-spelling on its own.
    baseline = _cat("country", {"US": 600, "UK": 400})
    current = _cat("country", {"us": 500, "uk": 400, "CA": 100})

    assert detect_case_format_normalization(baseline, current) is None


def test_small_new_category_blocks_only_via_appeared_gate():
    # Pins the appeared/disappeared gate as load-bearing, not redundant with the
    # total-variation guard. "CA" is a new significant category (3.5%, absent in
    # baseline) fed by a matching drop elsewhere, so the total-variation distance
    # is only 0.035 <= MASS_TOLERANCE (0.05) and the TV guard would let it pass.
    # Only the appeared-category gate recognises "ca" as a genuinely new category
    # and abstains; delete that gate and this re-casing would be mislabelled.
    baseline = _cat("country", {"US": 490, "UK": 475, "XX": 35})
    current = _cat("country", {"us": 475, "uk": 475, "xx": 15, "CA": 35})

    assert detect_case_format_normalization(baseline, current) is None


def test_category_dropping_below_threshold_still_detects():
    # A category present on *both* sides but whose share slips from significant
    # (3.5%) to a sub-threshold tail (2.0%) is the same category losing mass, not
    # a new/removed one. Strict significant-set equality would have abstained
    # here (the sets differ); the appeared/disappeared test does not, because
    # "rare" is entirely absent on neither side. The dominant re-casing is still
    # reported — this documents the intended loosening over the old gate.
    baseline = _cat("country", {"US": 500, "UK": 465, "RARE": 35})
    current = _cat("country", {"us": 500, "uk": 480, "rare": 20})

    symptom = detect_case_format_normalization(baseline, current)

    assert symptom is not None
    assert symptom.signature == "case_format_normalization"


def test_returns_none_for_numeric_column():
    from dvi.profiling import NumericStats

    prof = ColumnProfile(
        name="amount",
        row_count=3,
        null_count=0,
        distinct_count=3,
        numeric=NumericStats(count=3, mean=2, stddev=1, minimum=1, maximum=3),
    )
    assert detect_case_format_normalization(prof, prof) is None

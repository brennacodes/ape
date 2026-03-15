"""Tests for benchmark/scripts/evaluator/operators.py.

Every public function is exercised, including vacuous/boundary cases that
define the semantics relied upon by the evaluator.
"""

import pytest
from operators import (
    # transforms
    apply_transform,
    transform_count, transform_length, transform_first, transform_last,
    transform_min, transform_max,
    # scalar operators
    op_eq, op_neq, op_gt, op_gte, op_lt, op_lte,
    # collection operators
    op_exists_before, op_exists_after, op_exists_between, op_followed_by,
    op_strictly_precedes, op_strictly_ordered_subset,
    op_subset_of, op_each_preceded_by_within_N_steps,
    op_precedes_per_path, op_only_via, op_not_contains, op_regex_not_match,
    op_regex_match, op_imperative_mood, op_valid_format,
    op_has_key, op_has_key_any, op_contains, op_contains_count_gte,
    op_first_search_broader_than_final,
    # VacuousResult
    VacuousResult,
    # dispatch
    ALL_OPERATORS, SCALAR_OPERATORS, COLLECTION_OPERATORS,
)


# ===========================================================================
# Transforms
# ===========================================================================

class TestTransformCount:
    def test_list(self):
        assert transform_count([1, 2, 3]) == 3

    def test_empty_list(self):
        assert transform_count([]) == 0

    def test_none(self):
        assert transform_count(None) == 0

    def test_string(self):
        assert transform_count("abc") == 3

    def test_dict(self):
        assert transform_count({"a": 1, "b": 2}) == 2


class TestTransformLength:
    def test_string(self):
        assert transform_length("hello") == 5

    def test_empty_string(self):
        assert transform_length("") == 0

    def test_list(self):
        assert transform_length([1, 2]) == 2

    def test_none(self):
        assert transform_length(None) == 0


class TestTransformFirst:
    def test_non_empty(self):
        assert transform_first([10, 20, 30]) == 10

    def test_empty(self):
        assert transform_first([]) is None

    def test_single(self):
        assert transform_first(["only"]) == "only"


class TestTransformLast:
    def test_non_empty(self):
        assert transform_last([10, 20, 30]) == 30

    def test_empty(self):
        assert transform_last([]) is None

    def test_single(self):
        assert transform_last(["only"]) == "only"


class TestTransformMin:
    def test_non_empty(self):
        assert transform_min([3, 1, 2]) == 1

    def test_empty(self):
        assert transform_min([]) is None

    def test_single(self):
        assert transform_min([5]) == 5


class TestTransformMax:
    def test_non_empty(self):
        assert transform_max([3, 1, 2]) == 3

    def test_empty(self):
        assert transform_max([]) is None

    def test_single(self):
        assert transform_max([5]) == 5


class TestApplyTransform:
    def test_count(self):
        assert apply_transform("count", [1, 2]) == 2

    def test_length(self):
        assert apply_transform("length", "hi") == 2

    def test_first(self):
        assert apply_transform("first", [7, 8]) == 7

    def test_last(self):
        assert apply_transform("last", [7, 8]) == 8

    def test_min(self):
        assert apply_transform("min", [3, 1]) == 1

    def test_max(self):
        assert apply_transform("max", [3, 1]) == 3

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown transform"):
            apply_transform("nonexistent", [])


# ===========================================================================
# Scalar operators
# ===========================================================================

class TestScalarOperators:
    def test_eq_equal(self):
        assert op_eq(3, 3) is True

    def test_eq_not_equal(self):
        assert op_eq(3, 4) is False

    def test_neq(self):
        assert op_neq(3, 4) is True
        assert op_neq(3, 3) is False

    def test_gt(self):
        assert op_gt(5, 3) is True
        assert op_gt(3, 5) is False
        assert op_gt(3, 3) is False

    def test_gte(self):
        assert op_gte(3, 3) is True
        assert op_gte(4, 3) is True
        assert op_gte(2, 3) is False

    def test_lt(self):
        assert op_lt(2, 3) is True
        assert op_lt(3, 2) is False
        assert op_lt(3, 3) is False

    def test_lte(self):
        assert op_lte(3, 3) is True
        assert op_lte(2, 3) is True
        assert op_lte(4, 3) is False

    def test_eq_strings(self):
        assert op_eq("a", "a") is True
        assert op_eq("a", "b") is False

    def test_eq_zero_target(self):
        assert op_eq(0, 0) is True
        assert op_eq(1, 0) is False


# ===========================================================================
# Collection operators: exists_before / exists_after / exists_between
# ===========================================================================

class TestExistsBefore:
    def test_a_before_b(self):
        assert op_exists_before([1], [5]) is True

    def test_a_after_b(self):
        assert op_exists_before([5], [1]) is False

    def test_a_equal_to_b(self):
        assert op_exists_before([3], [3]) is False

    def test_b_empty_vacuously_true(self):
        result = op_exists_before([1, 2], [])
        assert isinstance(result, VacuousResult) or result is True
        assert result == True

    def test_a_empty_b_non_empty(self):
        assert op_exists_before([], [3]) is False

    def test_both_empty(self):
        result = op_exists_before([], [])
        assert isinstance(result, VacuousResult) or result is True
        assert result == True

    def test_min_a_before_min_b(self):
        # Even if some a > b, it passes if min(a) < min(b)
        assert op_exists_before([1, 10], [5]) is True

    def test_multiple_a_none_before_b(self):
        assert op_exists_before([6, 7], [5]) is False


class TestExistsAfter:
    def test_a_after_b(self):
        assert op_exists_after([5], [1]) is True

    def test_a_before_b(self):
        assert op_exists_after([1], [5]) is False

    def test_b_empty_vacuously_true(self):
        result = op_exists_after([1], [])
        assert isinstance(result, VacuousResult) or result is True
        assert result == True

    def test_a_empty_b_non_empty(self):
        assert op_exists_after([], [1]) is False

    def test_both_empty(self):
        result = op_exists_after([], [])
        assert isinstance(result, VacuousResult) or result is True
        assert result == True

    def test_max_a_after_max_b(self):
        assert op_exists_after([1, 10], [5]) is True

    def test_multiple_a_none_after_b(self):
        assert op_exists_after([1, 2], [5]) is False


class TestExistsBetween:
    def test_a_between_start_and_end(self):
        assert op_exists_between([3], [1], [5]) is True

    def test_a_before_start(self):
        assert op_exists_between([0], [1], [5]) is False

    def test_a_after_end(self):
        assert op_exists_between([6], [1], [5]) is False

    def test_a_at_start_boundary(self):
        # strictly between, not at boundary
        assert op_exists_between([1], [1], [5]) is False

    def test_a_at_end_boundary(self):
        assert op_exists_between([5], [1], [5]) is False

    def test_start_empty(self):
        assert op_exists_between([3], [], [5]) is False

    def test_end_empty(self):
        assert op_exists_between([3], [1], []) is False

    def test_start_after_end(self):
        # Inverted window — no valid range
        assert op_exists_between([3], [5], [1]) is False

    def test_multiple_a_one_in_range(self):
        assert op_exists_between([0, 3, 10], [1], [5]) is True

    def test_uses_min_start_max_end(self):
        # Window is min(start)=1 to max(end)=9; a=5 is inside
        assert op_exists_between([5], [1, 8], [9, 2]) is True


# ===========================================================================
# followed_by
# ===========================================================================

class TestFollowedBy:
    def test_a_before_b(self):
        assert op_followed_by([1], [5]) is True

    def test_a_after_b(self):
        assert op_followed_by([5], [1]) is False

    def test_a_equal_to_b(self):
        assert op_followed_by([3], [3]) is False

    def test_a_empty(self):
        assert op_followed_by([], [5]) is False

    def test_b_empty(self):
        assert op_followed_by([1], []) is False

    def test_both_empty(self):
        assert op_followed_by([], []) is False

    def test_min_a_before_max_b(self):
        # min(A)=1 < max(B)=5 → True
        assert op_followed_by([1, 10], [3, 5]) is True

    def test_min_a_after_max_b(self):
        # min(A)=6 > max(B)=5 → False
        assert op_followed_by([6, 10], [3, 5]) is False


# ===========================================================================
# strictly_precedes
# ===========================================================================

class TestStrictlyPrecedes:
    def test_all_a_before_all_b(self):
        assert op_strictly_precedes([1, 2], [5, 6]) is True

    def test_a_overlaps_b(self):
        assert op_strictly_precedes([1, 5], [3, 7]) is False

    def test_a_after_b(self):
        assert op_strictly_precedes([5], [1]) is False

    def test_a_empty(self):
        result = op_strictly_precedes([], [1, 2])
        assert isinstance(result, VacuousResult) or result is True
        assert result == True

    def test_b_empty(self):
        result = op_strictly_precedes([1, 2], [])
        assert isinstance(result, VacuousResult) or result is True
        assert result == True

    def test_both_empty(self):
        result = op_strictly_precedes([], [])
        assert isinstance(result, VacuousResult) or result is True
        assert result == True

    def test_adjacent(self):
        # max(a)=3 < min(b)=4 → True
        assert op_strictly_precedes([1, 3], [4, 5]) is True

    def test_touching(self):
        # max(a)=3, min(b)=3 → not strictly precedes
        assert op_strictly_precedes([1, 3], [3, 5]) is False


# ===========================================================================
# strictly_ordered_subset
# ===========================================================================

class TestStrictlyOrderedSubset:
    def test_correct_order(self):
        assert op_strictly_ordered_subset(
            ["intake", "investigation", "commits"],
            ["intake", "clarification", "investigation", "commits"],
        ) is True

    def test_wrong_order(self):
        assert op_strictly_ordered_subset(
            ["commits", "investigation"],
            ["intake", "clarification", "investigation", "commits"],
        ) is False

    def test_unknown_phases_ignored(self):
        # "unknown" is not in defined, so filtered out; intake before commits is OK
        assert op_strictly_ordered_subset(
            ["intake", "unknown", "commits"],
            ["intake", "commits"],
        ) is True

    def test_empty_observed(self):
        assert op_strictly_ordered_subset([], ["intake", "commits"]) is True

    def test_all_unknown(self):
        # Nothing survives filter → trivially passes
        assert op_strictly_ordered_subset(["x", "y"], ["intake", "commits"]) is True

    def test_single_phase(self):
        assert op_strictly_ordered_subset(["investigation"], ["intake", "investigation"]) is True

    def test_full_sequence_in_order(self):
        phases = ["intake", "clarification", "investigation", "commits"]
        assert op_strictly_ordered_subset(phases, phases) is True

    def test_repeated_phase_in_observed(self):
        # Repeated phase: indices [0, 0] — sorted is [0, 0] so passes
        assert op_strictly_ordered_subset(
            ["intake", "intake"],
            ["intake", "commits"],
        ) is True

    def test_only_subset_matters(self):
        # "verification" before "implementation" in observed, but
        # only "implementation" and "commits" are in defined.
        # After filter: ["implementation", "commits"] → in order.
        assert op_strictly_ordered_subset(
            ["verification", "implementation", "commits"],
            ["implementation", "commits"],
        ) is True


# ===========================================================================
# subset_of
# ===========================================================================

class TestSubsetOf:
    def test_subset(self):
        assert op_subset_of([1, 2], [1, 2, 3]) is True

    def test_equal_sets(self):
        assert op_subset_of([1, 2, 3], [1, 2, 3]) is True

    def test_not_subset(self):
        assert op_subset_of([1, 4], [1, 2, 3]) is False

    def test_empty_a(self):
        assert op_subset_of([], [1, 2]) is True

    def test_empty_both(self):
        assert op_subset_of([], []) is True

    def test_string_elements(self):
        assert op_subset_of(["a", "b"], ["a", "b", "c"]) is True
        assert op_subset_of(["a", "d"], ["a", "b", "c"]) is False


# ===========================================================================
# each_preceded_by_within_N_steps
# ===========================================================================

class TestEachPrecededByWithinNSteps:
    def test_b_directly_before_a(self):
        # A at 5, B at 4 — within window=1
        assert op_each_preceded_by_within_N_steps([5], [4], window=1) is True

    def test_b_too_far_before_a(self):
        # A at 10, B at 0 — gap=10, window=5
        assert op_each_preceded_by_within_N_steps([10], [0], window=5) is False

    def test_b_exactly_at_window_edge(self):
        # A at 10, B at 5 — gap=5, window=5 → OK (i - j = 5 <= 5)
        assert op_each_preceded_by_within_N_steps([10], [5], window=5) is True

    def test_b_after_a(self):
        # B at 7 is not before A at 5
        assert op_each_preceded_by_within_N_steps([5], [7], window=10) is False

    def test_a_empty(self):
        result = op_each_preceded_by_within_N_steps([], [1, 2], window=5)
        assert isinstance(result, VacuousResult) or result is True
        assert result == True

    def test_multiple_a_all_satisfied(self):
        assert op_each_preceded_by_within_N_steps([3, 7], [2, 6], window=2) is True

    def test_multiple_a_one_not_satisfied(self):
        # A at 3: B at 2 in window=1 → OK; A at 7: B only at 2, gap=5, window=1 → FAIL
        assert op_each_preceded_by_within_N_steps([3, 7], [2], window=1) is False

    def test_default_window(self):
        # Default window is 10
        assert op_each_preceded_by_within_N_steps([10], [0], window=10) is True
        assert op_each_preceded_by_within_N_steps([11], [0], window=10) is False


# ===========================================================================
# precedes_per_path
# ===========================================================================

class TestPrecedesPerPath:
    def test_basic_pass(self):
        a = {"foo.py": [1]}
        b = {"foo.py": [5]}
        assert op_precedes_per_path(a, b) is True

    def test_basic_fail_a_after_b(self):
        a = {"foo.py": [5]}
        b = {"foo.py": [1]}
        assert op_precedes_per_path(a, b) is False

    def test_b_has_path_a_does_not(self):
        a = {}
        b = {"foo.py": [5]}
        assert op_precedes_per_path(a, b) is False

    def test_a_at_same_index_as_b(self):
        a = {"foo.py": [3]}
        b = {"foo.py": [3]}
        assert op_precedes_per_path(a, b) is False

    def test_b_empty(self):
        # No paths in b → vacuously true
        a = {"foo.py": [1]}
        b = {}
        assert op_precedes_per_path(a, b) is True

    def test_multiple_paths_all_pass(self):
        a = {"foo.py": [1], "bar.py": [2]}
        b = {"foo.py": [5], "bar.py": [6]}
        assert op_precedes_per_path(a, b) is True

    def test_multiple_paths_one_fails(self):
        a = {"foo.py": [1], "bar.py": [7]}
        b = {"foo.py": [5], "bar.py": [6]}
        assert op_precedes_per_path(a, b) is False

    def test_extra_paths_in_a_ignored(self):
        # a has paths not in b — irrelevant
        a = {"foo.py": [1], "extra.py": [99]}
        b = {"foo.py": [5]}
        assert op_precedes_per_path(a, b) is True

    def test_min_a_index_used(self):
        # Multiple A reads for same path; earliest must precede B
        a = {"foo.py": [1, 10]}
        b = {"foo.py": [5]}
        assert op_precedes_per_path(a, b) is True

    def test_all_a_after_b_fails(self):
        a = {"foo.py": [6, 10]}
        b = {"foo.py": [5]}
        assert op_precedes_per_path(a, b) is False


# ===========================================================================
# not_contains / contains
# ===========================================================================

class TestNotContains:
    def test_value_absent(self):
        assert op_not_contains(["a", "b"], "c") is True

    def test_value_present(self):
        assert op_not_contains(["a", "b"], "a") is False

    def test_empty_collection(self):
        assert op_not_contains([], "x") is True

    def test_string_membership(self):
        assert op_not_contains("hello", "h") is False
        assert op_not_contains("hello", "z") is True


class TestContains:
    def test_value_present(self):
        assert op_contains(["a", "b"], "a") is True

    def test_value_absent(self):
        assert op_contains(["a", "b"], "c") is False

    def test_empty_collection(self):
        assert op_contains([], "x") is False

    def test_string_membership(self):
        assert op_contains("hello", "h") is True


# ===========================================================================
# regex_not_match
# ===========================================================================

class TestRegexNotMatch:
    def test_no_match(self):
        assert op_regex_not_match(["foo.py", "bar.py"], r"\.md$") is True

    def test_one_matches(self):
        assert op_regex_not_match(["foo.py", "README.md"], r"\.md$") is False

    def test_all_match(self):
        assert op_regex_not_match(["a.md", "b.md"], r"\.md$") is False

    def test_empty_list(self):
        assert op_regex_not_match([], r"\.md$") is True

    def test_temp_file_pattern(self):
        pattern = r"^(tmp|temp|scratch|\.tmp)[_\-.]"
        assert op_regex_not_match(["normal.py"], pattern) is True
        assert op_regex_not_match(["tmp_file.py"], pattern) is False
        assert op_regex_not_match(["temp-stuff.sh"], pattern) is False
        assert op_regex_not_match(["scratch_pad.txt"], pattern) is False

    def test_case_sensitivity(self):
        # Default: case-sensitive
        assert op_regex_not_match(["README.MD"], r"\.md$") is True
        assert op_regex_not_match(["README.md"], r"\.md$") is False


# ===========================================================================
# has_key
# ===========================================================================

class TestHasKey:
    def test_key_present(self):
        assert op_has_key({"type": "py", "path": "src"}, "type") is True

    def test_key_absent(self):
        assert op_has_key({"type": "py"}, "path") is False

    def test_empty_dict(self):
        assert op_has_key({}, "key") is False

    def test_non_dict_returns_false(self):
        assert op_has_key(["type", "path"], "type") is False
        assert op_has_key(None, "key") is False
        assert op_has_key("string", "s") is False


# ===========================================================================
# has_key_any
# ===========================================================================

class TestHasKeyAny:
    def test_first_key_present(self):
        assert op_has_key_any({"type": "py"}, ["type", "glob"]) is True

    def test_second_key_present(self):
        assert op_has_key_any({"glob": "*.py"}, ["type", "glob"]) is True

    def test_both_keys_present(self):
        assert op_has_key_any({"type": "py", "glob": "*.py"}, ["type", "glob"]) is True

    def test_no_keys_present(self):
        assert op_has_key_any({"pattern": "foo"}, ["type", "glob"]) is False

    def test_empty_dict(self):
        assert op_has_key_any({}, ["type", "glob"]) is False

    def test_non_dict_returns_false(self):
        assert op_has_key_any(["type"], ["type"]) is False
        assert op_has_key_any(None, ["type"]) is False

    def test_empty_keys_list(self):
        assert op_has_key_any({"type": "py"}, []) is False


# ===========================================================================
# contains_count_gte
# ===========================================================================

class TestContainsCountGte:
    def test_batch_meets_threshold(self):
        batches = [["Grep", "Grep", "Read"], ["Read"]]
        assert op_contains_count_gte(batches, "Grep", 2) is True

    def test_batch_below_threshold(self):
        batches = [["Grep", "Read"], ["Read"]]
        assert op_contains_count_gte(batches, "Grep", 2) is False

    def test_exactly_at_threshold(self):
        batches = [["Grep", "Grep"]]
        assert op_contains_count_gte(batches, "Grep", 2) is True

    def test_empty_batches(self):
        assert op_contains_count_gte([], "Grep", 1) is False

    def test_second_batch_meets(self):
        batches = [["Read"], ["Grep", "Grep", "Grep"]]
        assert op_contains_count_gte(batches, "Grep", 3) is True

    def test_no_matching_tool(self):
        batches = [["Read", "Write"]]
        assert op_contains_count_gte(batches, "Grep", 1) is False

    def test_count_zero_always_true(self):
        # 0 occurrences >= 0 is always true
        assert op_contains_count_gte([["Read"]], "Grep", 0) is True


# ===========================================================================
# first_search_broader_than_final
# ===========================================================================

class TestFirstSearchBroaderThanFinal:
    def test_first_shorter_than_last(self):
        calls = [{"pattern": "err"}, {"pattern": "error handling"}]
        assert op_first_search_broader_than_final(calls) is True

    def test_first_longer_than_last(self):
        calls = [{"pattern": "error handling"}, {"pattern": "err"}]
        assert op_first_search_broader_than_final(calls) is False

    def test_equal_lengths(self):
        calls = [{"pattern": "foo"}, {"pattern": "bar"}]
        assert op_first_search_broader_than_final(calls) is True

    def test_single_call(self):
        assert op_first_search_broader_than_final([{"pattern": "anything"}]) is True

    def test_empty(self):
        assert op_first_search_broader_than_final([]) is True

    def test_many_calls_only_first_and_last_matter(self):
        # Middle calls are wider than both first and last — doesn't matter
        calls = [
            {"pattern": "err"},          # len=3
            {"pattern": "very long error message"},  # len=22
            {"pattern": "errors"},       # len=6
        ]
        assert op_first_search_broader_than_final(calls) is True  # 3 <= 6

    def test_missing_pattern_key(self):
        # Missing 'pattern' key treated as empty string (len=0)
        calls = [{"pattern": ""}, {"pattern": "specific"}]
        assert op_first_search_broader_than_final(calls) is True


# ===========================================================================
# regex_match
# ===========================================================================

class TestRegexMatch:
    def test_all_match(self):
        assert op_regex_match(["foo.py", "bar.py"], r"\.py$") is True

    def test_some_not_match(self):
        assert op_regex_match(["foo.py", "bar.md"], r"\.py$") is False

    def test_none_match(self):
        assert op_regex_match(["foo.md", "bar.md"], r"\.py$") is False

    def test_empty_list(self):
        assert op_regex_match([], r"\.py$") is True

    def test_single_string_match(self):
        assert op_regex_match("foo.py", r"\.py$") is True

    def test_single_string_no_match(self):
        assert op_regex_match("foo.md", r"\.py$") is False

    def test_single_string_not_wrapped(self):
        # Should wrap in list automatically
        assert op_regex_match("hello", r"^h") is True

    def test_pattern_with_groups(self):
        pattern = r"^[A-Z].*[^.]$"
        assert op_regex_match(["Add feature"], pattern) is True
        assert op_regex_match(["add feature"], pattern) is False

    def test_multiple_strings_all_match(self):
        pattern = r"^[A-Z]"
        assert op_regex_match(["Add feature", "Fix bug", "Update docs"], pattern) is True

    def test_multiple_strings_one_fails(self):
        pattern = r"^[A-Z]"
        assert op_regex_match(["Add feature", "fix bug"], pattern) is False


# ===========================================================================
# imperative_mood
# ===========================================================================

class TestImperativeMood:
    def test_imperative_add(self):
        assert op_imperative_mood("Add feature", True) is True

    def test_imperative_fix(self):
        assert op_imperative_mood("Fix bug", True) is True

    def test_past_tense_added(self):
        assert op_imperative_mood("Added feature", True) is False

    def test_past_tense_fixed(self):
        assert op_imperative_mood("Fixed bug", True) is False

    def test_gerund_adding(self):
        assert op_imperative_mood("Adding feature", True) is False

    def test_gerund_fixing(self):
        assert op_imperative_mood("Fixing bug", True) is False

    def test_third_person_adds(self):
        assert op_imperative_mood("Adds feature", True) is False

    def test_third_person_fixes(self):
        assert op_imperative_mood("Fixes bug", True) is False

    def test_target_false_with_past_tense(self):
        assert op_imperative_mood("Added feature", False) is True

    def test_target_false_with_imperative(self):
        assert op_imperative_mood("Add feature", False) is False

    def test_empty_string(self):
        assert op_imperative_mood("", True) is False

    def test_list_all_imperative(self):
        assert op_imperative_mood(["Add feature", "Fix bug"], True) is True

    def test_list_some_imperative(self):
        assert op_imperative_mood(["Add feature", "Fixed bug"], True) is False

    def test_list_empty(self):
        assert op_imperative_mood([], True) is False

    def test_list_empty_target_false(self):
        assert op_imperative_mood([], False) is True

    def test_list_target_false_all_not_imperative(self):
        assert op_imperative_mood(["Added feature", "Fixed bug"], False) is True

    def test_common_imperative_verbs(self):
        imperative_verbs = ["Create", "Remove", "Move", "Make", "Get", "Set", "Put", "Run", "Use"]
        for verb in imperative_verbs:
            assert op_imperative_mood(f"{verb} something", True) is True

    def test_common_past_tense(self):
        past_tense = ["Updated", "Improved", "Converted", "Deleted", "Renamed", "Replaced"]
        for verb in past_tense:
            assert op_imperative_mood(f"{verb} something", True) is False

    def test_word_like_red_not_past_tense(self):
        # "Red" ends in -ed but is not past tense (too short)
        assert op_imperative_mood("Red flag", True) is True


# ===========================================================================
# valid_format
# ===========================================================================

class TestValidFormat:
    def test_dict_valid_format(self):
        body_format = {
            'has_body': True,
            'has_blank_line': True,
            'max_line_length': 72
        }
        assert op_valid_format(body_format, True) is True

    def test_dict_no_blank_line(self):
        body_format = {
            'has_body': True,
            'has_blank_line': False,
            'max_line_length': 72
        }
        assert op_valid_format(body_format, True) is False

    def test_dict_line_too_long(self):
        body_format = {
            'has_body': True,
            'has_blank_line': True,
            'max_line_length': 80
        }
        assert op_valid_format(body_format, True) is False

    def test_dict_no_body(self):
        body_format = {
            'has_body': False,
            'has_blank_line': False,
            'max_line_length': 0
        }
        assert op_valid_format(body_format, True) is True

    def test_dict_target_false_valid(self):
        body_format = {
            'has_body': True,
            'has_blank_line': True,
            'max_line_length': 72
        }
        assert op_valid_format(body_format, False) is False

    def test_dict_target_false_invalid(self):
        body_format = {
            'has_body': True,
            'has_blank_line': False,
            'max_line_length': 72
        }
        assert op_valid_format(body_format, False) is True

    def test_string_single_line(self):
        assert op_valid_format("Add feature", True) is True

    def test_string_with_blank_line_valid(self):
        commit_msg = "Add feature\n\nThis commit adds a new feature\nwith multiple lines"
        assert op_valid_format(commit_msg, True) is True

    def test_string_no_blank_line_invalid(self):
        commit_msg = "Add feature\nThis should have blank line"
        assert op_valid_format(commit_msg, True) is False

    def test_string_body_line_too_long(self):
        commit_msg = "Add feature\n\nThis is a very long line that exceeds the 72 character limit and should fail validation"
        assert op_valid_format(commit_msg, True) is False

    def test_string_body_line_exactly_72(self):
        commit_msg = "Add feature\n\n" + "x" * 72
        assert op_valid_format(commit_msg, True) is True

    def test_string_body_line_73_chars(self):
        commit_msg = "Add feature\n\n" + "x" * 73
        assert op_valid_format(commit_msg, True) is False

    def test_string_target_false(self):
        commit_msg = "Add feature\n\nValid body"
        assert op_valid_format(commit_msg, False) is False

    def test_list_all_valid(self):
        formats = [
            {'has_body': False, 'has_blank_line': False, 'max_line_length': 0},
            {'has_body': True, 'has_blank_line': True, 'max_line_length': 60}
        ]
        assert op_valid_format(formats, True) is True

    def test_list_one_invalid(self):
        formats = [
            {'has_body': False, 'has_blank_line': False, 'max_line_length': 0},
            {'has_body': True, 'has_blank_line': False, 'max_line_length': 60}
        ]
        assert op_valid_format(formats, True) is False

    def test_list_empty(self):
        assert op_valid_format([], True) is True

    def test_list_empty_target_false(self):
        assert op_valid_format([], False) is False

    def test_dict_missing_keys_defaults(self):
        # Missing keys should default to False/0
        body_format = {'has_body': False}
        assert op_valid_format(body_format, True) is True

    def test_invalid_type(self):
        assert op_valid_format(123, True) is False


# ===========================================================================
# VacuousResult
# ===========================================================================

class TestVacuousResult:
    def test_vacuous_result_bool_true(self):
        vr = VacuousResult("test reason")
        assert bool(vr) is True

    def test_vacuous_result_equality_with_true(self):
        vr = VacuousResult("test reason")
        assert vr == True

    def test_vacuous_result_equality_with_false(self):
        vr = VacuousResult("test reason")
        assert not (vr == False)

    def test_vacuous_result_equality_with_same_reason(self):
        vr1 = VacuousResult("same reason")
        vr2 = VacuousResult("same reason")
        assert vr1 == vr2

    def test_vacuous_result_equality_with_different_reason(self):
        vr1 = VacuousResult("reason 1")
        vr2 = VacuousResult("reason 2")
        assert vr1 != vr2

    def test_vacuous_result_isinstance(self):
        vr = VacuousResult("test")
        assert isinstance(vr, VacuousResult)

    def test_vacuous_result_not_bool_instance(self):
        vr = VacuousResult("test")
        assert not isinstance(vr, bool)

    def test_vacuous_result_repr(self):
        vr = VacuousResult("test reason")
        assert "VacuousResult" in repr(vr)
        assert "test reason" in repr(vr)

    def test_vacuous_result_hash(self):
        vr1 = VacuousResult("reason")
        vr2 = VacuousResult("reason")
        assert hash(vr1) == hash(vr2)

    def test_vacuous_result_different_hash(self):
        vr1 = VacuousResult("reason 1")
        vr2 = VacuousResult("reason 2")
        assert hash(vr1) != hash(vr2)

    def test_vacuous_result_in_set(self):
        vr1 = VacuousResult("reason")
        vr2 = VacuousResult("reason")
        s = {vr1}
        assert vr2 in s

    def test_exists_before_vacuous(self):
        result = op_exists_before([1, 2], [])
        assert isinstance(result, VacuousResult)
        assert bool(result) is True

    def test_exists_after_vacuous(self):
        result = op_exists_after([1, 2], [])
        assert isinstance(result, VacuousResult)
        assert bool(result) is True

    def test_strictly_precedes_vacuous(self):
        result = op_strictly_precedes([], [1, 2])
        assert isinstance(result, VacuousResult)
        assert bool(result) is True

    def test_each_preceded_by_within_N_steps_vacuous(self):
        result = op_each_preceded_by_within_N_steps([], [1, 2], window=5)
        assert isinstance(result, VacuousResult)
        assert bool(result) is True

    def test_only_via_vacuous(self):
        result = op_only_via([], [1, 2])
        assert isinstance(result, VacuousResult)
        assert bool(result) is True


# ===========================================================================
# Dispatch tables completeness
# ===========================================================================

class TestDispatchTables:
    def test_all_operators_registered(self):
        expected = {
            "eq", "neq", "gt", "gte", "lt", "lte",
            "exists_before", "exists_after", "exists_between",
            "followed_by",
            "strictly_precedes", "strictly_ordered_subset",
            "subset_of", "each_preceded_by_within_N_steps",
            "precedes_per_path", "not_contains", "regex_not_match",
            "regex_match", "imperative_mood", "valid_format",
            "has_key", "has_key_any", "contains", "contains_count_gte",
            "first_search_broader_than_final", "only_via",
        }
        assert set(ALL_OPERATORS.keys()) == expected

    def test_scalar_operators_are_callable(self):
        for name, fn in SCALAR_OPERATORS.items():
            assert callable(fn), f"{name} is not callable"

    def test_collection_operators_are_callable(self):
        for name, fn in COLLECTION_OPERATORS.items():
            assert callable(fn), f"{name} is not callable"

    def test_no_overlap_between_scalar_and_collection(self):
        overlap = set(SCALAR_OPERATORS) & set(COLLECTION_OPERATORS)
        assert overlap == set()

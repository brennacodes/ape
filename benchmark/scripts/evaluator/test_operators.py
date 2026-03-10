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
    op_exists_before, op_exists_after, op_exists_between,
    op_strictly_precedes, op_strictly_ordered_subset,
    op_subset_of, op_each_preceded_by_within_N_steps,
    op_precedes_per_path, op_not_contains, op_regex_not_match,
    op_has_key, op_has_key_any, op_contains, op_contains_count_gte,
    op_first_search_broader_than_final,
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
        assert op_exists_before([1, 2], []) is True

    def test_a_empty_b_non_empty(self):
        assert op_exists_before([], [3]) is False

    def test_both_empty(self):
        assert op_exists_before([], []) is True

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
        assert op_exists_after([1], []) is True

    def test_a_empty_b_non_empty(self):
        assert op_exists_after([], [1]) is False

    def test_both_empty(self):
        assert op_exists_after([], []) is True

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
        assert op_strictly_precedes([], [1, 2]) is True

    def test_b_empty(self):
        assert op_strictly_precedes([1, 2], []) is True

    def test_both_empty(self):
        assert op_strictly_precedes([], []) is True

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
        assert op_each_preceded_by_within_N_steps([], [1, 2], window=5) is True

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
# Dispatch tables completeness
# ===========================================================================

class TestDispatchTables:
    def test_all_operators_registered(self):
        expected = {
            "eq", "neq", "gt", "gte", "lt", "lte",
            "exists_before", "exists_after", "exists_between",
            "strictly_precedes", "strictly_ordered_subset",
            "subset_of", "each_preceded_by_within_N_steps",
            "precedes_per_path", "not_contains", "regex_not_match",
            "has_key", "has_key_any", "contains", "contains_count_gte",
            "first_search_broader_than_final",
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

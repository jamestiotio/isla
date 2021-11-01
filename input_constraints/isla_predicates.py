import copy
import itertools
from typing import Union, List, Optional, Dict, Tuple, Generator, Callable

from fuzzingbook.Parser import canonical, EarleyParser, PEGParser
from grammar_graph.gg import GrammarGraph

from input_constraints import isla
from input_constraints.existential_helpers import insert_tree
from input_constraints.helpers import delete_unreachable, path_iterator, parent_reflexive, parent_or_child
from input_constraints.isla import DerivationTree, Constant, SemPredEvalResult, StructuralPredicate, SemanticPredicate
from input_constraints.type_defs import Grammar, Path, ParseTree


def is_before(path_1: Path, path_2: Path) -> bool:
    if not path_1 or not path_2:
        # Note: (1,) is not before (1,0), since it's a prefix!
        # Also, (1,) cannot be before ().
        # But (1,0) would be before (1,1).
        return False

    car_1, *cdr_1 = path_1
    car_2, *cdr_2 = path_2

    if car_1 < car_2:
        return True
    elif car_2 < car_1:
        return False
    else:
        return is_before(tuple(cdr_1), tuple(cdr_2))


BEFORE_PREDICATE = StructuralPredicate("before", 2, is_before)

AFTER_PREDICATE = StructuralPredicate(
    "after",
    2,
    lambda path_1, path_2:
    not is_before(path_1, path_2) and
    path_1 != path_2[:len(path_1)]  # No prefix
)


def is_same_position(path_1: Path, path_2: Path) -> bool:
    return path_1 == path_2


DIFFERENT_POSITION_PREDICATE = StructuralPredicate(
    "different_position",
    2,
    lambda p1, p2: not is_same_position(p1, p2)
)

SAME_POSITION_PREDICATE = StructuralPredicate(
    "same_position",
    2,
    is_same_position
)


def count(grammar: Grammar,
          in_tree: DerivationTree,
          needle: str,
          num: Union[Constant, DerivationTree]) -> SemPredEvalResult:
    graph = GrammarGraph.from_grammar(grammar)

    def reachable(fr: str, to: str) -> bool:
        f_node = graph.get_node(fr)
        t_node = graph.get_node(to)
        return f_node.reachable(t_node)

    num_needle_occurrences = len(in_tree.filter(lambda t: t.value == needle))

    leaf_nonterminals = [node.value for _, node in in_tree.open_leaves()]

    more_needles_possible = any(reachable(leaf_nonterminal, needle)
                                for leaf_nonterminal in leaf_nonterminals)

    if isinstance(num, Constant):
        # Return the number of needle occurrences in in_tree, or "not ready" if in_tree is not
        # closed and more needle occurrences can yet occur in in_tree
        if more_needles_possible:
            return SemPredEvalResult(None)

        return SemPredEvalResult({num: DerivationTree(str(num_needle_occurrences), None)})

    assert not num.children
    assert num.value.isnumeric()
    target_num_needle_occurrences = int(num.value)

    if num_needle_occurrences > target_num_needle_occurrences:
        return SemPredEvalResult(False)

    if not more_needles_possible:
        # TODO: We could also try to insert needle into already closed parts of the tree,
        #       similar to treatment of existential quantifiers...
        if num_needle_occurrences == target_num_needle_occurrences:
            return SemPredEvalResult(True)
        else:
            return SemPredEvalResult(False)

    if more_needles_possible and num_needle_occurrences == target_num_needle_occurrences:
        return SemPredEvalResult(None)

    assert num_needle_occurrences < target_num_needle_occurrences

    # Try to add more needles to in_tree, such that no more needles can be obtained
    # in the resulting tree from expanding leaf nonterminals.

    num_needles = lambda candidate: len(candidate.filter(lambda t: t.value == needle))

    canonical_grammar = canonical(grammar)
    candidates = [candidate for candidate in insert_tree(canonical_grammar, DerivationTree(needle, None), in_tree)
                  if num_needles(candidate) <= target_num_needle_occurrences]
    already_seen = {candidate.structural_hash() for candidate in candidates}
    while candidates:
        candidate = candidates.pop(0)
        candidate_needle_occurrences = num_needles(candidate)

        candidate_more_needles_possible = \
            any(reachable(leaf_nonterminal, needle)
                for leaf_nonterminal in [node.value for _, node in candidate.open_leaves()])

        if not candidate_more_needles_possible and candidate_needle_occurrences == target_num_needle_occurrences:
            return SemPredEvalResult({in_tree: candidate})

        if candidate_needle_occurrences < target_num_needle_occurrences:
            new_candidates = [
                new_candidate
                for new_candidate in insert_tree(canonical_grammar, DerivationTree(needle, None), candidate)
                if (num_needles(new_candidate) <= target_num_needle_occurrences
                    and not new_candidate.structural_hash() in already_seen)]

            candidates.extend(new_candidates)
            already_seen.update({new_candidate.structural_hash() for new_candidate in new_candidates})

    # TODO: Check if None would not be more appropriate. Could we have missed a better insertion opportunity?
    return SemPredEvalResult(False)


COUNT_PREDICATE = lambda grammar: SemanticPredicate(
    "count", 3, lambda in_tree, needle, num: count(grammar, in_tree, needle, num), binds_tree=True)


def embed_tree(
        orig: DerivationTree,
        extended: DerivationTree,
        leaves_to_match: Optional[Tuple[Path, ...]] = None,
        path_combinations: Optional[Tuple[Tuple[Tuple[Path, DerivationTree], Tuple[Path, DerivationTree]], ...]] = None,
) -> Tuple[Dict[Path, Path], ...]:
    if path_combinations is None:
        assert leaves_to_match is None
        leaves_to_match = [path for path, _ in orig.leaves()]

        path_combinations = [
            ((orig_path, orig_tree), (extended_path, extended_tree))
            for orig_path, orig_tree in orig.paths()
            for extended_path, extended_tree in extended.paths()
            if orig_tree.structural_hash() == extended_tree.structural_hash()
        ]

    if not path_combinations:
        return

    ((orig_path, orig_subtree), (extended_path, extended_subtree)), *remaining_combinations = path_combinations

    yield from embed_tree(orig, extended, leaves_to_match, remaining_combinations)

    remaining_leaves_to_match = tuple(
        path for path in leaves_to_match
        if not parent_reflexive(orig_path, path)
    )

    remaining_combinations = tuple(
        combination for combination in remaining_combinations
        if (
            other_orig_path := combination[0][0],
            other_extended_path := combination[1][0],
            not parent_or_child(orig_path, other_orig_path) and
            not parent_or_child(extended_path, other_extended_path),
        )[-1]
    )

    if not remaining_leaves_to_match:
        assert not remaining_combinations
        yield {extended_path: orig_path}
        return

    for assignment in embed_tree(orig, extended, remaining_leaves_to_match, remaining_combinations):
        yield assignment | {extended_path: orig_path}


def crop(mk_parser: Callable[[str], Callable[[str], List[ParseTree]]],
         tree: DerivationTree,
         width: Union[int, DerivationTree]) -> SemPredEvalResult:
    if not tree.is_complete():
        return SemPredEvalResult(None)

    unparsed = str(tree)

    if isinstance(width, Constant):
        return SemPredEvalResult({width: DerivationTree(str(len(unparsed)), None)})

    assert isinstance(width, DerivationTree)
    if not width.is_complete():
        return SemPredEvalResult(None)

    width = int(str(width))

    if len(unparsed) <= width:
        return SemPredEvalResult(True)

    parser = mk_parser(tree.value)
    result = DerivationTree.from_parse_tree(parser(unparsed[:width])[0]).get_subtree((0,))
    return SemPredEvalResult({tree: result})


def just(ljust: bool,
         crop: bool,
         mk_parser: Callable[[str], Callable[[str], List[ParseTree]]],
         tree: DerivationTree,
         width: Union[int, DerivationTree],
         fill_char: Optional[str] = None) -> SemPredEvalResult:
    if not tree.is_complete():
        return SemPredEvalResult(None)

    unparsed = str(tree)

    if isinstance(width, Constant):
        return SemPredEvalResult({width: DerivationTree(str(len(unparsed)), None)})

    if fill_char is None:
        assert len(unparsed) > 0
        assert unparsed == unparsed[0].ljust(len(unparsed), unparsed[0])
        fill_char = unparsed[0]

    if len(fill_char) != 1:
        raise TypeError("The fill character must be exactly one character long")

    assert isinstance(width, DerivationTree) or isinstance(width, int)
    if isinstance(width, DerivationTree):
        if not width.is_complete():
            return SemPredEvalResult(None)

        width = int(str(width))

    if len(unparsed) == width:
        return SemPredEvalResult(True)

    parser = mk_parser(tree.value)

    unparsed_output = unparsed.ljust(width, fill_char) if ljust else unparsed.rjust(width, fill_char)

    assert crop or len(unparsed_output) == width

    unparsed_output = unparsed_output[len(unparsed_output) - width:]
    result = DerivationTree.from_parse_tree(parser(unparsed_output)[0]).get_subtree((0,))

    return SemPredEvalResult({tree: result})


def mk_parser(grammar: Grammar):
    def Parser(start: str) -> Callable[[str], List[ParseTree]]:
        specialized_grammar = copy.deepcopy(grammar)
        specialized_grammar["<start>"] = [start]
        delete_unreachable(specialized_grammar)
        parser = EarleyParser(specialized_grammar)

        return lambda inp: list(parser.parse(inp))

    return Parser


CROP_PREDICATE = lambda grammar: SemanticPredicate(
    "crop", 2,
    lambda tree, width: crop(mk_parser(grammar), tree, width),
    binds_tree=False)

LJUST_PREDICATE = lambda grammar: SemanticPredicate(
    "ljust", 3,
    lambda tree, width, fillchar: just(True, False, mk_parser(grammar), tree, width, fillchar),
    binds_tree=False)

LJUST_CROP_PREDICATE = lambda grammar: SemanticPredicate(
    "ljust_crop", 3,
    lambda tree, width, fillchar: just(True, True, mk_parser(grammar), tree, width, fillchar),
    binds_tree=False)

EXTEND_CROP_PREDICATE = lambda grammar: SemanticPredicate(
    "extend_crop", 2,
    lambda tree, width: just(True, True, mk_parser(grammar), tree, width),
    binds_tree=False)

RJUST_PREDICATE = lambda grammar: SemanticPredicate(
    "rjust", 3, lambda tree, width, fillchar: just(False, False, mk_parser(grammar), tree, width, fillchar),
    binds_tree=False)

RJUST_CROP_PREDICATE = lambda grammar: SemanticPredicate(
    "rjust_crop", 3,
    lambda tree, width, fillchar: just(False, True, mk_parser(grammar), tree, width, fillchar),
    binds_tree=False)


def octal_to_dec(
        _octal_parser: Callable[[str], List[ParseTree]],
        _decimal_parser: Callable[[str], List[ParseTree]],
        octal: Union[isla.Constant, DerivationTree],
        decimal: Union[isla.Constant, DerivationTree]) -> SemPredEvalResult:
    assert not isinstance(octal, isla.Constant) or not isinstance(decimal, isla.Constant)

    decimal_parser = lambda inp: DerivationTree.from_parse_tree(_decimal_parser(inp)[0][1][0])
    octal_parser = lambda inp: DerivationTree.from_parse_tree(_octal_parser(inp)[0][1][0])

    if isinstance(octal, DerivationTree):
        if not octal.is_complete():
            return SemPredEvalResult(None)

        # Conversion to decimal
        octal_str = str(octal)

        decimal_number = 0
        for idx, digit in enumerate(reversed(octal_str)):
            decimal_number += (8 ** idx) * int(digit)

        if isinstance(decimal, DerivationTree) and decimal_number == int(str(decimal)):
            return SemPredEvalResult(True)

        return SemPredEvalResult({decimal: decimal_parser(str(decimal_number))})

    assert isinstance(octal, isla.Constant)
    assert isinstance(decimal, DerivationTree)

    if not decimal.is_complete():
        return SemPredEvalResult(None)

    decimal_number = int(str(decimal))
    octal_str = str(oct(decimal_number))[2:]

    if isinstance(octal, DerivationTree) and octal_str == str(octal):
        return SemPredEvalResult(True)

    return SemPredEvalResult({octal: octal_parser(octal_str)})


OCTAL_TO_DEC_PREDICATE = lambda grammar, octal_start, decimal_start: SemanticPredicate(
    "octal_to_decimal", 2,
    lambda octal, decimal: octal_to_dec(
        mk_parser(grammar)(octal_start),
        mk_parser(grammar)(decimal_start),
        octal, decimal),
    binds_tree=False
)

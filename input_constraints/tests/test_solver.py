import copy
import logging
import os
import unittest
from typing import cast, Optional, Dict, List, Callable, Union, Set
from xml.dom import minidom
from xml.sax.saxutils import escape

import z3
from fuzzingbook.GrammarCoverageFuzzer import GrammarCoverageFuzzer

from input_constraints import isla
from input_constraints import isla_shortcuts as sc
from input_constraints.concrete_syntax import ISLA_GRAMMAR
from input_constraints.helpers import delete_unreachable
from input_constraints.isla import VariablesCollector, parse_isla
from input_constraints.isla_predicates import BEFORE_PREDICATE, COUNT_PREDICATE
from input_constraints.solver import ISLaSolver, SolutionState, STD_COST_SETTINGS, CostSettings, CostWeightVector
from input_constraints.tests.subject_languages import rest, tinyc, tar, simple_tar, scriptsizec
from input_constraints.tests.subject_languages.tar import extract_tar
from input_constraints.tests.test_data import LANG_GRAMMAR, CSV_GRAMMAR, SIMPLE_CSV_GRAMMAR, XML_GRAMMAR


class TestSolver(unittest.TestCase):
    def test_qfd_formula_might_match(self):
        mgr = isla.VariableManager(LANG_GRAMMAR)
        solver = ISLaSolver(LANG_GRAMMAR, mgr.smt(mgr.const("$DUMMY", "<start>").to_smt() == z3.StringVal("")))

        tree = isla.DerivationTree.from_parse_tree(
            ('<start>', [
                ('<stmt>', [
                    ('<assgn>', [
                        ('<var>', None),  # Path (0, 0, 0)
                        (' := ', []), ('<rhs>', [('<var>', None)])]),
                    (' ; ', []),
                    ('<stmt>', [('<assgn>', [('<var>', None), (' := ', []), ('<rhs>', None)])])])]))

        formula = cast(isla.QuantifiedFormula, mgr.create(sc.forall_bind(
            isla.BindExpression(mgr.bv("$var1", "<var>")),
            mgr.bv("$rhs1", "<rhs>"),
            tree,
            mgr.smt(mgr.bv("$var1").to_smt() == z3.StringVal("x"))
        )))

        assert tree.get_subtree((0, 0, 2, 0)).value == "<var>"
        # assert not tree.get_subtree((0, 0, 2, 0)).children

        self.assertTrue(solver.quantified_formula_might_match(formula, (0, 0, 2, 0), tree))

    def test_atomic_smt_formula(self):
        assgn = isla.Constant("$assgn", "<assgn>")
        formula = isla.SMTFormula(cast(z3.BoolRef, assgn.to_smt() == z3.StringVal("x := x")), assgn)
        self.execute_generation_test(formula, num_solutions=1)

    def test_simple_universal_formula(self):
        start = isla.Constant("$start", "<start>")
        var1 = isla.BoundVariable("$var", "<var>")

        formula = sc.forall(
            var1, start,
            sc.smt_for(cast(z3.BoolRef, var1.to_smt() == z3.StringVal("x")), var1))

        self.execute_generation_test(formula, max_number_free_instantiations=1)

    def test_simple_universal_formula_with_bind(self):
        mgr = isla.VariableManager(LANG_GRAMMAR)
        formula = mgr.create(
            sc.forall_bind(
                isla.BindExpression(mgr.bv("$var1", "<var>")),
                mgr.bv("$rhs", "<rhs>"), mgr.const("$start", "<start>"),
                mgr.smt(cast(z3.BoolRef, mgr.bv("$var1").to_smt() == z3.StringVal("x"))))
        )

        self.execute_generation_test(formula)

    def test_simple_existential_formula(self):
        mgr = isla.VariableManager(LANG_GRAMMAR)
        start = isla.Constant("$start", "<start>")

        formula = mgr.create(
            sc.exists(
                mgr.bv("$var", "<var>"), start,
                mgr.smt(mgr.bv("$var").to_smt() == z3.StringVal("x")))
        )

        self.execute_generation_test(
            formula, num_solutions=1,
            max_number_free_instantiations=1,
            # expand_after_existential_elimination=True
        )

    def test_simple_existential_formula_with_bind(self):
        start = isla.Constant("$start", "<start>")
        rhs = isla.BoundVariable("$rhs", "<rhs>")
        var1 = isla.BoundVariable("$var", "<var>")

        formula = sc.exists_bind(
            isla.BindExpression(var1),
            rhs, start,
            sc.smt_for(cast(z3.BoolRef, var1.to_smt() == z3.StringVal("x")), var1))

        # TODO: Try to create infinite solution stream
        self.execute_generation_test(formula, num_solutions=1)

    def test_conjunction_of_qfd_formulas(self):
        start = isla.Constant("$start", "<start>")
        assgn = isla.BoundVariable("$assgn", "<assgn>")
        rhs_1 = isla.BoundVariable("$rhs_1", "<rhs>")
        rhs_2 = isla.BoundVariable("$rhs_2", "<rhs>")
        var_1 = isla.BoundVariable("$var1", "<var>")
        var_2 = isla.BoundVariable("$var2", "<var>")

        formula = \
            sc.forall_bind(
                isla.BindExpression(var_1),
                rhs_1, start,
                sc.smt_for(cast(z3.BoolRef, var_1.to_smt() == z3.StringVal("x")), var_1)) & \
            sc.forall_bind(
                var_2 + " := " + rhs_2,
                assgn, start,
                sc.smt_for(cast(z3.BoolRef, var_2.to_smt() == z3.StringVal("y")), var_2))

        self.execute_generation_test(formula)

    def test_xml(self):
        mgr = isla.VariableManager(XML_GRAMMAR)
        start = mgr.const("$start", "<start>")

        formula: isla.Formula = mgr.create(
            sc.forall_bind(
                sc.bexpr("<") + mgr.bv("$oid", "<id>") + ">" +
                "<inner-xml-tree>" +
                "</" + mgr.bv("$cid", "<id>") + ">",
                "<xml-tree>",
                start,
                mgr.smt(mgr.bv("$oid").to_smt() == mgr.bv("$cid").to_smt())
            ) &
            sc.forall_bind(
                sc.bexpr("<") + mgr.bv("$oid", "<id>") + " " + "<xml-attribute>" + ">" +
                "<inner-xml-tree>" +
                "</" + mgr.bv("$cid", "<id>") + ">",
                "<xml-tree>",
                start,
                mgr.smt(mgr.bv("$oid").to_smt() == mgr.bv("$cid").to_smt())
            )
        )

        self.execute_generation_test(
            formula,
            grammar=XML_GRAMMAR,
            max_number_free_instantiations=1,
            num_solutions=30,
            cost_settings=CostSettings(
                weight_vectors=(
                    CostWeightVector(
                        tree_closing_cost=14,
                        vacuous_penalty=15,
                        constraint_cost=18,
                        derivation_depth_penalty=1,
                        low_k_coverage_penalty=8)
                    ,),
                cost_phase_lengths=(200,))
        )

    def test_declared_before_used(self):
        mgr = isla.VariableManager(LANG_GRAMMAR)
        formula: isla.Formula = mgr.create(sc.forall_bind(
            mgr.bv("$lhs_1", "<var>") + " := " + mgr.bv("$rhs_1", "<rhs>"),
            mgr.bv("$assgn_1", "<assgn>"),
            mgr.const("$start", "<start>"),
            sc.forall(
                mgr.bv("$var", "<var>"),
                mgr.bv("$rhs_1"),
                sc.exists_bind(
                    mgr.bv("$lhs_2", "<var>") + " := " + mgr.bv("$rhs_2", "<rhs>"),
                    mgr.bv("$assgn_2", "<assgn>"),
                    mgr.const("$start"),
                    sc.before(mgr.bv("$assgn_2"), mgr.bv("$assgn_1")) &
                    mgr.smt(cast(z3.BoolRef, mgr.bv("$lhs_2").to_smt() == mgr.bv("$var").to_smt()))
                )
            )
        ))

        self.execute_generation_test(
            formula,
            max_number_free_instantiations=1,
            max_number_smt_instantiations=3,
            num_solutions=50)

    def test_declared_before_used_concrete_syntax(self):
        formula = """
const start: <start>;

vars {
    lhs_1, var, lhs_2: <var>;
    rhs_1, rhs_2: <rhs>;
    assgn_1, assgn_2: <assgn>;
}

constraint {
  forall assgn_1="{lhs_1} := {rhs_1}" in start:
    forall var in rhs_1:
      exists assgn_2="{lhs_2} := {rhs_2}" in start:
        (before(assgn_2, assgn_1) and (= lhs_2 var))
}
"""

        self.execute_generation_test(
            formula,
            structural_predicates={BEFORE_PREDICATE},
            max_number_free_instantiations=1,
            num_solutions=30)

    def test_simple_csv_rows_equal_length(self):
        mgr = isla.VariableManager(SIMPLE_CSV_GRAMMAR)
        formula = mgr.create(
            mgr.smt(cast(z3.BoolRef, z3.StrToInt(mgr.num_const("$num").to_smt()) >= z3.IntVal(3))) &
            mgr.smt(cast(z3.BoolRef, z3.StrToInt(mgr.num_const("$num").to_smt()) <= z3.IntVal(5))) &
            sc.forall(
                mgr.bv("$hline", "<csv-header>"),
                mgr.const("$start", "<start>"),
                sc.count(SIMPLE_CSV_GRAMMAR, mgr.bv("$hline"), "<csv-field>", mgr.num_const("$num"))) &
            sc.forall(
                mgr.bv("$line", "<csv-record>"),
                mgr.const("$start", "<start>"),
                sc.count(SIMPLE_CSV_GRAMMAR, mgr.bv("$line"), "<csv-field>", mgr.num_const("$num")))
        )

        self.execute_generation_test(
            formula,
            grammar=SIMPLE_CSV_GRAMMAR,
            max_number_free_instantiations=1,
            max_number_smt_instantiations=2,
            enforce_unique_trees_in_queue=False)

    def test_csv_rows_equal_length(self):
        property = """
const start: <start>;

vars {
  colno: NUM;
  hline: <csv-header>;
  line: <csv-record>;
}

constraint {
  forall hline in start:
    num colno:
      ((>= (str.to_int colno) 3) and 
      ((<= (str.to_int colno) 5) and 
       (count(hline, "<raw-string>", colno) and 
       forall line in start:
         count(line, "<raw-string>", colno))))
}
"""

        self.execute_generation_test(
            property,
            semantic_predicates={COUNT_PREDICATE(CSV_GRAMMAR)},
            grammar=CSV_GRAMMAR,
            num_solutions=30,
            max_number_free_instantiations=2,
            max_number_smt_instantiations=2,
            enforce_unique_trees_in_queue=False,
            cost_settings=CostSettings(
                (CostWeightVector(
                    tree_closing_cost=20,
                    vacuous_penalty=2,
                    constraint_cost=5,
                    derivation_depth_penalty=5,
                    low_k_coverage_penalty=10),),
                cost_phase_lengths=(200,)),
        )

    # Note: Disabling this for now since the z3 solver times out now that we solve
    # all SMT formulas en block (but doing so is the only sound way).
    def test_rest_titles(self):
        self.execute_generation_test(
            rest.LENGTH_UNDERLINE,
            grammar=rest.REST_GRAMMAR,
            max_number_free_instantiations=1,
            max_number_smt_instantiations=4,
            expand_after_existential_elimination=False,
            enforce_unique_trees_in_queue=False)

    def test_tiny_c_def_before_use(self):
        # TODO: The compile_tinyc_lang test function is not totally precise, as it initializes
        #       all assigned variables at the beginning to make the tinyc snippet c compatible.
        #       Actually, we would have to localize the initializations, which requires some
        #       more programming work; and then, the constraint would have to be refined (see
        #       commented test case below).

        self.execute_generation_test(
            tinyc.TINYC_DEF_BEFORE_USE_CONSTRAINT,
            grammar=tinyc.TINYC_GRAMMAR,
            max_number_free_instantiations=1,
            max_number_smt_instantiations=1,
            expand_after_existential_elimination=False,
            enforce_unique_trees_in_queue=True,
        )

    def test_scriptsize_c_def_before_use(self):
        self.execute_generation_test(
            scriptsizec.SCRIPTSIZE_C_DEF_USE_CONSTR,  # & scriptsizec.SCRIPTSIZE_C_NO_REDEF_CONSTR,
            grammar=scriptsizec.SCRIPTSIZE_C_GRAMMAR,
            max_number_free_instantiations=1,
            max_number_smt_instantiations=1,
            expand_after_existential_elimination=False,
            enforce_unique_trees_in_queue=True,
            cost_settings=CostSettings(
                (
                    # CostWeightVector(
                    #     tree_closing_cost=4,
                    #     vacuous_penalty=3,
                    #     constraint_cost=24,
                    #     derivation_depth_penalty=7,
                    #     low_coverage_penalty=12),
                    CostWeightVector(
                        tree_closing_cost=38,
                        vacuous_penalty=5,
                        constraint_cost=9,
                        derivation_depth_penalty=5,
                        low_k_coverage_penalty=13,
                        low_global_k_path_coverage_penalty=5
                    ),
                ),
                cost_phase_lengths=(200,),
                k=3
            ),
            print_only=True,
            num_solutions=400
        )

    def test_tar(self):
        # TODO: On CI, get error message
        # "AssertionError: tar: \r: Cannot create symlink to ‘’: No such file or directory",
        # so obviously link name is empty. That shouldn't happen... Check!
        self.execute_generation_test(
            tar.TAR_CONSTRAINTS,
            grammar=tar.TAR_GRAMMAR,
            max_number_free_instantiations=1,
            max_number_smt_instantiations=1,
            expand_after_existential_elimination=False,
            enforce_unique_trees_in_queue=False,
            # debug=True,
            num_solutions=10,
            precompute_reachability=False,
            custom_test_func=extract_tar,
            # cost_vectors=((20, 0, .5, 0),),
            # cost_phase_lengths=(100,),
        )

    def test_simple_tar(self):
        self.execute_generation_test(
            simple_tar.TAR_CONSTRAINTS,
            grammar=simple_tar.SIMPLE_TAR_GRAMMAR,
            max_number_free_instantiations=1,
            max_number_smt_instantiations=1,
            expand_after_existential_elimination=False,
            enforce_unique_trees_in_queue=False,
            debug=True,
            num_solutions=10,
            precompute_reachability=False,
            cost_settings=STD_COST_SETTINGS,
        )

    def Xtest_isla(self):
        # TODO
        # grammar = ISLA_GRAMMAR
        grammar = copy.deepcopy(ISLA_GRAMMAR)
        grammar["<smt_atom>"] = ["(= <id> <id>)"]
        # grammar["<predicate_atom>"] = ["before(<id>, <id>)"]
        delete_unreachable(grammar)

        fuzzer = GrammarCoverageFuzzer(grammar)
        logger = logging.getLogger(type(self).__name__)
        for _ in range(1000):
            inp = fuzzer.fuzz()
            logger.info(inp)
            try:
                parse_isla(inp)
            except SyntaxError as err:
                logger.warning(err.msg)

    def execute_generation_test(
            self,
            formula: Union[isla.Formula, str],
            structural_predicates: Optional[Set[isla.StructuralPredicate]] = None,
            semantic_predicates: Optional[Set[isla.SemanticPredicate]] = None,
            grammar=LANG_GRAMMAR,
            num_solutions=50,
            print_solutions=False,
            max_number_free_instantiations=1,
            max_number_smt_instantiations=1,
            expand_after_existential_elimination=False,
            enforce_unique_trees_in_queue=True,
            precompute_reachability=False,
            debug=False,
            state_tree_out="/tmp/state_tree.xml",
            log_out="/tmp/isla_log.txt",
            custom_test_func: Optional[Callable[[isla.DerivationTree], Union[bool, str]]] = None,
            cost_settings=STD_COST_SETTINGS,
            print_only: bool = False,
    ):
        logger = logging.getLogger(type(self).__name__)

        if debug:
            for f in [f for f in [state_tree_out, log_out] if os.path.exists(f)]:
                os.remove(f)

        solver = ISLaSolver(
            grammar=grammar,
            formula=formula,
            structural_predicates=structural_predicates,
            semantic_predicates=semantic_predicates,
            max_number_free_instantiations=max_number_free_instantiations,
            max_number_smt_instantiations=max_number_smt_instantiations,
            expand_after_existential_elimination=expand_after_existential_elimination,
            enforce_unique_trees_in_queue=enforce_unique_trees_in_queue,
            precompute_reachability=precompute_reachability,
            debug=debug,
            cost_settings=cost_settings,
        )

        if debug:
            file_handler = logging.FileHandler(log_out)
            for name in logging.root.manager.loggerDict:
                logging.getLogger(name).addHandler(file_handler)

        if isinstance(formula, str):
            formula = parse_isla(formula, structural_predicates, semantic_predicates)

        constant = next(
            c for c in VariablesCollector.collect(formula)
            if isinstance(c, isla.Constant) and not c.is_numeric())

        it = solver.solve()
        solutions_found = 0
        for idx in range(num_solutions):
            try:
                assignment = next(it)

                solutions_found += 1
                logger.info(f"Found solution no. %d: %s", solutions_found, assignment)

                if not print_only:
                    self.assertTrue(
                        isla.evaluate(formula.substitute_expressions({constant: assignment}), assignment),
                        f"Solution {assignment} does not satisfy constraint {formula}"
                    )

                    if custom_test_func:
                        test_result = custom_test_func(assignment)
                        if test_result is not True:
                            logger.info(f"Solution WRONG: %s", assignment)
                            self.fail("" if not isinstance(test_result, str) else test_result)

                if print_solutions:
                    print(str(assignment))
            except StopIteration:
                if idx == 0:
                    self.fail("No solution found.")
                self.fail(f"Only found {idx} solutions")

        if debug:
            with open(state_tree_out, 'w') as file:
                file.write(state_tree_to_xml(
                    solver.state_tree_root, solver.state_tree, solver.costs))
                print(f"Written derivation data (XML) to {state_tree_out}")
                print(f"Written log {log_out}")


def state_tree_to_xml(
        root: SolutionState,
        tree: Dict[SolutionState, List[SolutionState]],
        costs: Dict[SolutionState, float],
        prettify=True) -> str:
    if root not in tree:
        children_string = ""
    else:
        children_string = (
                "<children>" +
                "".join([state_tree_to_xml(child, tree, costs, False) for child in tree[root]]) +
                "</children>")

    special_char_map = {
        "\x00": "&lt;NUL&gt;",
        "\x0b": "&lt;VTAB&gt;",
        "\x0c": "&lt;FFEED&gt;",
    }

    result = ("<state>" +
              "<constraint>" + escape(str(root.constraint), special_char_map) + "</constraint>" +
              "<tree>" + escape(str(root.tree), special_char_map) + "</tree>" +
              "<cost>" + str(costs[root]) + "</cost>" +
              "<hash>" + str(hash(root)) + "</hash>" +
              children_string +
              "</state>")

    if prettify:
        return minidom.parseString(result).toprettyxml(indent="    ")
    else:
        return result


if __name__ == '__main__':
    unittest.main()

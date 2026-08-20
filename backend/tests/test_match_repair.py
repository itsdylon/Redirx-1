"""
Learning a site's rename conventions and repairing low-confidence matches.

Most of these pin down tuning that was arrived at by measurement against the
production corpus, not by taste. Each such case names the observation that set
it, because the values look arbitrary otherwise and the failure modes they
prevent are not obvious from the code.
"""
import os
import sys
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from redirx import match_repair as mrp


class TestSegments(unittest.TestCase):
    def test_absolute_url_reduces_to_path_segments(self):
        self.assertEqual(
            mrp.segments("https://old.com/a/b/c.html"), ("a", "b", "c")
        )

    def test_bare_path_works_too(self):
        self.assertEqual(mrp.segments("/a/b"), ("a", "b"))

    def test_extension_is_stripped_from_the_last_segment_only(self):
        self.assertEqual(mrp.segments("/a.html/b.php"), ("a.html", "b"))

    def test_root_has_no_segments(self):
        self.assertEqual(mrp.segments("https://old.com/"), ())

    def test_a_segment_that_is_only_an_extension_is_left_alone(self):
        # Stripping this would leave an empty segment and shift the whole path.
        self.assertEqual(mrp.segments("/.html"), (".html",))


class TestSlugEquivalence(unittest.TestCase):
    """
    The tolerance that decides what counts as "the same page, renamed".

    Both directions here were measured. Too strict and a re-platform looks
    convention-less; too loose and the convention gets absorbed into the tail
    and disappears.
    """

    def test_identical_slugs_match(self):
        self.assertTrue(mrp.slugs_equivalent("about", "about"))

    def test_long_slug_survives_punctuation_drift(self):
        # Measured on a real Shopify migration: byte equality discarded all
        # 274 of its confident matches and learned nothing.
        self.assertTrue(
            mrp.slugs_equivalent(
                "stovetop-krumkake-ironpizzelle-maker",
                "stovetop-krumkake-iron-pizzelle-maker",
            )
        )

    def test_short_section_names_must_match_exactly(self):
        # `product` and `products` are 93% similar. Treating them as one
        # segment absorbs `/product/* -> /products/*` into the shared tail,
        # which is precisely the rule we are trying to learn.
        self.assertFalse(mrp.slugs_equivalent("product", "products"))
        self.assertFalse(mrp.slugs_equivalent("blog", "blogs"))

    def test_long_but_genuinely_different_slugs_do_not_match(self):
        self.assertFalse(
            mrp.slugs_equivalent("stainless-steel-compost-pail", "aladdin-oil-lamp-shade")
        )

    def test_empty_never_matches(self):
        self.assertFalse(mrp.slugs_equivalent("", "about"))


class TestCommonSuffix(unittest.TestCase):
    def test_counts_shared_trailing_segments(self):
        self.assertEqual(
            mrp.common_suffix_length(("a", "x", "y"), ("b", "x", "y")), 2
        )

    def test_stops_at_the_first_difference(self):
        self.assertEqual(
            mrp.common_suffix_length(("x", "a", "y"), ("x", "b", "y")), 1
        )

    def test_no_shared_tail(self):
        self.assertEqual(mrp.common_suffix_length(("a",), ("b",)), 0)


class TestLearnRules(unittest.TestCase):
    @staticmethod
    def pairs(n, old_prefix, new_prefix):
        return [
            (f"/{old_prefix}/item-{i}", f"/{new_prefix}/item-{i}") for i in range(n)
        ]

    def test_a_repeated_rename_becomes_a_rule(self):
        rules = mrp.learn_rules(self.pairs(5, "case-studies", "success-stories"))
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].old_prefix, ("case-studies",))
        self.assertEqual(rules[0].new_prefix, ("success-stories",))
        self.assertEqual(rules[0].support, 5)
        self.assertEqual(rules[0].consistency, 1.0)

    def test_too_few_examples_is_a_coincidence_not_a_convention(self):
        self.assertEqual(mrp.learn_rules(self.pairs(2, "blog", "news")), [])

    def test_an_inconsistent_section_yields_no_rule(self):
        # A section that scattered has no destination to propose.
        scattered = (
            self.pairs(3, "stuff", "here")
            + [("/stuff/a", "/there/a"), ("/stuff/b", "/elsewhere/b"), ("/stuff/c", "/other/c")]
        )
        self.assertEqual(mrp.learn_rules(scattered), [])

    def test_a_dominant_destination_survives_a_stray(self):
        pairs = self.pairs(9, "blog", "news") + [("/blog/odd", "/random/odd")]
        rules = mrp.learn_rules(pairs)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].new_prefix, ("news",))
        self.assertEqual(rules[0].support, 9)
        self.assertAlmostEqual(rules[0].consistency, 0.9)

    def test_pairs_sharing_no_tail_teach_nothing(self):
        pairs = [(f"/a/old-{i}", f"/b/totally-different-{i}") for i in range(5)]
        self.assertEqual(mrp.learn_rules(pairs), [])

    def test_an_unchanged_prefix_is_not_a_rule(self):
        # It would only ever propose what the matcher already had.
        self.assertEqual(mrp.learn_rules(self.pairs(5, "docs", "docs")), [])

    def test_prefix_addition_is_learnable(self):
        rules = mrp.learn_rules(
            [(f"/team/{i}", f"/insights/team/{i}") for i in range(5)]
        )
        self.assertEqual(rules[0].old_prefix, ())
        self.assertEqual(rules[0].new_prefix, ("insights",))

    def test_a_shared_inner_segment_generalises_to_the_outer_rename(self):
        # /resources/kb/x -> /help/kb/x keeps `kb`, so the convention this
        # demonstrates is /resources/* -> /help/*, not a rule about `kb`.
        rules = mrp.learn_rules(self.pairs(5, "resources/kb", "help/kb"))
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].old_prefix, ("resources",))
        self.assertEqual(rules[0].new_prefix, ("help",))

    def test_more_specific_rules_come_first(self):
        # A subsection that moved somewhere its parent did not.
        nested = [(f"/resources/kb/a-{i}", f"/help/knowledge-base/a-{i}") for i in range(5)]
        outer = [(f"/resources/guide-{i}", f"/help/guide-{i}") for i in range(5)]
        rules = mrp.learn_rules(nested + outer)

        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].old_prefix, ("resources", "kb"))
        self.assertEqual(rules[1].old_prefix, ("resources",))

    def test_the_most_specific_rule_wins_when_both_apply(self):
        nested = [(f"/resources/kb/a-{i}", f"/help/knowledge-base/a-{i}") for i in range(5)]
        outer = [(f"/resources/guide-{i}", f"/help/guide-{i}") for i in range(5)]
        rules = mrp.learn_rules(nested + outer)

        index = mrp.index_new_urls(
            [
                "https://new.com/help/knowledge-base/printing",
                "https://new.com/help/kb/printing",
            ]
        )
        repairs = mrp.repair_matches(
            [("https://old.com/resources/kb/printing", "https://new.com/")],
            rules,
            index,
        )
        self.assertEqual(len(repairs), 1)
        self.assertEqual(
            repairs[0].repaired_url, "https://new.com/help/knowledge-base/printing"
        )

    def test_the_pluralisation_rename_is_learnable(self):
        """
        Regression from the production corpus.

        A store moved /product/<slug> to /products/<slug> and rewrote slug
        punctuation on the way. Byte-equal suffix comparison saw zero shared
        tail on all 274 confident matches and learned nothing; over-tolerant
        comparison folded `product`/`products` into the tail and also learned
        nothing. Both failures produced the same symptom: no rules at all.
        """
        pairs = [
            ("/product/stovetop-krumkake-ironpizzelle-maker",
             "/products/stovetop-krumkake-iron-pizzelle-maker"),
            ("/product/clear-oil-lamp-chimney-2-78-base",
             "/products/clear-oil-lamp-chimney-2-7-8-base"),
            ("/product/stainless-steel-compost-pail-large",
             "/products/stainless-steel-compost-pail-large"),
            ("/product/cast-iron-dutch-oven-6-quart",
             "/products/cast-iron-dutch-oven-6-quart"),
        ]
        rules = mrp.learn_rules(pairs)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].old_prefix, ("product",))
        self.assertEqual(rules[0].new_prefix, ("products",))


class TestRuleApplication(unittest.TestCase):
    RULE = mrp.Rule(("case-studies",), ("success-stories",), support=10, consistency=1.0)

    def test_rewrites_a_matching_prefix(self):
        self.assertEqual(
            self.RULE.apply("/case-studies/acme"), ("success-stories", "acme")
        )

    def test_ignores_a_path_outside_the_prefix(self):
        self.assertIsNone(self.RULE.apply("/blog/acme"))

    def test_refuses_a_path_with_nothing_left_to_keep(self):
        # Otherwise every URL under the prefix collapses onto one destination —
        # the catch-all-to-homepage failure this exists to catch.
        self.assertIsNone(self.RULE.apply("/case-studies"))

    def test_confidence_rises_with_evidence(self):
        weak = mrp.Rule((), ("x",), support=3, consistency=1.0)
        strong = mrp.Rule((), ("x",), support=50, consistency=1.0)
        self.assertLess(weak.confidence, strong.confidence)
        self.assertLess(strong.confidence, 1.0)

    def test_description_is_readable(self):
        self.assertEqual(self.RULE.describe(), "/case-studies/* → /success-stories/*")


class TestIndex(unittest.TestCase):
    def test_lookup_is_extension_insensitive(self):
        index = mrp.index_new_urls(["https://new.com/a/b.html"])
        self.assertEqual(index[("a", "b")], "https://new.com/a/b.html")

    def test_a_canonical_url_is_not_displaced_by_a_later_equivalent(self):
        index = mrp.index_new_urls(["https://new.com/a", "https://new.com/a.html"])
        self.assertEqual(index[("a",)], "https://new.com/a")


class TestRepair(unittest.TestCase):
    def setUp(self):
        self.rules = mrp.learn_rules(
            [(f"/case-studies/c-{i}", f"/success-stories/c-{i}") for i in range(6)]
        )

    def test_exact_repair_when_the_rules_target_is_published(self):
        index = mrp.index_new_urls(["https://new.com/success-stories/acme"])
        repairs = mrp.repair_matches(
            [("https://old.com/case-studies/acme", "https://new.com/")],
            self.rules,
            index,
        )
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0].how, "exact")
        self.assertEqual(repairs[0].repaired_url, "https://new.com/success-stories/acme")

    def test_nothing_proposed_when_the_target_is_unpublished(self):
        # The guard that makes a wrong rule harmless: it finds nothing rather
        # than authoring a URL nobody published.
        repairs = mrp.repair_matches(
            [("https://old.com/case-studies/acme", "https://new.com/")],
            self.rules,
            mrp.index_new_urls(["https://new.com/unrelated"]),
            allow_section_search=False,
        )
        self.assertEqual(repairs, [])

    def test_a_row_already_pointing_at_the_rules_target_is_not_repaired(self):
        index = mrp.index_new_urls(["https://new.com/success-stories/acme"])
        repairs = mrp.repair_matches(
            [(
                "https://old.com/case-studies/acme",
                "https://new.com/success-stories/acme.html",
            )],
            self.rules,
            index,
        )
        self.assertEqual(repairs, [])

    def test_no_applicable_rule_proposes_nothing(self):
        index = mrp.index_new_urls(["https://new.com/success-stories/acme"])
        self.assertEqual(
            mrp.repair_matches(
                [("https://old.com/pricing", "https://new.com/")], self.rules, index
            ),
            [],
        )

    def test_section_search_finds_a_reworded_leaf(self):
        # The measured near-miss: the section moved and the leaf was reworded.
        index = mrp.index_new_urls(
            ["https://new.com/success-stories/acme-corporation"]
            + [f"https://new.com/success-stories/unrelated-{i}" for i in range(5)]
        )
        repairs = mrp.repair_matches(
            [("https://old.com/case-studies/acme-corporation-profile", "https://new.com/")],
            self.rules,
            index,
        )
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0].how, "section")
        self.assertEqual(
            repairs[0].repaired_url, "https://new.com/success-stories/acme-corporation"
        )

    def test_section_search_refuses_a_coin_flip(self):
        # Several equally-close candidates is not a finding.
        index = mrp.index_new_urls(
            [f"https://new.com/success-stories/acme-corporation-{i}" for i in range(4)]
        )
        repairs = mrp.repair_matches(
            [("https://old.com/case-studies/acme-corporation", "https://new.com/")],
            self.rules,
            index,
        )
        self.assertEqual(repairs, [])

    def test_a_section_repair_is_less_confident_than_an_exact_one(self):
        rule = mrp.Rule(("a",), ("b",), support=50, consistency=1.0)
        exact = mrp.Repair("o", None, "u", rule, how="exact")
        section = mrp.Repair("o", None, "u", rule, how="section", similarity=0.95)
        self.assertLess(section.confidence, exact.confidence)

    def test_evidence_reads_as_a_sentence(self):
        rule = mrp.Rule(("case-studies",), ("success-stories",), support=41, consistency=1.0)
        evidence = mrp.Repair("o", None, "u", rule, how="exact").evidence
        self.assertIn("/case-studies/* → /success-stories/*", evidence)
        self.assertIn("41", evidence)


class TestScoring(unittest.TestCase):
    """
    Why the scorer is token_set_ratio and not the matcher's token_sort_ratio.
    """

    def test_a_destination_that_adds_qualifiers_still_scores_high(self):
        # Migrations overwhelmingly add words: a brand, a size, a pack count.
        self.assertGreaterEqual(
            mrp._score_tails(("bean-tower",), ("bean-tower-trellis-usa-made",)), 0.95
        )

    def test_a_coincidental_letter_overlap_scores_low(self):
        self.assertLess(mrp._score_tails(("bean-tower",), ("oven-towel",)), 0.90)

    def test_the_right_answer_beats_the_coincidence(self):
        """
        Regression. Under token_sort_ratio `bean-tower` scored 0.54 against its
        real destination and 0.70 against the unrelated `oven-towel`, so the
        repair actively replaced a correct match with a wrong one.
        """
        correct = mrp._score_tails(("bean-tower",), ("bean-tower-trellis-usa-made",))
        coincidence = mrp._score_tails(("bean-tower",), ("oven-towel",))
        self.assertGreater(correct, coincidence)

    def test_slug_separators_are_word_boundaries(self):
        self.assertEqual(mrp.tail_text(("a-b", "c_d")), "a b c d")


if __name__ == "__main__":
    unittest.main()

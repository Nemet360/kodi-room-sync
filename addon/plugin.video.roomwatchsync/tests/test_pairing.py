"""The pairing code is the household's only secret. These tests are about the
ways that could quietly fail: a code too small to resist guessing, a code a
person cannot type back, or a derivation that leaks the key to the store.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "resources", "lib"))

import pairing  # noqa: E402


class TestAlphabet(unittest.TestCase):
    def test_the_confusable_letters_are_absent(self):
        """Crockford drops I, L, O (look-alikes) and U (obscenity). Somebody
        reads this off one television and types it into another."""
        for char in "ILOU":
            self.assertNotIn(char, pairing.ALPHABET)

    def test_thirty_two_symbols_so_each_character_is_five_bits(self):
        self.assertEqual(len(pairing.ALPHABET), 32)
        self.assertEqual(len(set(pairing.ALPHABET)), 32)


class TestGenerate(unittest.TestCase):
    def test_a_code_carries_at_least_sixty_four_bits(self):
        """OWASP's floor for a bearer secret is 64 bits against guessing. A
        six-character code would be 30 — the Zoom meeting-id failure."""
        bits = pairing.CODE_CHARS * 5
        self.assertGreaterEqual(bits, 64)

    def test_the_printed_form_is_grouped_but_the_canonical_one_is_not(self):
        code = pairing.generate_code()
        self.assertRegex(code, r"^[0-9A-Z]{4}(-[0-9A-Z]{4})+$")
        self.assertEqual(len(pairing.normalize(code)), pairing.CODE_CHARS)

    def test_two_codes_are_never_the_same(self):
        codes = {pairing.generate_code() for _ in range(200)}
        self.assertEqual(len(codes), 200)

    def test_every_generated_code_is_accepted_by_the_parser(self):
        """A generator and a parser that disagree is a home nobody can join."""
        for _ in range(200):
            self.assertTrue(pairing.is_valid(pairing.generate_code()))

    def test_generated_codes_use_the_whole_alphabet(self):
        seen = set(re.sub(r"-", "", "".join(pairing.generate_code() for _ in range(400))))
        self.assertGreaterEqual(len(seen), 30)


class TestNormalize(unittest.TestCase):
    def setUp(self):
        self.canonical = pairing.normalize(pairing.generate_code())

    def test_lowercase_is_accepted(self):
        self.assertEqual(pairing.normalize(self.canonical.lower()), self.canonical)

    def test_dashes_and_spaces_are_accepted(self):
        spaced = " ".join(self.canonical[i:i + 4] for i in range(0, 16, 4))
        self.assertEqual(pairing.normalize(spaced), self.canonical)
        self.assertEqual(pairing.normalize(pairing.format_code(self.canonical)),
                         self.canonical)

    def test_a_typed_letter_o_means_zero_and_l_means_one(self):
        """The person is holding a remote. A correct code that is refused is
        indistinguishable from a wrong one."""
        self.assertEqual(pairing.normalize("O" * 16), "0" * 16)
        self.assertEqual(pairing.normalize("l" * 16), "1" * 16)
        self.assertEqual(pairing.normalize("I" * 16), "1" * 16)

    def test_a_typed_u_is_refused_rather_than_guessed(self):
        """U is excluded from the alphabet, so it has no single obvious intent —
        guessing would silently join a different home than the one printed."""
        with self.assertRaises(pairing.InvalidCode):
            pairing.normalize("U" * 16)

    def test_the_wrong_length_names_the_length(self):
        with self.assertRaises(pairing.InvalidCode) as caught:
            pairing.normalize("ABC")
        self.assertIn("16", str(caught.exception))
        self.assertIn("3", str(caught.exception))

    def test_an_empty_code_says_so(self):
        for value in ("", "   ", "---", None):
            with self.assertRaises(pairing.InvalidCode) as caught:
                pairing.normalize(value)
            self.assertIn("no code", str(caught.exception))

    def test_every_rejection_carries_a_reason(self):
        """'Invalid code' alone leaves a person retyping the same thing."""
        for value in ("", "ABC", "U" * 16, "!!!!!!!!!!!!!!!!"):
            with self.assertRaises(pairing.InvalidCode) as caught:
                pairing.normalize(value)
            self.assertGreater(len(str(caught.exception)), 10)


class TestDerivation(unittest.TestCase):
    def setUp(self):
        self.code = pairing.generate_code()

    def test_the_topic_is_not_the_code(self):
        """The topic is the only part that travels. If it were the code itself,
        the service operator would hold the key to everything else."""
        topic, secret, auth = pairing.derive(self.code)
        canonical = pairing.normalize(self.code)
        self.assertNotIn(canonical, topic)
        self.assertNotIn(canonical.encode(), secret)
        self.assertNotIn(canonical.encode(), auth)

    def test_the_three_derived_values_are_independent(self):
        """Learning the topic — from a screenshot, a support request — must not
        hand anyone the signing key or the item secret."""
        topic, secret, auth = pairing.derive(self.code)
        self.assertNotEqual(secret, auth)
        self.assertNotIn(secret.hex()[:16], topic.lower())
        self.assertNotIn(auth.hex()[:16], topic.lower())
        self.assertNotIn(topic.lower()[:16], secret.hex())

    def test_derivation_is_deterministic_across_typed_variants(self):
        """Two televisions must land on the same topic even when one person
        typed dashes and lowercase and the other did not."""
        canonical = pairing.normalize(self.code)
        typed = pairing.format_code(canonical).lower().replace("0", "O")
        self.assertEqual(pairing.derive(canonical), pairing.derive(typed))

    def test_different_codes_give_different_topics_and_keys(self):
        topics, secrets, auths = set(), set(), set()
        for _ in range(100):
            topic, secret, auth = pairing.derive(pairing.generate_code())
            topics.add(topic)
            secrets.add(secret)
            auths.add(auth)
        self.assertEqual(len(topics), 100)
        self.assertEqual(len(secrets), 100)
        self.assertEqual(len(auths), 100)

    def test_the_keys_are_full_length(self):
        self.assertEqual(len(pairing.item_secret(self.code)), 32)
        self.assertEqual(len(pairing.auth_key(self.code)), 32)

    def test_the_topic_space_is_large_enough_that_homes_never_collide(self):
        topic = pairing.topic(self.code)
        self.assertEqual(len(topic), pairing.TOPIC_CHARS)
        self.assertGreaterEqual(pairing.TOPIC_CHARS * 5, 128)
        self.assertTrue(all(c in pairing.ALPHABET for c in topic))

    def test_the_topic_is_safe_in_a_url_path(self):
        """It becomes https://ntfy.sh/<topic>; Crockford Base32 has no character
        that needs escaping, so nothing is silently rewritten in transit."""
        topic = pairing.topic(self.code)
        self.assertRegex(topic, r"^[0-9A-Z]+$")

    def test_hkdf_matches_rfc_5869_structure(self):
        """Two labels, one secret, independent outputs — the Firefox Send shape."""
        secret = pairing._secret(self.code)
        a = pairing._hkdf(secret, b"label-a", 32)
        b = pairing._hkdf(secret, b"label-b", 32)
        self.assertNotEqual(a, b)
        self.assertEqual(a, pairing._hkdf(secret, b"label-a", 32))

    def test_hkdf_expands_past_one_hash_block(self):
        long = pairing._hkdf(b"secret", b"info", 100)
        self.assertEqual(len(long), 100)
        self.assertEqual(long[:32], pairing._hkdf(b"secret", b"info", 32))

    def test_an_invalid_code_cannot_be_derived_from(self):
        for value in ("", "ABC", "U" * 16):
            with self.assertRaises(pairing.InvalidCode):
                pairing.derive(value)


if __name__ == "__main__":
    unittest.main()

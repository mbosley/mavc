"""Model steps refuse prompts over their context budget unless a larger budget is declared with a reason."""
import unittest

from mavc.declare import Settings
from mavc.model import DEFAULT_PROMPT_BUDGET_CHARS, check_prompt_budget, prompt_chars


class PromptBudgetTests(unittest.TestCase):
    def test_default_budget_refuses_oversized_prompt(self):
        check_prompt_budget(DEFAULT_PROMPT_BUDGET_CHARS, {})
        with self.assertRaisesRegex(ValueError, 'compact index'):
            check_prompt_budget(DEFAULT_PROMPT_BUDGET_CHARS + 1, {})

    def test_larger_budget_needs_a_reason(self):
        with self.assertRaisesRegex(ValueError, 'prompt_budget_reason'):
            check_prompt_budget(10, {'prompt_budget_chars': 400_000})
        check_prompt_budget(300_000, {'prompt_budget_chars': 400_000,
                                      'prompt_budget_reason': 'whole short document is the unit'})

    def test_size_counts_system_prompt_and_prompt_and_settings_accept_the_fields(self):
        self.assertEqual(prompt_chars({'system_prompt': 'abc', 'prompt': 'de'}), 5)
        self.assertEqual(prompt_chars({'system_prompt': 'abc', 'prompt': 'de', 'system_prompt_copies': 2}), 8)
        settings = Settings(prompt_budget_chars=200_000, prompt_budget_reason='reviewed').model_dump(exclude_unset=True)
        self.assertEqual(settings['prompt_budget_chars'], 200_000)


if __name__ == '__main__':
    unittest.main()

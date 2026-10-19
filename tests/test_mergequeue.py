import unittest

from mergequeue import config


class SafetyTests(unittest.TestCase):
    def test_allowed_refs(self):
        for ok in ("refs/heads/performance-optimizations", "refs/heads/mq/12"):
            self.assertEqual(config.assert_pushable(ok), ok)

    def test_refused_refs(self):
        for bad in ("refs/heads/main", "main", "refs/heads/mq/x", "refs/heads/mq/1/2", "refs/tags/mq/1",
                    "refs/heads/performance-optimizations2", "refs/heads/other", "+refs/heads/mq/1", ""):
            with self.assertRaises(config.UnsafeRef):
                config.assert_pushable(bad)


if __name__ == "__main__":
    unittest.main()

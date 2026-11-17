"""Repository-level rules: English docs, Conventional Commits, no secrets.

These tests encode the project conventions documented in AGENTS.md, so a
translation pass or a careless commit cannot silently violate them.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_checker():
    path = ROOT / "scripts" / "check_commit_message.py"
    spec = importlib.util.spec_from_file_location("check_commit_message", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = load_checker()

DOC_FILES = [
    "README.md",
    "AGENTS.md",
    "docs/DEPLOY.md",
    ".env.example",
    "Dockerfile",
    "docker-compose.yml",
    "deploy/appbot-telegram@.service",
    "deploy/appbot-telegram.container",
    "deploy/ecosystem.config.js",
    "deploy/docker-compose.localapi.yml",
    "run.sh",
    "requirements-dev.txt",
]


class TestDocumentationLanguage(unittest.TestCase):
    @staticmethod
    def strip_code_fences(text: str) -> str:
        """Prose must be English; fenced blocks may quote real command output."""
        return re.sub(r"```.*?```", "", text, flags=re.DOTALL)

    def test_documented_files_exist(self):
        for name in DOC_FILES:
            self.assertTrue((ROOT / name).is_file(), f"{name} is missing")

    def test_docs_are_in_english(self):
        failures = []
        for name in DOC_FILES:
            text = (ROOT / name).read_text(encoding="utf-8")
            found = CHECKER.indonesian_words(self.strip_code_fences(text))
            if len(found) >= 3:
                failures.append(f"{name}: {', '.join(sorted(found))}")
        self.assertEqual(failures, [], "Indonesian prose found in docs/code comments:\n" + "\n".join(failures))

    def test_source_comments_are_in_english(self):
        failures = []
        for path in sorted((ROOT / "appbot").rglob("*.py")) + [ROOT / "bot.py", ROOT / "config.py"]:
            text = path.read_text(encoding="utf-8")
            comments = "\n".join(
                line for line in text.splitlines()
                if line.lstrip().startswith("#")
            )
            found = CHECKER.indonesian_words(comments)
            if len(found) >= 2:
                failures.append(f"{path.relative_to(ROOT)}: {', '.join(sorted(found))}")
        self.assertEqual(failures, [], "Indonesian comments found:\n" + "\n".join(failures))


class TestUserFacingIndonesian(unittest.TestCase):
    """The bot answers in Indonesian; translations must not touch those strings."""

    EXPECTED = {
        "appbot/handlers.py": [
            "Unduhan dibatalkan",
            "Pembersihan selesai",
            "Tidak ada yang perlu dibersihkan",
            "hanya untuk admin",
            "menunggu antrean",
        ],
        "appbot/telegram_io.py": [
            "melebihi batas upload",
            "Gagal mengirim",
        ],
        "appbot/downloaders/base.py": [
            "YouTube meminta verifikasi",
            "Konten privat",
        ],
        "appbot/downloaders/tiktok.py": [
            "Diunduh tanpa watermark",
        ],
        "appbot/bot_profile.py": [
            "Unduh video/audio YouTube",
            "Batalkan unduhan",
        ],
        "appbot/system_info.py": [
            "Informasi Sistem",
            "Memori",
        ],
    }

    def test_user_facing_strings_still_indonesian(self):
        for name, phrases in self.EXPECTED.items():
            text = (ROOT / name).read_text(encoding="utf-8")
            for phrase in phrases:
                self.assertIn(
                    phrase.lower(), text.lower(),
                    f"{name} is missing the Indonesian text: {phrase!r}",
                )


class TestCommitMessageRules(unittest.TestCase):
    def assert_valid(self, message: str):
        self.assertEqual(CHECKER.check(message), [], f"should be valid: {message!r}")

    def assert_invalid(self, message: str, needle: str = ""):
        problems = CHECKER.check(message)
        self.assertTrue(problems, f"should be rejected: {message!r}")
        if needle:
            self.assertTrue(
                any(needle in p for p in problems),
                f"expected {needle!r} in {problems}",
            )

    def test_accepts_conventional_commits(self):
        self.assert_valid("feat: add cleanup command")
        self.assert_valid("fix(tiktok): prefer play_addr over watermarked source")
        self.assert_valid("docs(deploy): document podman quadlet")
        self.assert_valid("chore(deps)!: drop python 3.10")
        self.assert_valid(
            "feat(cleanup): cancel running downloads\n\n"
            "Each download is registered as a task so it can be cancelled.\n"
            "Refs: #12"
        )

    def test_rejects_non_conventional_headers(self):
        self.assert_invalid("update readme")
        self.assert_invalid("feat add something")
        self.assert_invalid("Feat: add something")
        self.assert_invalid("feature: add something")

    def test_rejects_bad_subjects(self):
        self.assert_invalid("feat: Add capitalized subject", "lowercase")
        self.assert_invalid("feat: add trailing period.", "period")
        self.assert_invalid("feat: update", "vague")
        self.assert_invalid("feat: " + "x" * 80, "limit is 72")

    def test_rejects_indonesian_messages(self):
        self.assert_invalid("feat: tambah perintah cleanup", "English")
        self.assert_invalid("fix: perbaiki unduhan yang gagal", "English")
        self.assert_invalid("docs: perbarui dokumentasi", "English")

    def test_requires_blank_line_before_body(self):
        self.assert_invalid("feat: add thing\nbody without blank line", "second line")

    def test_ignores_comment_lines_from_git(self):
        message = "feat: add thing\n\n# Please enter the commit message\n# On branch main\n"
        self.assertEqual(CHECKER.check(message), [])


class TestHooks(unittest.TestCase):
    def test_commit_msg_hook_is_executable(self):
        hook = ROOT / ".githooks" / "commit-msg"
        self.assertTrue(hook.is_file())
        self.assertTrue(os.access(hook, os.X_OK), "commit-msg hook must be executable")
        self.assertIn("check_commit_message.py", hook.read_text(encoding="utf-8"))

    def test_hook_wiring_documented(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("git config core.hooksPath .githooks", text)
        self.assertIn("Conventional Commits", text)
        self.assertIn("English", text)


class TestDeveloperFacingEnglish(unittest.TestCase):
    """CLI output, logs and assertion messages are English; only the chat replies are Indonesian."""

    DEV_FILES = [
        "bot.py",
        "config.py",
        "appbot/health.py",
        "appbot/jobs.py",
        "appbot/logging_setup.py",
        "scripts/livetest.py",
        # scripts/check_commit_message.py is intentionally excluded: it holds the
        # Indonesian vocabulary list used to detect non-English messages.
    ]

    MSG_INDEX = {
        "assertTrue": 1, "assertFalse": 1, "assertIsNone": 1, "assertIsNotNone": 1,
        "assertIs": 2, "assertIsNot": 2, "assertIn": 2, "assertNotIn": 2,
        "assertEqual": 2, "assertNotEqual": 2, "assertGreater": 2,
        "assertGreaterEqual": 2, "assertLess": 2, "assertLessEqual": 2,
        "assertRegex": 2, "assertNotRegex": 2,
        "assertIsInstance": 2, "assertNotIsInstance": 2,
    }

    @staticmethod
    def _text_of(node) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                part.value for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
        return None

    def test_cli_and_log_strings_are_english(self):
        failures = []
        for name in self.DEV_FILES:
            tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                text = self._text_of(node)
                if not text:
                    continue
                found = CHECKER.indonesian_words(text)
                if len(found) >= 2:
                    failures.append(f"{name}: {sorted(found)} :: {text[:60]}")
        self.assertEqual(failures, [], "Indonesian developer-facing text:\n" + "\n".join(failures))

    def test_assertion_messages_are_english(self):
        failures = []
        for path in sorted((ROOT / "tests").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                attr = getattr(func, "attr", None)
                if attr not in self.MSG_INDEX:
                    continue
                candidates = list(node.args[self.MSG_INDEX[attr]:])
                candidates += [kw.value for kw in node.keywords if kw.arg == "msg"]
                for candidate in candidates:
                    text = self._text_of(candidate)
                    if not text:
                        continue
                    found = CHECKER.indonesian_words(text)
                    if len(found) >= 2:
                        failures.append(
                            f"{path.name}:{node.lineno} {sorted(found)} :: {text[:60]}"
                        )
        self.assertEqual(failures, [], "Indonesian assertion messages:\n" + "\n".join(failures))


class TestNoSecrets(unittest.TestCase):
    PATTERNS = (
        re.compile(r"AAF[A-Za-z0-9_-]{20,}"),          # telegram bot token body
        re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),     # github tokens
        re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),  # bot token id:secret
    )

    def test_no_token_patterns_in_tracked_files(self):
        offenders = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix in {".pyc", ".mp4", ".jpg", ".png"}:
                continue
            parts = set(path.parts)
            if parts & {".venv", "tmp", "downloads", ".git", "__pycache__"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for pattern in self.PATTERNS:
                if pattern.search(text):
                    offenders.append(str(path.relative_to(ROOT)))
                    break
        self.assertEqual(offenders, [], f"possible secrets in: {offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
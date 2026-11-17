"""Validate a commit message: Conventional Commits + English.

Used by the `.githooks/commit-msg` hook and runnable manually:

    python3 scripts/check_commit_message.py --message "feat: add cleanup command"
    python3 scripts/check_commit_message.py .git/COMMIT_EDITMSG
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TYPES = (
    "feat", "fix", "docs", "style", "refactor", "perf",
    "test", "build", "ci", "chore", "revert",
)

HEADER_RE = re.compile(
    r"^(?P<type>" + "|".join(TYPES) + r")"
    r"(?:\((?P<scope>[a-z0-9][a-z0-9._/-]*)\))?"
    r"(?P<breaking>!)?: "
    r"(?P<subject>\S.*)$"
)

MAX_HEADER = 72
MAX_BODY_LINE = 100

# Distinctly Indonesian words. English prose never contains them, so two or more
# hits mean the message is (at least partly) Indonesian.
INDONESIAN_WORDS = frozenset(
    """
    dan yang untuk dengan tidak akan dari ini itu pada atau sudah belum harus bisa juga
    karena agar saat setelah sebelum dalam adalah oleh kita kamu saya lebih semua ketika
    tanpa masih hanya tambah hapus ubah perbarui perbaiki jalankan pasang unduh unduhan
    berkas perintah pengguna pesan berjalan gagal selesai dipakai memakai pakai cara
    contoh misalnya seperti tersebut apabila jika maka sebagai antara beberapa banyak
    sedang telah punya buat dibuat membuat ke dari sehingga namun tetapi serta bagi
    terhadap melalui secara sesuai tanpa hingga sampai selama dokumentasi dependensi
    konfigurasi keterangan perubahan penambahan kesalahan pengaturan penggunaan
    """.split()
)


def _strip_comments(text: str) -> str:
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def indonesian_words(text: str) -> set[str]:
    words = set(re.findall(r"[a-z]+", text.lower()))
    return words & INDONESIAN_WORDS


def check(message: str) -> list[str]:
    """Return a list of human-readable problems; empty means the message is valid."""
    text = _strip_comments(message).strip()
    if not text:
        return ["commit message is empty"]

    lines = text.splitlines()
    header = lines[0].strip()
    problems: list[str] = []

    match = HEADER_RE.match(header)
    if not match:
        problems.append(
            "header must look like '<type>(<scope>): <subject>' with type in "
            + ", ".join(TYPES)
        )
    else:
        subject = match.group("subject")
        if len(header) > MAX_HEADER:
            problems.append(f"header is {len(header)} chars, limit is {MAX_HEADER}")
        if subject[0].isupper():
            problems.append("subject should start with a lowercase letter")
        if subject.rstrip().endswith("."):
            problems.append("subject must not end with a period")
        if subject.strip() in {"", "update", "changes", "fix", "wip"}:
            problems.append("subject is too vague; describe the change")

    if len(lines) > 1 and lines[1].strip():
        problems.append("second line must be empty (separate header from body)")

    for number, line in enumerate(lines[1:], start=2):
        if len(line) > MAX_BODY_LINE:
            problems.append(f"line {number} is {len(line)} chars, limit is {MAX_BODY_LINE}")

    found = indonesian_words(text)
    if len(found) >= 2:
        problems.append(
            "commit message must be in English; Indonesian words found: "
            + ", ".join(sorted(found))
        )

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a commit message")
    parser.add_argument("path", nargs="?", help="file containing the commit message")
    parser.add_argument("--message", "-m", help="check this string instead of a file")
    args = parser.parse_args(argv)

    if args.message is not None:
        message = args.message
    elif args.path:
        try:
            message = Path(args.path).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"cannot read {args.path}: {exc}", file=sys.stderr)
            return 2
    else:
        message = sys.stdin.read()

    problems = check(message)
    if not problems:
        return 0

    print("Invalid commit message:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print("\nSee AGENTS.md for the commit conventions.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
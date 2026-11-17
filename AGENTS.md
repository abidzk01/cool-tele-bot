# AGENTS.md

Instructions for AI agents and human contributors working on this repository.

## Language policy

| Artifact | Language |
|---|---|
| Code, identifiers, comments, docstrings | **English** |
| Commit messages | **English** |
| `README.md`, files in `docs/`, `AGENTS.md`, `CONTRIBUTING`-style docs | **English** |
| `.env.example`, Dockerfile, compose files, unit files, shell scripts | **English** |
| Tests (names, comments, assertions messages) | **English** |
| User-facing Telegram strings (replies, help text, error messages) | Indonesian (product language) |

Reason: the repository is public and reviewed by an international audience, while the bot
itself targets Indonesian users. If a second language is ever needed for the bot, add
localization instead of mixing languages in the code.

Do not add new Indonesian comments or documentation. `tests/test_project_rules.py` scans the
docs and sources and fails when Indonesian prose appears. Fenced code blocks are exempt so an
example can quote real bot output, but the prose around them stays English.

## Commit messages: Conventional Commits

Format:

```
<type>(<scope>)<!>: <subject>

<body>

<footer>
```

- **type** — one of `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`,
  `ci`, `chore`, `revert`.
- **scope** — optional, lowercase, e.g. `feat(download): ...`, `fix(tiktok): ...`.
- **subject** — imperative mood ("add", not "added"/"adds"), lowercase first letter, no
  trailing period, header (`type(scope): subject`) at most 72 characters.
- **body** — optional, wrapped at 100 characters, explains *what* and *why*.
- **footer** — `BREAKING CHANGE: ...` and issue references such as `Refs: #12`.
- **English only.** The hook rejects messages containing Indonesian words.

Examples:

```
feat(cleanup): cancel running downloads and purge leftovers
fix(tiktok): prefer play_addr over watermarked download_addr
docs(deploy): document podman quadlet setup
chore(deps): bump yt-dlp to 2026.10.26
```

### Enforcement

The rule is enforced by a local `commit-msg` hook. Enable it once per clone:

```bash
git config core.hooksPath .githooks
```

The hook runs `scripts/check_commit_message.py` and blocks invalid messages. To check a
message manually:

```bash
python3 scripts/check_commit_message.py --message "feat: add cleanup command"
```

## Workflow

1. Create a branch unless the change is trivial: `feat/...`, `fix/...`, `docs/...`.
2. Run the full suite before committing: `./run.sh test` (76+ tests, no network needed).
3. Keep commits focused; one logical change per commit.
4. Never commit `.env`, `cookies/*.txt`, `downloads/`, `tmp/`, or logs — they are gitignored.
5. Update `README.md` / `docs/DEPLOY.md` when behaviour, configuration, or deployment changes.

## Architecture contracts

These are invariants, not suggestions — tests rely on them:

- Downloaders return a `DownloadResult` with `MediaItem`s; they never send anything to
  Telegram themselves.
- Every download runs inside a `JobDir` and is registered in `JobRegistry`, so `/cleanup`
  can cancel it and remove files.
- All downloaded files are deleted after sending (success, failure, or oversize skip). If you
  add a new media path, keep that guarantee and add a test.
- Videos are normalized to H.264 (`ensure_h264`) before sending, otherwise many clients show
  them as documents.
- The command menu is derived from registered `CommandHandler`s; add new commands to
  `MENU_COMMANDS` and `ALIASES` in `appbot/bot_profile.py`.
- `tmp/heartbeat` is written by the job queue every 60 s; `bot.py --health` reads it.
  Anything that purges `tmp/` must re-create it.

## Testing

```bash
./run.sh test                                  # unit + integration, offline
.venv/bin/python scripts/livetest.py --video-url <URL>   # real download, needs network
```

Real-network behaviour cannot be faked: when you touch a downloader, verify with
`scripts/livetest.py` and check the artifact with `ffprobe`.
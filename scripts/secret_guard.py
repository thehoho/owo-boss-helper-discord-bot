from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


SAFE_ENV_FILES = {".env.example"}
SAFE_GUARD_PATHS = {"scripts/secret_guard.py", "tests/test_secret_guard.py"}
BLOCKED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}
BLOCKED_NAMES = re.compile(
    r"(?:^|[-_.])(credentials?|secrets?|service[-_.]?account)(?:[-_.]|$)", re.I
)
PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
)
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b")
DISCORD_CREDENTIAL_PATTERN = re.compile(
    r"\b(?:mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{20,})\b"
)
ASSIGNMENT = re.compile(
    r"(?m)^[ \t]*(?:export[ \t]+)?"
    r"([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY))"
    r"[ \t]*=[ \t]*([^\r\n#]*)"
)
PLACEHOLDER_PARTS = (
    "your_",
    "replace_",
    "example",
    "placeholder",
    "changeme",
    "redacted",
    "<token>",
    "<secret>",
    "${",
)


def git(*args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.check_output(
        ["git", *args], input=input_bytes, stderr=subprocess.DEVNULL
    )


def candidate_paths(mode: str) -> list[str]:
    if mode == "staged":
        raw = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    else:
        raw = git("ls-files", "-z")
    return [item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item]


def staged_content(path: str) -> bytes:
    return git("show", f":{path}")


def worktree_content(path: str) -> bytes:
    return Path(path).read_bytes()


def risky_filename(path: str) -> str | None:
    if path in SAFE_GUARD_PATHS:
        return None
    pure = PurePosixPath(path)
    name = pure.name.lower()
    if name.startswith(".env") and name not in SAFE_ENV_FILES:
        return "environment file"
    if pure.suffix.lower() in BLOCKED_SUFFIXES:
        return "key or certificate container"
    if BLOCKED_NAMES.search(name):
        return "credential or secret file"
    return None


def safe_placeholder(value: str) -> bool:
    cleaned = value.strip().strip("\"'").strip().lower()
    return not cleaned or any(part in cleaned for part in PLACEHOLDER_PARTS)


def content_findings(content: bytes) -> list[str]:
    if b"\0" in content:
        return []
    text = content.decode("utf-8", "replace")
    findings: list[str] = []
    if PRIVATE_KEY.search(text):
        findings.append("private key material")
    if JWT.search(text):
        findings.append("JWT-like token")
    if DISCORD_CREDENTIAL_PATTERN.search(text):
        findings.append("Discord-token-like value")
    for match in ASSIGNMENT.finditer(text):
        if not safe_placeholder(match.group(2)):
            findings.append(f"non-placeholder value assigned to {match.group(1)}")
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser(description="Block secrets before Git commit/push.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--tracked", action="store_true")
    args = parser.parse_args()
    mode = "staged" if args.staged else "tracked"
    failures: list[tuple[str, str]] = []
    for path in candidate_paths(mode):
        filename_problem = risky_filename(path)
        if filename_problem:
            failures.append((path, filename_problem))
            continue
        try:
            content = staged_content(path) if mode == "staged" else worktree_content(path)
        except (OSError, subprocess.CalledProcessError):
            failures.append((path, "could not be inspected"))
            continue
        failures.extend((path, finding) for finding in content_findings(content))
    if failures:
        print("Secret guard blocked this Git operation:", file=sys.stderr)
        for path, reason in failures:
            print(f"- {path}: {reason}", file=sys.stderr)
        print("No secret values were printed. Remove or rotate the credential.", file=sys.stderr)
        return 1
    print(f"Secret guard passed for {len(candidate_paths(mode))} {mode} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
AdvancedShellm Automated Test Runner
=====================================

Drives a live advancedShellm.py session (or one session per test) via
stdin/stdout pipes, runs every test in ssh_tests_definitions.py, and
writes the full terminal transcript + pass/fail result to tests/logs/.

Usage:
    python3 run_tests.py \\
        --provider openai --model gpt-4o --personality worker \\
        [--supervise] \\
        [--manager_provider openai] [--manager_model gpt-4o-mini] \\
        [--mode single|per-test] \\
        [--timeout 120] \\
        [--trace]

Modes:
    single    All tests run sequentially in ONE shared session.
              Filesystem state accumulates across tests.
    per-test  Each test gets a FRESH session (default).
              worker.yml is restored before every test.
"""

import argparse
import os
import re
import shutil
import sys
import subprocess
import threading
import time
from datetime import datetime

# ── paths ────────────────────────────────────────────────────────────────────
TESTS_DIR        = os.path.dirname(os.path.abspath(__file__))
CODEBASE_DIR     = os.path.abspath(os.path.join(TESTS_DIR, ".."))
LOGS_DIR         = os.path.join(TESTS_DIR, "logs")
PERSONALITIES_DIR = os.path.join(CODEBASE_DIR, "LLM_Plugins", "personalities")


def _personality_yml(personality: str) -> str:
    """Absolute path to <personality>.yml."""
    return os.path.join(PERSONALITIES_DIR, f"{personality}.yml")


def _personality_backup_yml(personality: str) -> str:
    """Absolute path to <personality>_backup.yml."""
    return os.path.join(PERSONALITIES_DIR, f"{personality}_backup.yml")


def _restore_personality_from_backup(personality: str):
    """
    Copy <personality>_backup.yml  →  <personality>.yml
    and flush the result to disk so the child process sees a clean state.
    Raises FileNotFoundError if the backup does not exist.
    """
    src = _personality_backup_yml(personality)
    dst = _personality_yml(personality)
    if not os.path.exists(src):
        raise FileNotFoundError(
            f"Backup personality file not found: {src}\n"
            f"Create '{os.path.basename(src)}' in the personalities directory "
            f"to enable clean-state restores."
        )
    with open(src, "r", encoding="utf-8") as fin:
        content = fin.read()
    with open(dst, "w", encoding="utf-8") as fout:
        fout.write(content)
        fout.flush()
        os.fsync(fout.fileno())


# Add codebase to path so we can import test definitions
sys.path.insert(0, TESTS_DIR)
from ssh_tests_definitions import TESTS, SHELL_PROMPT_REGEX  # noqa: E402

# Detect any "ready-for-input" prompt: shell $ or REPL >>>
PROMPT_READY_RE = re.compile(
    r"(?:"
    r"(?:\S+@\S+:[^\$]*|\S+(?:/\S+)+)\$"  # shell: user@host:~$
    r"|>>>"                                 # python REPL
    r"|\.\.\."                              # python continuation
    r")\s*$"
)


# ── subprocess driver ─────────────────────────────────────────────────────────

class ProcessDriver:
    """
    Drives advancedShellm.py as a child process via stdin/stdout pipes.
    A background thread accumulates all stdout characters so we never block
    on reads; the main thread polls until it sees a prompt pattern.
    """

    def __init__(self, cmd: list[str]):
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,          # unbuffered — critical so chars arrive immediately
            cwd=CODEBASE_DIR,
        )
        self._stdout_buf: list[str] = []
        self._stderr_buf: list[str] = []
        self._lock = threading.Lock()
        self._finished = False

        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # ── background readers ────────────────────────────────────────────────

    def _read_stdout(self):
        while True:
            ch = self.proc.stdout.read(1)
            if not ch:
                self._finished = True
                break
            with self._lock:
                self._stdout_buf.append(ch.decode("utf-8", errors="replace"))

    def _read_stderr(self):
        while True:
            line = self.proc.stderr.readline()
            if not line:
                break
            with self._lock:
                self._stderr_buf.append(line.decode("utf-8", errors="replace"))

    # ── public API ────────────────────────────────────────────────────────

    def read_until_prompt(self, timeout: float = 120.0) -> str:
        """
        Accumulate stdout until a shell/REPL prompt appears at the very end
        of the buffer and remains stable for 0.3 s (guard against mid-line
        matches during streaming), or until *timeout* seconds elapse.
        Returns everything captured and clears the internal buffer.
        """
        start = time.time()
        stable_since: float | None = None

        while time.time() - start < timeout:
            if self._finished:
                break

            with self._lock:
                text = "".join(self._stdout_buf)

            if PROMPT_READY_RE.search(text):
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= 0.3:
                    break           # prompt stable for 0.3 s → done
            else:
                stable_since = None

            time.sleep(0.05)

        with self._lock:
            text = "".join(self._stdout_buf)
            self._stdout_buf.clear()
        return text

    def send(self, cmd: str):
        """Write a line to stdin."""
        self.proc.stdin.write((cmd + "\n").encode())
        self.proc.stdin.flush()

    def stderr_output(self) -> str:
        with self._lock:
            return "".join(self._stderr_buf)

    def close(self):
        """Gracefully stop the child; kill if it won't cooperate."""
        try:
            self.proc.stdin.write(b"exit\n")
            self.proc.stdin.flush()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


# (snapshot helpers removed — personality state is now restored from backup)


# ── command builder ───────────────────────────────────────────────────────────

def _build_cmd(args) -> list[str]:
    cmd = [
        sys.executable,
        os.path.join(CODEBASE_DIR, "advancedShellm.py"),
        "--provider",    args.provider,
        "--model",       args.model,
        "--personality", args.personality,
        "--cleaned",     # always start with a fresh log directory
    ]
    if args.supervise:
        cmd.append("--supervise")
    if args.trace:
        cmd.append("--trace")
    if args.manager_provider:
        cmd += ["--manager_provider", args.manager_provider]
    if args.manager_model:
        cmd += ["--manager_model", args.manager_model]
    return cmd


# ── single test execution ─────────────────────────────────────────────────────

def _run_one_test(
    driver: ProcessDriver,
    test: dict,
    log_f,
    index: int,
    total: int,
    timeout: float,
) -> bool:
    """
    Send the test command, capture full terminal output, evaluate assertion.
    Writes a detailed transcript block to *log_f*.
    Returns True if the assertion passed.
    """
    name = test["name"]
    cmd  = test["command"]

    sep = "─" * 60
    log_f.write(f"\n{sep}\n")
    log_f.write(f"TEST {index:02d}/{total:02d}: {name}\n")
    log_f.write(f"{sep}\n")
    log_f.write(f"COMMAND: {cmd}\n\n")
    log_f.write("─── TERMINAL OUTPUT ───────────────────────────────────────\n")
    log_f.flush()

    driver.send(cmd)
    response = driver.read_until_prompt(timeout=timeout)

    log_f.write(response if response.strip() else "(empty response)\n")
    log_f.write("\n")

    # ── assertion ──────────────────────────────────────────────────────
    try:
        passed = bool(test["assertion"](response))
        assertion_err = None
    except Exception as exc:
        passed = False
        assertion_err = str(exc)

    status = "✓  PASS" if passed else "✗  FAIL"
    log_f.write("─── RESULT ─────────────────────────────────────────────────\n")
    log_f.write(f"{status}\n")
    if assertion_err:
        log_f.write(f"ASSERTION EXCEPTION: {assertion_err}\n")
    log_f.write(f"{sep}\n")
    log_f.flush()

    return passed


# ── run modes ─────────────────────────────────────────────────────────────────

def _write_run_header(log_f, args, mode: str, n_tests: int):
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep = "═" * 60
    log_f.write(f"{sep}\n")
    log_f.write(f"  ADVANCEDSHELLM TEST RUNNER\n")
    log_f.write(f"{sep}\n")
    log_f.write(f"  Timestamp  : {ts}\n")
    log_f.write(f"  Mode       : {mode}\n")
    log_f.write(f"  Provider   : {args.provider}\n")
    log_f.write(f"  Model      : {args.model}\n")
    log_f.write(f"  Personality: {args.personality}\n")
    log_f.write(f"  Supervise  : {'yes' if args.supervise else 'no'}\n")
    if args.supervise:
        mgr_prov  = args.manager_provider or args.provider
        mgr_model = args.manager_model or args.model
        log_f.write(f"  Manager    : {mgr_prov} / {mgr_model}\n")
    log_f.write(f"  Tests      : {n_tests}\n")
    log_f.write(f"{sep}\n\n")


def _write_summary(path: str, results: list[tuple[str, bool]], args, mode: str):
    passed = sum(1 for _, p in results if p)
    total  = len(results)
    failed = total - passed
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep    = "═" * 60

    mgr_line = []
    if args.supervise:
        mgr_prov  = args.manager_provider or args.provider
        mgr_model = args.manager_model or args.model
        mgr_line  = [f"  Manager    : {mgr_prov} / {mgr_model}"]

    lines = [
        sep,
        "  TEST RUN SUMMARY",
        sep,
        f"  Finished   : {ts}",
        f"  Mode       : {mode}",
        f"  Provider   : {args.provider} / {args.model}",
        f"  Personality: {args.personality}",
        f"  Supervise  : {'yes' if args.supervise else 'no'}",
        *mgr_line,
        f"  Result     : {passed}/{total} passed   {failed} failed",
        sep,
        "",
    ]
    for i, (name, p) in enumerate(results, 1):
        mark = "✓" if p else "✗"
        lines.append(f"  {mark}  [{i:02d}]  {name}")
    lines += [
        "",
        f"  PASSED: {passed}    FAILED: {failed}    TOTAL: {total}",
        sep,
    ]

    content = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content + "\n")
    print("\n" + content)


def mode_single_session(args, run_dir: str) -> list[tuple[str, bool]]:
    """All tests in one shared session — filesystem state accumulates."""
    log_path     = os.path.join(run_dir, "session_log.txt")
    summary_path = os.path.join(run_dir, "summary.txt")
    cmd          = _build_cmd(args)
    results: list[tuple[str, bool]] = []

    # Restore personality from backup once before the single session.
    _restore_personality_from_backup(args.personality)

    with open(log_path, "w", encoding="utf-8") as log_f:
        _write_run_header(log_f, args, "single-session", len(TESTS))
        log_f.write("── SESSION START ───────────────────────────────────────────\n")
        log_f.flush()

        driver = ProcessDriver(cmd)

        log_f.write("[Waiting for initial banner...]\n\n")
        log_f.flush()
        banner = driver.read_until_prompt(timeout=120)
        log_f.write("── INITIAL BANNER ──────────────────────────────────────────\n")
        log_f.write(banner or "(no initial output)\n")
        log_f.write("\n")
        log_f.flush()

        for i, test in enumerate(TESTS, 1):
            passed = _run_one_test(driver, test, log_f, i, len(TESTS), args.timeout)
            results.append((test["name"], passed))
            status = "PASS" if passed else "FAIL"
            print(f"  [{i:02d}/{len(TESTS)}] {status:4s}  {test['name']}")

        log_f.write("\n── SESSION END ─────────────────────────────────────────────\n")
        driver.close()

        stderr = driver.stderr_output()
        if stderr.strip():
            log_f.write("\n── STDERR ──────────────────────────────────────────────────\n")
            log_f.write(stderr)

    _write_summary(summary_path, results, args, "single-session")
    return results


def mode_per_test(args, run_dir: str) -> list[tuple[str, bool]]:
    """Each test runs in its own fresh session for full isolation."""
    summary_path = os.path.join(run_dir, "summary.txt")
    results: list[tuple[str, bool]] = []

    for i, test in enumerate(TESTS, 1):
        # Restore personality from backup before every session so tests are isolated.
        _restore_personality_from_backup(args.personality)

        # Sanitise test name for use as a directory name.
        safe_name = re.sub(r"[^\w\- ]", "", test["name"]).strip().replace(" ", "_")
        test_dir  = os.path.join(run_dir, f"{i:02d}_{safe_name}")
        os.makedirs(test_dir, exist_ok=True)
        log_path  = os.path.join(test_dir, "session_log.txt")

        cmd = _build_cmd(args)

        with open(log_path, "w", encoding="utf-8") as log_f:
            _write_run_header(log_f, args, "per-test", len(TESTS))
            log_f.write(f"── FRESH SESSION FOR TEST {i:02d}/{len(TESTS)} ──────────────────────────\n")
            log_f.write(f"   {test['name']}\n\n")
            log_f.flush()

            driver = ProcessDriver(cmd)

            log_f.write("[Waiting for initial banner...]\n\n")
            log_f.flush()
            banner = driver.read_until_prompt(timeout=120)
            log_f.write("── INITIAL BANNER ──────────────────────────────────────────\n")
            log_f.write(banner or "(no initial output)\n")
            log_f.write("\n")
            log_f.flush()

            passed = _run_one_test(driver, test, log_f, i, len(TESTS), args.timeout)
            results.append((test["name"], passed))

            log_f.write("\n── SESSION END ─────────────────────────────────────────────\n")
            driver.close()

            stderr = driver.stderr_output()
            if stderr.strip():
                log_f.write("\n── STDERR ──────────────────────────────────────────────────\n")
                log_f.write(stderr)

        status = "PASS" if passed else "FAIL"
        print(f"  [{i:02d}/{len(TESTS)}] {status:4s}  {test['name']}")

    _write_summary(summary_path, results, args, "per-test")
    return results


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AdvancedShellm automated test runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--provider",  required=True,
                        choices=["openai", "einfra", "ollama", "localqlora"])
    parser.add_argument("--model",     required=True)
    parser.add_argument("--personality", required=True)

    parser.add_argument("--supervise", action="store_true", default=False,
                        help="Run advancedShellm with --supervise (manager review loop)")
    parser.add_argument("--trace",     action="store_true", default=False,
                        help="Run advancedShellm with --trace (verbose API logging)")

    parser.add_argument("--manager_provider",
                        choices=["openai", "einfra", "ollama", "localqlora"])
    parser.add_argument("--manager_model")

    parser.add_argument("--mode", choices=["single", "per-test"], default="per-test",
                        help=(
                            "single   : all tests share ONE session (state accumulates)\n"
                            "per-test : each test gets a FRESH session (default)"
                        ))
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="Seconds to wait per LLM response (default: 120)")

    args = parser.parse_args()

    # ── create run directory ──────────────────────────────────────────────
    os.makedirs(LOGS_DIR, exist_ok=True)
    ts      = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = os.path.join(LOGS_DIR, f"{ts}_{args.mode}")
    os.makedirs(run_dir, exist_ok=True)

    # ── print header ──────────────────────────────────────────────────────
    print()
    print("╔══════════════════════════════════════════════╗")
    print("║      AdvancedShellm Test Runner              ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"  Mode       : {args.mode}")
    print(f"  Provider   : {args.provider} / {args.model}")
    print(f"  Personality: {args.personality}")
    print(f"  Supervise  : {'yes' if args.supervise else 'no'}")
    print(f"  Timeout    : {args.timeout}s per command")
    print(f"  Tests      : {len(TESTS)}")
    print(f"  Log dir    : {run_dir}")
    print()

    # ── dispatch ──────────────────────────────────────────────────────────
    if args.mode == "single":
        results = mode_single_session(args, run_dir)
    else:
        results = mode_per_test(args, run_dir)

    passed = sum(1 for _, p in results if p)
    print(f"\nLogs saved to: {run_dir}")

    # Exit 0 only when everything passed — useful for CI
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()

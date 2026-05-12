import subprocess
import os
import re
import time
from dotenv import load_dotenv
import argparse
import select
from pathlib import Path

from ssh_tests_definitions import TESTS

# --- Load Environment ---
load_dotenv()
THIS_FILE = Path(__file__).resolve()

PROJECT_ROOT = THIS_FILE.parents[2]  # Project/
BOT_PATH = PROJECT_ROOT / "Honeypots - separate" / "SSH" / "LinuxSSHbot.py"

SCRIPT_TO_TEST = str(BOT_PATH)
if not SCRIPT_TO_TEST or not os.path.exists(SCRIPT_TO_TEST):
    raise FileNotFoundError("❌ SSH_BOT_PATH not found or invalid in .env")

# --- Argument setup ---
parser = argparse.ArgumentParser(description="Run SSH honeypot tests")
parser.add_argument("--provider", type=str, required=True,
                    choices=["openai", "einfra", "ollama", "localqlora"],
                    help="LLM backend provider")
parser.add_argument("--model", type=str, required=False,
                    default=os.getenv("MODEL_CHOICE", "llama3.3"),
                    help="Model name (or ID) to use")
parser.add_argument("--personality", type=str, required=False,
                    default=os.getenv("PERSONALITY", "gpt-oss-120b"),
                    help="Personality YAML (without .yml) to use")
parser.add_argument("--trace", action="store_true", help="Enable detailed trace logging")
parser.add_argument("--session-mode", type=str, default="single",
                    choices=["single", "per-test"],
                    help="Session strategy: 'single' = one session for all tests; 'per-test' = new session per test")
args = parser.parse_args()

# --- Paths ---
honeypot_dir = os.path.dirname(os.path.abspath(SCRIPT_TO_TEST))

# Honeypot's own logs (cleared every run)
honeypot_logs_dir = os.path.join(honeypot_dir, "logs")
os.makedirs(honeypot_logs_dir, exist_ok=True)

# Test runner logs (persistent across runs)
test_runner_dir = os.path.dirname(os.path.abspath(__file__))
test_logs_dir = os.path.join(test_runner_dir, "logs")
os.makedirs(test_logs_dir, exist_ok=True)

TRACE_MODE = args.trace
timestamp = int(time.time())

# Clean ONLY honeypot logs before starting
for f in os.listdir(honeypot_logs_dir):
    try:
        os.remove(os.path.join(honeypot_logs_dir, f))
    except IsADirectoryError:
        continue
print(f"🧹 Cleared old honeypot logs from: {honeypot_logs_dir}")

# Test runner outputs go here (Tests/SSH/logs)
TRACE_FILE = os.path.join(test_logs_dir, f"trace_log_{timestamp}.txt") if TRACE_MODE else None
LOG_FILE = os.path.join(test_logs_dir, f"test_session_log_{timestamp}.txt")

# --- Utility: trace logger ---
def trace_log(event: str, data: str):
    if not TRACE_MODE:
        return
    timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open(TRACE_FILE, "a", encoding="utf-8") as tf:
        tf.write(f"[{timestamp_str}] {event}:\n{data}\n{'-'*70}\n")

# --- Regex prompt ---
shell_prompt_regex = re.compile(r"\S+@\S+:.*?\$\s*$")

def extract_between_markers(text: str) -> str:
    """Extract the newest model output between <<BEGIN_MSG>> and <<END_MSG>>."""
    pattern = re.compile(r"<<BEGIN_MSG>>(.*?)<<END_MSG>>", re.DOTALL)
    matches = pattern.findall(text)
    if not matches:
        return text.strip()
    # return the last (most recent) wrapped block only
    return matches[-1].strip()

def read_until_end_marker(process, overall_timeout=300, inactivity_timeout=5.0, expect_marker=True):
    """
    Read from process.stdout until we see '<<END_MSG>>' OR
    (in non-marker mode) we hit inactivity timeout.
    Uses low-level os.read() to avoid fighting TextIO buffering.
    """
    buffer = ""
    start = time.time()
    last_data = None
    marker = "<<END_MSG>>"

    fd = process.stdout.fileno()

    while True:
        now = time.time()
        if overall_timeout and (now - start > overall_timeout):
            print("⚠️ Overall timeout waiting for model output.")
            break

        rlist, _, _ = select.select([fd], [], [], 0.1)

        if not rlist:
            if last_data is None:
                # Haven't seen any data at all yet, keep waiting
                continue

            if expect_marker:
                # Wait until marker arrives or timeout
                continue

            if now - last_data > inactivity_timeout:
                break
            continue

        chunk = os.read(fd, 1024).decode(errors="ignore")
        if not chunk:
            break  # EOF

        buffer += chunk
        last_data = now

        if marker in buffer and expect_marker:
            break

        if not expect_marker and buffer:
            lines = buffer.splitlines()
            tail = lines[-1] if lines else buffer
            if shell_prompt_regex.search(tail):
                break

    return buffer

def prepare_environment_for_run():
    """Remove honeypot history file to start a fresh logical session."""
    honeypot_history = os.path.join(honeypot_dir, "history.txt")
    if os.path.exists(honeypot_history):
        try:
            os.remove(honeypot_history)
            print(f"🧹 Removed old honeypot history: {honeypot_history}")
        except Exception as e:
            print(f"⚠️ Could not remove honeypot history: {e}")

def start_honeypot_process():
    """Start the honeypot process in testing mode and read the initial prompt."""
    cmd = [
        "python3", "-u", SCRIPT_TO_TEST,
        "--provider", args.provider,
        "--model", args.model,
        "--personality", args.personality,
        *(["--trace"] if TRACE_MODE else []),
        *(["--testing"]),
        *(["--cleaned"]),
    ]

    trace_log("PROCESS_START", " ".join(cmd))

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    print("⌛ Waiting for initial prompt (testing mode)...")
    welcome_output = read_until_end_marker(process, expect_marker=True)
    markers_expected = "<<END_MSG>>" in welcome_output
    if not markers_expected:
        print("⚠️ Honeypot output is missing <<END_MSG>> markers; reverting to prompt-based detection.")

    print(welcome_output)
    trace_log("WELCOME", welcome_output)

    return process, markers_expected, welcome_output

def run_single_test(process, markers_expected: bool, test: dict):
    """Send one test command to the honeypot process and evaluate its assertion."""
    name, command = test["name"], test["command"]
    print(f"\n🧑‍💻 {name} → {command}")

    if process.poll() is not None:
        print("❌ Process exited early before running this test.")
        return False, markers_expected

    # Send the command
    process.stdin.write(f"{command}\n")
    process.stdin.flush()
    trace_log("SENT", command)

    # Read response
    current_output = read_until_end_marker(process, expect_marker=markers_expected)
    if markers_expected and "<<END_MSG>>" not in current_output:
        print("⚠️ Output missing <<END_MSG>> marker; switching to prompt-based capture.")
        markers_expected = False
    trace_log("RECEIVED", current_output)

    cleaned_output = extract_between_markers(current_output)

    # Do NOT strip echoed commands or prompts here.
    cleaned_output = cleaned_output.rstrip("\n")

    if cleaned_output:
        print(cleaned_output)

    # Evaluate assertion
    try:
        passed = bool(test["assertion"](cleaned_output))
    except Exception as e:
        print(f"❌ Error in assertion: {e}")
        passed = False

    status = "✅ PASSED" if passed else "❌ FAILED"
    print(status)

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n# --- {name} ---\nCOMMAND: {command}\n{cleaned_output}\nRESULT: {status}\n{'-'*60}\n")

    return passed, markers_expected

def run_tests_single_session():
    """Run all tests in ONE honeypot session."""
    prepare_environment_for_run()
    print(f"🚀 Launching honeypot (single session) with provider={args.provider}, model={args.model}, personality={args.personality}")
    print(f"🗂 Logs will be stored in: {test_logs_dir}")

    process, markers_expected, _welcome = start_honeypot_process()

    results = []  # >>> track per-test results

    for test in TESTS:
        passed, markers_expected = run_single_test(process, markers_expected, test)
        results.append({"name": test["name"], "passed": passed})  # >>>
        time.sleep(2)

    # Wrap up
    try:
        process.stdin.close()
    except Exception:
        pass
    process.wait()

    return results  # >>>

def run_tests_session_per_test():
    """
    Run each test in its OWN honeypot session.
    History + environment cleaned before each start.
    """
    print(f"🚀 Launching honeypot (session per test) with provider={args.provider}, model={args.model}, personality={args.personality}")
    print(f"🗂 Logs will be stored in: {test_logs_dir}")

    results = []  # >>>

    for test in TESTS:
        prepare_environment_for_run()
        print("\n" + "=" * 60)
        print(f"🔁 New session for test: {test['name']}")
        print("=" * 60)

        process, markers_expected, _welcome = start_honeypot_process()

        passed, _ = run_single_test(process, markers_expected, test)
        results.append({"name": test["name"], "passed": passed})  # >>>

        try:
            process.stdin.close()
        except Exception:
            pass
        process.wait()
        time.sleep(1)

    return results  # >>>

def run_tests():
    # >>> Write header at the very beginning of the log
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write("SSH Honeypot Test Run\n")
        f.write(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n")
        f.write(f"Provider   : {args.provider}\n")
        f.write(f"Model      : {args.model}\n")
        f.write(f"Personality: {args.personality}\n")
        f.write(f"Session mode: {args.session_mode}\n")
        f.write("=" * 60 + "\n")

    if args.session_mode == "single":
        results = run_tests_single_session()
    else:
        results = run_tests_session_per_test()

    total = len(results)
    failed_tests = [r for r in results if not r["passed"]]
    passed_count = total - len(failed_tests)

    # >>> Append detailed T1..Tn summary to log
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write("\n=== Test Summary ===\n")
        for idx, r in enumerate(results, start=1):
            status = "PASSED" if r["passed"] else "FAILED"
            # T1 - Test Name - PASSED/FAILED
            f.write(f"T{idx} - {r['name']} - {status}\n")
        f.write(f"\nTotal: {passed_count}/{total} tests passed.\n")
        if failed_tests:
            f.write("Failed: " + ", ".join(r["name"] for r in failed_tests) + "\n")
        f.write("=" * 60 + "\n")

    summary = f"✅ {passed_count}/{total} tests passed."
    if failed_tests:
        summary += f"\n❌ Failed: {', '.join(r['name'] for r in failed_tests)}"

    print("\n" + "=" * 60)
    print(summary)
    print(f"📘 Log saved to: {LOG_FILE}")
    if TRACE_MODE:
        print(f"📜 Full trace saved to: {TRACE_FILE}")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
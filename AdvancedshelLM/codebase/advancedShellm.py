import os
import sys

# Wrap heavy imports to prevent ugly traceback on Ctrl+C during load
try:
    import argparse
    import random
    import re
    import time
    from datetime import datetime
    from time import sleep
    import readline
    import yaml
    import json
    from dotenv import dotenv_values
    from LLM_Plugins.log_manager import LogManager
    from LLM_Plugins.api_clients.openai_client import OpenAIClient
    from LLM_Plugins.api_clients.einfra_client import EinfraClient, BackendUnavailable, BackendHTTPError, BackendParseError
    from LLM_Plugins.manager_agent import ManagerAgent
except (KeyboardInterrupt, Exception):
    print("\nConnection closed by remote host.")
    sys.exit(1)


# -------- Helpers --------
def _extract_code(text: str) -> str:
    if not text:
        return " "
    m = re.search(r"```(?:\s*\w+)?\s*\r?\n([\s\S]*?)\r?\n```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"`([^`]+)`", text)
    if m2:
        return m2.group(1).strip()
    return text.strip()


def strip_hallucinated_fences(text: str) -> str:
    """
    Remove markdown code fences (``` ... ```) that are model hallucinations,
    while preserving output that legitimately contains backtick sequences.

    A fence is considered a hallucination when:
      - The opening ``` line is the very first non-empty line of the response, AND
      - The closing ``` line is the very last non-empty line of the response.

    This means the model wrapped its entire answer in a code block — a classic
    hallucination pattern.  If ``` appears only in the middle of the text (e.g.
    the user ran `cat` on a Markdown file, or the shell output genuinely contains
    backticks), the fences are left untouched.
    """
    if not isinstance(text, str) or "```" not in text:
        return text

    lines = text.splitlines()

    # Find first and last non-empty line indices
    first_nonempty = next((i for i, l in enumerate(lines) if l.strip()), None)
    last_nonempty  = next((i for i, l in enumerate(reversed(lines)) if l.strip()), None)
    if first_nonempty is None or last_nonempty is None:
        return text
    last_nonempty = len(lines) - 1 - last_nonempty

    first_line = lines[first_nonempty].strip()
    last_line  = lines[last_nonempty].strip()

    # Opening fence: ``` optionally followed by a language tag (e.g. ```bash)
    opening_fence = re.match(r"^```\w*$", first_line)
    closing_fence = (last_line == "```")

    if opening_fence and closing_fence and first_nonempty < last_nonempty:
        # Strip the fence lines; preserve everything in between
        inner = lines[first_nonempty + 1 : last_nonempty]
        return "\n".join(inner).strip()

    return text


def remove_think_blocks(text: str) -> str:
    if not isinstance(text, str):
        return text
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<\|.*?\|>", "", cleaned)
    cleaned = re.sub(r"<analysis>|</analysis>|<final>|</final>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

END_MARKER = "<|end|>"

def strip_after_end_marker(text: str, marker: str = END_MARKER) -> str:
    """Cut everything from the first <|end|> (or marker) to the end."""
    if not isinstance(text, str):
        return text
    return text.split(marker, 1)[0]


def is_null_like_response(value) -> bool:
    """
    Treat backend null-ish outputs as no-op responses.
    """
    if value is None:
        return True
    if isinstance(value, str):
        s = value.strip().lower()
        return s in {"", "null", "none"}
    return False


def fallback_prompt(last_prompt: str | None) -> str:
    p = (last_prompt or "").strip()
    return p if p else "$"


# Patterns that are dead giveaways of leaked reasoning prose.
# These are lines that look like the model is explaining itself rather than
# outputting shell content. Checked against each line of the draft.
_LEAKAGE_SENTENCE_RE = re.compile(
    r"""
    (                               # Any of these at the START of a line (ignoring leading whitespace)
        we\s+need\s+to              # "We need to respond / output..."
      | so\s+we\s+should            # "So we should output..."
      | the\s+user\s+is\s+in       # "The user is in a Python REPL..."
      | in\s+(python|the)\s+repl   # "In Python REPL, that's a NameError"
      | as\s+per\s+rules            # "as per rules"
      | i\s+should\s+output         # "I should output..."
      | this\s+means\s+(we|i)       # "This means we need to..."
      | respond\s+with\s+only       # "respond with only..."
      | no\s+extra\.?$              # "No extra." at end of prose
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

def strip_thought_leakage(text: str) -> str:
    """
    Remove leaked reasoning prose from the beginning of a worker response.

    The model occasionally bleeds its internal chain-of-thought directly into
    the output, e.g.:
        "We need to respond as per rules. The user is in a Python REPL...
         No extra.NameError: name 'mkdir' is not defined\n>>> "

    Strategy: scan lines from the top. A line is considered "leakage prose" if
    it matches any of the known reasoning-sentence patterns. Once we hit a line
    that looks like real shell/REPL output (error message, prompt, blank between
    sections, etc.) we stop stripping and keep everything from that point.

    This is intentionally conservative: it only strips lines it is very confident
    are prose. It will NOT strip legitimate error messages or shell output.
    """
    if not isinstance(text, str) or not text.strip():
        return text

    lines = text.splitlines()
    first_real = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            # Blank lines between prose and output — skip, keep looking
            first_real = i + 1
            continue
        if _LEAKAGE_SENTENCE_RE.search(stripped):
            # This whole line (or the prefix of a fused line) is reasoning prose.
            # If the prose is FUSED onto the real output (no newline between them),
            # try to split at the first recognisable shell-output token.
            # e.g. "No extra.NameError: name 'mkdir' is not defined"
            fused = re.split(r'(?i)(NameError|TypeError|SyntaxError|ValueError|'
                             r'AttributeError|ImportError|IndentationError|'
                             r'bash:\s|admin@|>>>|\$\s)', stripped, maxsplit=1)
            if len(fused) > 1:
                # Reconstruct from the real-output fragment onward
                real_fragment = "".join(fused[1:])
                lines[i] = real_fragment
                first_real = i
                break
            # Pure prose line — mark it for dropping
            first_real = i + 1
        else:
            # First non-prose line found — stop stripping
            first_real = i
            break

    result = "\n".join(lines[first_real:])
    return result.strip() if result.strip() else text.strip()


def _extract_fs_entries_from_prompt(prompt_text: str) -> list[str]:
    """
    Extract filesystem JSON-lines from the live system prompt.
    Includes entries from both FILESYSTEM LISTING and USER-CREATED FILES.
    """
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        return []

    entries = []
    for line in prompt_text.splitlines():
        s = line.strip()
        if not (s.startswith("{") and s.endswith("}")):
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if isinstance(obj, dict) and "p" in obj and "k" in obj:
            entries.append(s)
    return entries





def simulate_ping_output(response: str) -> str:
    """
    Stream ping-like output line by line.

    IMPORTANT: we *do not* print the final prompt line, we return it so the
    caller can pass it as the argument to input(). That way readline knows
    where the prompt ends and will not let the user backspace over it.
    """
    lines = [line for line in response.split("\n") if line.strip()]
    if len(lines) < 4 or not any(("icmp_seq" in l.lower()) or l.lstrip().lower().startswith("ping ") for l in lines):
        # Not really ping output – keep old behaviour
        print(response)
        return lines[-1] if lines else ""

    # Stream everything except the last line (usually the shell prompt)
    for line in lines[:-1]:
        print(line)
        time.sleep(random.uniform(0.12, 0.25))

    # Do NOT print the last line here – let the caller treat it as a prompt
    return lines[-1] if lines else ""



def load_personality_text(personality_path: str) -> dict:
    """Load both prompt and continuation from a personality YAML."""
    with open(personality_path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            if "personality" in data and isinstance(data["personality"], dict):
                prompt = str(data["personality"].get("prompt", "")).strip()
                continuation = str(data["personality"].get("continuation", "")).strip()
                return {"prompt": prompt, "continuation": continuation}
            elif "prompt" in data:
                return {"prompt": str(data["prompt"]).strip(), "continuation": ""}
        # fallback if YAML structure unexpected
        return {"prompt": raw.strip(), "continuation": ""}
    except Exception:
        return {"prompt": raw.strip(), "continuation": ""}



PROMPT_LINE_RE = re.compile(r".*[#$]\s*$")

def extract_prompt_line(text: str, allow_bare_shell: bool = False) -> str | None:
    if not isinstance(text, str):
        return None

    shell_candidates = []

    for ln in reversed(text.splitlines()):
        s = ln.strip()
        if not s:
            continue

        # REPL prompts — standalone or as a prefix (model hallucination pattern:
        # ">>> admin@hp_dev_server:~/banana$" when it blindly appends shell prompt
        # after the REPL prompt on the same line).
        if s in (">>>", "..."):
            return s
        if s.startswith(">>> ") or s.startswith("... "):
            # The REPL is the active context; ignore anything pasted after it.
            return s[:3]

        # Shell prompts
        if s.endswith("$") or s.endswith("#"):
            # avoid learning bare "$" or "#" (common in bad outputs / errors)
            if not allow_bare_shell and s in ("$", "#"):
                continue
            shell_candidates.append(s)

    if not shell_candidates:
        return ("$" if allow_bare_shell else None)

    # Prefer "real" prompts like user@host:path$ over short junk
    def score(p: str) -> int:
        sc = 0
        if "@" in p: sc += 100
        if ":" in p: sc += 30
        if "~" in p or "/" in p: sc += 10
        sc += min(len(p), 80)
        return sc

    return max(shell_candidates, key=score)


def find_prompt_in_messages(messages: list) -> str | None:
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "assistant":
            p = extract_prompt_line(m.get("content", ""), allow_bare_shell=False)
            if p:
                return p
    return None


def ensure_prompt_at_end(text: str, last_prompt: str | None) -> str:
    if not isinstance(text, str):
        return "$ "

    p = extract_prompt_line(text, allow_bare_shell=False)

    # If response contains a prompt somewhere, TRIM to the last occurrence of that prompt.
    if p:
        lines = text.splitlines()
        cut = None
        repl_prompt = p in (">>>", "...")
        for i in range(len(lines) - 1, -1, -1):
            s = lines[i].strip()
            if s == p:
                cut = i
                break
            # Handle hallucination: ">>> admin@hp_dev_server:~/banana$" on one line.
            # When p is a REPL prompt, also accept lines that START with that prefix.
            if repl_prompt and s.startswith(p + " "):
                # Rewrite the mixed line to just the clean REPL prompt
                lines[i] = p
                cut = i
                break
        if cut is not None:
            # Strip trailing blank lines immediately before the prompt
            content_lines = lines[:cut]
            while content_lines and content_lines[-1].strip() == "":
                content_lines.pop()
            text = "\n".join(content_lines + [lines[cut]])

        return text if text.endswith(" ") else (text + " ")

    # Otherwise append last known prompt
    if last_prompt:
        base = (text or "").rstrip("\n ")
        if base:
            return base + "\n" + last_prompt + " "
        return last_prompt + " "

    return (text if text.endswith(" ") else (text + " "))



# -------- Filesystem patch helper --------
def _apply_fs_patch(
    messages: list,
    additions: list,
    removals: list,
    logger=None,
    personality_path: str | None = None,
) -> None:
    """
    Update the system prompt (messages[0]) to reflect filesystem changes.

    - Removals: drop matching JSON-line entries from anywhere in the prompt.
    - Additions: upsert JSON-line entries directly into the FILESYSTEM LISTING
      block (right after the last existing JSON entry in that block), so the
      listing stays as the single source of truth and retries do not duplicate
      the same path. If an ADD payload already includes ctime/mtime, preserve
      them; only fill missing timestamps with the real current time.

    After patching messages[0], immediately persists the updated prompt to
    personality_path (worker.yml) so the YAML is always in sync with memory.
    """
    import json as _json
    if not messages or not (additions or removals):
        return

    system_text = messages[0].get("content", "")
    lines = system_text.split("\n")

    # ---- Removals: strip matching paths from any JSON line in the prompt ----
    if removals:
        removal_set = set(removals)
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("{"):
                try:
                    obj = _json.loads(stripped)
                    if obj.get("p") in removal_set:
                        continue  # Drop this entry
                except (ValueError, KeyError):
                    pass
            new_lines.append(line)
        lines = new_lines

    # ---- Additions: upsert directly into FILESYSTEM LISTING ----
    if additions:
        from datetime import datetime as _dt, timezone as _tz
        now_iso = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        valid_by_path = {}
        valid_order = []
        for entry in additions:
            entry = entry.strip()
            try:
                obj = _json.loads(entry)
                if "p" in obj and "k" in obj:
                    path = obj["p"]
                    obj.setdefault("ctime", now_iso)
                    obj.setdefault("mtime", now_iso)
                    if path not in valid_by_path:
                        valid_order.append(path)
                    valid_by_path[path] = _json.dumps(obj, separators=(",", ":"))
            except (ValueError, KeyError):
                pass

        if valid_by_path:
            addition_paths = set(valid_by_path)
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("{"):
                    try:
                        obj = _json.loads(stripped)
                        if obj.get("p") in addition_paths:
                            continue
                    except (ValueError, KeyError):
                        pass
                new_lines.append(line)
            lines = new_lines
            valid = [valid_by_path[path] for path in valid_order]

            # Find the index of the last JSON-line entry in the prompt.
            # New entries are inserted immediately after it so they appear
            # inside the FILESYSTEM LISTING block.
            last_json_idx = None
            for i, line in enumerate(lines):
                s = line.strip()
                if s.startswith("{") and s.endswith("}"):
                    try:
                        obj = _json.loads(s)
                        if "p" in obj and "k" in obj:
                            last_json_idx = i
                    except Exception:
                        pass

            if last_json_idx is not None:
                lines = lines[:last_json_idx + 1] + valid + lines[last_json_idx + 1:]
            else:
                # Fallback: append at end if no JSON block found
                lines.extend(valid)

    messages[0]["content"] = "\n".join(lines)

    # ---- Persist to personality YAML immediately ----
    _persist_system_prompt(messages[0]["content"], personality_path)

    if logger and (additions or removals):
        if hasattr(logger, "log_fs_patch"):
            logger.log_fs_patch(additions, removals)
        else:
            logger.log_marker(
                f"fs_patch applied — added: {len(additions)}, removed: {len(removals)}"
            )

        # Also append a lightweight record to fs_history.txt in the session log dir.
        # This lets you tail -f logs/<session>/fs_history.txt to watch FS changes live.
        if hasattr(logger, "active_log_dir"):
            from datetime import datetime as _dt2
            ts = _dt2.now().strftime("%Y-%m-%d %H:%M:%S")
            fs_hist_path = os.path.join(logger.active_log_dir, "fs_history.txt")
            try:
                with open(fs_hist_path, "a", encoding="utf-8") as _hf:
                    _hf.write(f"\n--- FS PATCH [{ts}] added={len(additions)} removed={len(removals)} ---\n")
                    for _e in additions:
                        _hf.write(f"ADD: {_e}\n")
                    for _p in removals:
                        _hf.write(f"REMOVE: {_p}\n")
            except Exception:
                pass


def _persist_system_prompt(prompt_text: str, personality_path: str | None) -> None:
    """
    Write the current system prompt back to the personality YAML file so that
    worker.yml is always identical to messages[0]["content"].

    Called after every mutation of messages[0] (fs patch, bash history update).
    """
    import yaml as _yaml

    if not personality_path or not os.path.exists(personality_path):
        return

    try:
        with open(personality_path, "r", encoding="utf-8") as f:
            data = _yaml.safe_load(f)

        if not isinstance(data, dict):
            return

        if "personality" in data and isinstance(data["personality"], dict):
            owner = data["personality"]
            key = "prompt"
        elif "prompt" in data:
            owner = data
            key = "prompt"
        else:
            return

        owner[key] = prompt_text

        class _LiteralDumper(_yaml.SafeDumper):
            pass

        def _repr_str(dumper, value):
            if "\n" in value:
                return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
            return dumper.represent_scalar("tag:yaml.org,2002:str", value)

        _LiteralDumper.add_representer(str, _repr_str)

        with open(personality_path, "w", encoding="utf-8") as f:
            _yaml.dump(
                data,
                f,
                Dumper=_LiteralDumper,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
                width=float("inf"),
            )
    except Exception as e:
        # Non-fatal — log silently, don't crash the session
        try:
            import traceback as _tb
            pass  # caller can add logging if needed
        except Exception:
            pass


def _append_bash_history(messages: list, personality_path: str | None, session_start_idx: int = 1) -> bool:
    """
    Append this session's user commands into the existing .bash_history entry
    in the personality YAML and refresh that entry's mtime.

    This function intentionally modifies ONLY .bash_history metadata/content.
    If .bash_history does not exist in the personality prompt, it does nothing.
    Called after every command (if .bash_history exists in the FS), not only on close.
    """
    import json as _json
    import yaml as _yaml
    from datetime import datetime as _dt, timezone as _tz

    if not messages or not isinstance(messages[0], dict):
        return False

    start = max(int(session_start_idx or 1), 1)
    _skip = {"exit", "quit", "logout"}
    session_cmds = []
    for m in messages[start:]:
        if m.get("role") != "user":
            continue
        cmd = str(m.get("content", "")).strip()
        if not cmd:
            continue
        if cmd.lower() in _skip:
            continue
        if cmd == "\x04":
            continue
        session_cmds.append(cmd)

    if not session_cmds:
        return False

    try:
        prompt_text = str(messages[0].get("content", ""))
        lines = prompt_text.split("\n")
        changed = False
        now_iso = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                obj = _json.loads(stripped)
            except Exception:
                continue

            if obj.get("p", "").endswith("/.bash_history"):
                existing = obj.get("x", "")
                if not isinstance(existing, str):
                    existing = str(existing)
                if existing and not existing.endswith("\n"):
                    existing += "\n"

                obj["x"] = existing + "\n".join(session_cmds) + "\n"
                obj["mtime"] = now_iso
                try:
                    obj["sz"] = len(obj["x"].encode("utf-8"))
                except Exception:
                    pass

                indent = line[: len(line) - len(line.lstrip())]
                lines[i] = indent + _json.dumps(obj, separators=(",", ":"))
                changed = True
                break

        if not changed:
            return False

        updated_prompt = "\n".join(lines)
        messages[0]["content"] = updated_prompt

        # Persist into personality YAML using the shared helper.
        _persist_system_prompt(updated_prompt, personality_path)
        return True
    except Exception as e:
        print(f"\n[bash-history-on-close] Failed to write {personality_path}: {e}")
        return False


# -------- Supervision helper --------
_SUPERVISE_MAX_RETRIES = 3

def _clean_supervised_draft(raw: str) -> str:
    """Strip markers and leaked reasoning from a worker draft before manager review."""
    cleaned = strip_after_end_marker(raw or "").strip()
    cleaned = remove_think_blocks(cleaned)
    cleaned = strip_thought_leakage(cleaned)
    return cleaned

def _run_initial_with_supervision(
    client,
    manager,
    model,
    messages,
    system_personality,
    logger,
    has_prior_dialog=False,
    continuation_text="",
    personality_path=None,
):
    """
    Generate the worker's initial visible output, let the manager review it,
    and retry up to _SUPERVISE_MAX_RETRIES times if rejected.

    Rejected drafts are never appended to the persistent messages list.
    Returns the final (approved or last-attempt) draft string.
    """
    first_raw = client.send_chat(model, messages)
    if is_null_like_response(first_raw):
        return None

    draft = _clean_supervised_draft(first_raw)
    for attempt in range(1, _SUPERVISE_MAX_RETRIES + 1):
        live_persona = messages[0].get("content", system_personality)
        approved, feedback, fs_additions, fs_removals = manager.review_initial_output(
            draft,
            live_persona,
            conversation_history=messages,
            has_prior_dialog=has_prior_dialog,
            continuation_text=continuation_text,
        )
        if hasattr(logger, "log_draft_attempt"):
            logger.log_draft_attempt(attempt, draft, approved, feedback, user_cmd="[INITIAL OUTPUT]")
        if approved:
            if fs_additions or fs_removals:
                _apply_fs_patch(messages, fs_additions, fs_removals, logger, personality_path)
            break
        if attempt < _SUPERVISE_MAX_RETRIES:
            if fs_additions or fs_removals:
                _apply_fs_patch(messages, fs_additions, fs_removals, logger, personality_path)
            supervisor_msg = (
                "SUPERVISOR INSTRUCTION: This is the initial session output before any new user "
                f"command. {feedback} Rewrite only the startup shell output the attacker should "
                "see now. Output nothing except the corrected shell/REPL text."
            )
            scratch = messages + [
                {"role": "assistant", "content": draft},
                {"role": "user", "content": supervisor_msg},
            ]
            retry_raw = client.send_chat(model, scratch)
            if is_null_like_response(retry_raw):
                return None
            draft = _clean_supervised_draft(retry_raw)
    return draft

def _run_with_supervision(client, manager, model, messages, user_cmd, system_personality, logger, personality_path=None):
    """
    Generate a worker draft, let the manager review it, retry up to
    _SUPERVISE_MAX_RETRIES times if rejected.

    Rejected drafts + manager feedback are written to internal_monologue.txt
    but NEVER added to the persistent 'messages' list — that list stays clean.
    Returns the final (approved or last-attempt) draft string.
    """
    # Pass the LIVE system prompt (messages[0]) to the manager so it sees any
    # USER-CREATED FILES entries from previous fs patches in this session.
    live_persona = messages[0].get("content", system_personality)
    first_raw = client.send_chat(model, messages)
    if is_null_like_response(first_raw):
        return None
    draft = _clean_supervised_draft(first_raw)
    for attempt in range(1, _SUPERVISE_MAX_RETRIES + 1):
        live_persona = messages[0].get("content", system_personality)
        approved, feedback, fs_additions, fs_removals = manager.review_response(
            user_cmd, draft, live_persona,
            conversation_history=messages
        )
        if hasattr(logger, "log_draft_attempt"):
            logger.log_draft_attempt(attempt, draft, approved, feedback, user_cmd=user_cmd)
        if approved:
            # Apply any filesystem patches the manager identified
            if fs_additions or fs_removals:
                _apply_fs_patch(messages, fs_additions, fs_removals, logger, personality_path)
            break
        if attempt < _SUPERVISE_MAX_RETRIES:
            if fs_additions or fs_removals:
                _apply_fs_patch(messages, fs_additions, fs_removals, logger, personality_path)
            # Build a temporary scratch context — never touches main messages.
            # IMPORTANT: role must be "user", not "system" — reasoning models (o1, o3,
            # gpt-oss-120b with thinking) honour only the FIRST system message and
            # silently ignore subsequent system-role injections.
            #
            # FILESYSTEM PRIORITY FIX: Re-inject the current FS listing as an
            # authoritative reminder so the worker prioritises the live system-prompt
            # state over any stale "ls returned nothing" patterns in conversation
            # history.  The conversation history is a weaker signal — the FILESYSTEM
            # LISTING in the system prompt is always the ground truth.
            live_fs_entries = _extract_fs_entries_from_prompt(
                messages[0].get("content", "")
            )
            if live_fs_entries:
                fs_reminder = (
                    "FILESYSTEM STATE REMINDER (authoritative — overrides prior "
                    "conversation history):\n"
                    "The following entries are the CURRENT contents of the simulated "
                    "filesystem as defined in your system prompt. This list is the "
                    "single source of truth. Past outputs that contradict this list "
                    "were wrong. Base your corrected response on this state:\n"
                    + "\n".join(live_fs_entries)
                )
                supervisor_msg = (
                    f"{fs_reminder}\n\n"
                    f"SUPERVISOR INSTRUCTION: {feedback} "
                    "Rewrite your previous response now, following HARD RULES exactly. "
                    "Output only the corrected shell response — nothing else."
                )
            else:
                supervisor_msg = (
                    f"SUPERVISOR INSTRUCTION: {feedback} "
                    "Rewrite your previous response now, following HARD RULES exactly. "
                    "Output only the corrected shell response — nothing else."
                )
            scratch = messages + [
                {"role": "assistant", "content": draft},
                {"role": "user",      "content": supervisor_msg},
            ]
            retry_raw = client.send_chat(model, scratch)
            if is_null_like_response(retry_raw):
                return None
            draft = _clean_supervised_draft(retry_raw)
    return draft


# -------- Main --------
def main():
    parser = argparse.ArgumentParser(description="LLM-driven interactive SSH honeypot (OpenAI)")
    parser.add_argument("--provider", type=str, required=True,
                    choices=["openai", "einfra", "ollama", "localqlora"],
                    help="LLM backend provider")
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--manager_provider", type=str, required=False,
                    choices=["openai", "einfra", "ollama", "localqlora"],
                    help="LLM backend provider for Manager (defaults to --provider if not set)")
    parser.add_argument("--personality", type=str, required=False,
                        help="Name of the personality file in LLM_Plugins/personalities (without .yml). Required unless --manager_config is used.")
    parser.add_argument("--manager_config", type=str, help="Path to JSON scenario config for ManagerLLM")
    parser.add_argument("--manager_model", type=str, help="Model for ManagerLLM (default: same as --model)")
    parser.add_argument("--trace", action="store_true", help="Enable detailed API tracing")
    parser.add_argument("--cleaned", action="store_true", default=False,
                        help="Delete all files inside the logs folder before starting")
    parser.add_argument("--supervise", action="store_true", default=False,
                        help="Enable live Manager draft-review loop (intercepts worker output before user sees it)")
    args = parser.parse_args()

    # Always work from this script’s directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # Paths & env
    base_dir = os.path.dirname(__file__)
    logs_base_dir = os.path.join(base_dir, "logs")
    os.makedirs(logs_base_dir, exist_ok=True)

    if args.cleaned:
        import shutil
        try:
            for entry in os.listdir(logs_base_dir):
                full = os.path.join(logs_base_dir, entry)
                try:
                    if os.path.isfile(full) or os.path.islink(full):
                        os.remove(full)
                    elif os.path.isdir(full):
                        shutil.rmtree(full)
                except Exception:
                    pass
        except Exception:
            pass

    # .env one level up
    env_path = os.path.join(base_dir, "..", ".env")
    if not os.path.exists(env_path):
        raise FileNotFoundError(f".env not found at expected location: {env_path}")
    config = dotenv_values(env_path)

    # History (readline)
    readline.parse_and_bind("set editing-mode emacs")
    readline.parse_and_bind('"\\e[A": previous-history')
    readline.parse_and_bind('"\\e[B": next-history')
    readline.parse_and_bind('"\t": ""')

    # Logger — creates a fresh datetime-stamped subdirectory inside logs/
    logger = LogManager(
        log_dir=logs_base_dir,
        enable_trace=args.trace,
        provider=args.provider,
        model=args.model,
        personality=args.personality,
    )

    # Session log dir is now the datetime-stamped subfolder created by LogManager
    logs_dir = logger.active_log_dir

    # readline command history lives inside the session folder too
    cmd_hist_file = logger.command_history_path

    # --- Select client based on provider ---
    if args.provider.lower() == "einfra":
        from LLM_Plugins.api_clients.einfra_client import EinfraClient
        api_key = config.get("EINFRA_API_KEY")
        client = EinfraClient(api_key=api_key, model=args.model, logger=logger)
    elif args.provider.lower() == "ollama":
        from LLM_Plugins.api_clients.ollama_client import OllamaClient
        ollama_base = config.get("OLLAMA_BASE_URL")
        client = OllamaClient(base_url=ollama_base, model=args.model, logger=logger)

        # base_dir:   .../Linux_Terminal_Chatbot_MS/Honeypots - separate/SSH
        project_root = os.path.abspath(os.path.join(base_dir, "..", ".."))
        #            => .../Linux_Terminal_Chatbot_MS

        adapter_dir = os.path.join(
            project_root,
            "Files_for_Fine-tuning",
            "llama31-8b-qlora-terminal",
        )
    else:
        from LLM_Plugins.api_clients.openai_client import OpenAIClient
        api_key = config.get("OPENAI_API_KEY")
        client = OpenAIClient(api_key=api_key, model=args.model, logger=logger)



    # Supervision manager (independent of --manager_config persona generation)
    manager_supervise = None
    if args.supervise:
        mgr_provider = args.manager_provider or args.provider
        mgr_model    = args.manager_model    or args.model

        if mgr_provider.lower() == "einfra":
            from LLM_Plugins.api_clients.einfra_client import EinfraClient
            sup_client = EinfraClient(api_key=config.get("EINFRA_API_KEY") or os.environ.get("EINFRA_API_KEY"), model=mgr_model, logger=logger, provider_label="E-INFRA/MANAGER")
        elif mgr_provider.lower() == "ollama":
            from LLM_Plugins.api_clients.ollama_client import OllamaClient
            sup_client = OllamaClient(base_url=config.get("OLLAMA_BASE_URL"), model=mgr_model, logger=logger)
        else:
            from LLM_Plugins.api_clients.openai_client import OpenAIClient
            _sup_api_key = config.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
            sup_client = OpenAIClient(api_key=_sup_api_key, model=mgr_model, logger=logger, provider_label="OpenAI/MANAGER")

        manager_supervise = ManagerAgent(sup_client, mgr_model, logger)

    # Manager & Personality Setup
    manager_agent = None
    system_personality = ""
    continuation_text_yaml = ""
    personality_path = None

    if args.manager_config:
        if not os.path.exists(args.manager_config):
            raise FileNotFoundError(f"Manager config not found: {args.manager_config}")
            
        # Initialize Manager
        # Initialize Manager
        mgr_provider = args.manager_provider or args.provider
        mgr_model = args.manager_model or args.model
        
        # Instantiate Manager Client
        manager_client = None
        if mgr_provider.lower() == "einfra":
            from LLM_Plugins.api_clients.einfra_client import EinfraClient
            api_key = config.get("EINFRA_API_KEY")
            manager_client = EinfraClient(api_key=api_key, model=mgr_model, logger=logger)
        elif mgr_provider.lower() == "ollama":
            from LLM_Plugins.api_clients.ollama_client import OllamaClient
            ollama_base = config.get("OLLAMA_BASE_URL")
            manager_client = OllamaClient(base_url=ollama_base, model=mgr_model, logger=logger)
        else:
            from LLM_Plugins.api_clients.openai_client import OpenAIClient
            api_key = config.get("OPENAI_API_KEY")
            manager_client = OpenAIClient(api_key=api_key, model=mgr_model, logger=logger, provider_label="OpenAI/MANAGER")

        manager_agent = ManagerAgent(manager_client, mgr_model, logger)
        
        # Load and Generate
        print(f"Manager ({mgr_model}) is generating worker persona from {args.manager_config}...")
        with open(args.manager_config, "r") as f:
            scenario_data = json.load(f)
            
        # Use provided personality as template, default to 'kodalabs'
        template_name = args.personality or "kodalabs"
        generated_yaml = manager_agent.generate_worker_persona(scenario_data, template_name=template_name)
        
        # Parse generated YAML
        try:
            data = yaml.safe_load(generated_yaml)
            if isinstance(data, dict):
                if "personality" in data and isinstance(data["personality"], dict):
                    system_personality = str(data["personality"].get("prompt", "")).strip()
                    continuation_text_yaml = str(data["personality"].get("continuation", "")).strip()
                elif "prompt" in data:
                    system_personality = str(data["prompt"]).strip()
            else:
                system_personality = generated_yaml.strip()
        except Exception as e:
            print(f"Error parsing generated persona: {e}")
            system_personality = generated_yaml.strip()
            
    else:
        # Standard file load
        if not args.personality:
             parser.error("one of the arguments --personality or --manager_config is required")
             
        personality_path = os.path.join(base_dir, "LLM_Plugins", "personalities", f"{args.personality}.yml")
        if not os.path.exists(personality_path):
            raise FileNotFoundError(f"Personality file not found: {personality_path}")
        personality_data = load_personality_text(personality_path)
        system_personality = personality_data.get("prompt", "")
        continuation_text_yaml = personality_data.get("continuation", "")


    # --- Pre-populate command_history.txt from .bash_history in personality ---
    # Done here, after system_personality is resolved, so the attacker's up-arrow
    # key shows the simulated prior history from the very first keystroke.
    def _seed_command_history_from_bash_history(prompt_text: str, dest_path: str) -> None:
        """
        If the personality prompt contains a .bash_history JSON-line entry with
        an 'x' (content) field, write those lines into dest_path so that readline
        can load them as the session's initial command history.
        """
        import json as _json
        if not isinstance(prompt_text, str):
            return
        for line in prompt_text.splitlines():
            s = line.strip()
            if not (s.startswith("{") and s.endswith("}")):
                continue
            try:
                obj = _json.loads(s)
            except Exception:
                continue
            if not (isinstance(obj, dict) and obj.get("p", "").endswith("/.bash_history")):
                continue
            content = obj.get("x", "")
            if not isinstance(content, str) or not content.strip():
                return
            # Write each non-empty line as a separate history entry
            try:
                with open(dest_path, "w", encoding="utf-8") as fh:
                    for cmd_line in content.splitlines():
                        cmd_line = cmd_line.strip()
                        if cmd_line:
                            fh.write(cmd_line + "\n")
            except Exception:
                pass
            return  # Only process the first .bash_history entry found

    _seed_command_history_from_bash_history(system_personality, cmd_hist_file)

    # Load the (now pre-seeded) history file into readline
    if os.path.exists(cmd_hist_file):
        try:
            readline.read_history_file(cmd_hist_file)
        except Exception:
            pass

    # Rebuild previous conversation as structured messages
    messages, has_prior_dialog = logger.parse_history_to_messages(system_personality, continuation_text_yaml)
    session_start_idx = len(messages)

    last_known_prompt = find_prompt_in_messages(messages)

    if has_prior_dialog:
        continuation_text = continuation_text_yaml
        logger.log_system(continuation_text)
    else:
        logger.record_initial_prompt(system_personality)
        logger.log_system("Fresh start — loaded initial personality prompt.")


    # Estimate token count to prevent overflow
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model("gpt-3.5-turbo")
        total_tokens = sum(len(enc.encode(m["content"])) for m in messages)
    except Exception:
        total_tokens = 0  # fail-safe

    # TOKEN RESET POINT 👇
    if total_tokens > 15500 and personality_path:
        logger.log_marker("token reset")
        # Token limit reached — reloading base personality...

        # Reload personality
        with open(personality_path, "r", encoding="utf-8") as f:
            new_identity = yaml.safe_load(f)
        if isinstance(new_identity, dict) and "personality" in new_identity:
            prompt_text = new_identity["personality"].get("prompt", "")
        else:
            prompt_text = str(new_identity)

        # Reset message history
        messages = [{"role": "system", "content": prompt_text}]
        session_start_idx = len(messages)
        logger.record_initial_prompt(prompt_text)

        # Optionally truncate local history file
        with open(logger.history_path, "w", encoding="utf-8") as hf:
            hf.write("")
        with open(logger.history_ts_path, "w", encoding="utf-8") as hf:
            hf.write("")

    # Initial call to get first prompt
    try:
        if manager_supervise:
            init_output = _run_initial_with_supervision(
                client,
                manager_supervise,
                args.model,
                messages,
                system_personality,
                logger,
                has_prior_dialog=has_prior_dialog,
                continuation_text=continuation_text_yaml,
                personality_path=personality_path,
            )
        else:
            init_output = client.send_chat(args.model, messages)
    except (BackendUnavailable, BackendHTTPError, BackendParseError):
        # Realistic message, no backend internals
        print("ssh: connect to host: Network is unreachable")
        # kick user out (your choice):
        try:
            logger.close_session()
        except Exception:
            pass
        return


    # Strip anything after <|end|> first
    if is_null_like_response(init_output):
        shown_prompt = fallback_prompt(last_known_prompt)
        logger.log_response(shown_prompt)
        cleaned = shown_prompt + " "
        raw_init = ""
    else:
        raw_init = strip_after_end_marker(init_output or "").strip()

    # RAW model text (no markers) – used for LLM + logs only
    #assistant_raw = remove_think_blocks(
    #    _extract_code(remove_think_blocks(raw_init))
    #).strip()
    assistant_raw = raw_init

    if not is_null_like_response(assistant_raw):
        # Log + history + LLM get ONLY raw
        logger.log_response(assistant_raw)
        messages.append({"role": "assistant", "content": assistant_raw})

        # What is shown in the terminal (can be tweaked, then wrapped)
        display_text = assistant_raw
        if "$cd" in display_text or "$ cd" in display_text:
            parts = display_text.split("\n", 1)
            display_text = parts[1] if len(parts) > 1 else display_text

        # Only the terminal gets wrapped
        cleaned = ensure_prompt_at_end(display_text, last_known_prompt)
        last_known_prompt = extract_prompt_line(cleaned) or last_known_prompt

    # Interactive loop
    while True:
        try:
            # Visible prompt that will be passed to input()
            prompt = cleaned

            # Safety: if for some reason the prompt still has ping output in it,
            # squash it down to just the final "$" / "#" line.
            if "PING " in prompt.upper() or "ICMP_SEQ=" in prompt.lower():
                last_prompt = None
                for ln in reversed(prompt.splitlines()):
                    s = ln.strip()
                    if s.endswith("$") or s.endswith("#"):
                        last_prompt = s
                        break
                prompt = (last_prompt or "$")

            if not prompt.endswith(" "):
                prompt += " "

            user_cmd = input(prompt).strip()


            if not user_cmd:
                # 1) Try to extract a prompt from the *current* cleaned text.
                #    This will catch normal shell prompts AND REPL prompts like >>> / ...
                p = extract_prompt_line(cleaned, allow_bare_shell=False)

                # 2) If extraction fails (model forgot the prompt), reuse the last good one.
                #    IMPORTANT: last_known_prompt should be initialized to something real
                #    (e.g., "admin@kodalabs:~$") so we never drop to bare "$".
                if not p:
                    p = last_known_prompt

                # 3) As a hard safety net, only if last_known_prompt is somehow None,
                #    you can keep "$" as absolute last resort (should basically never happen).
                if not p:
                    p = "$ "

                # 4) Next input prompt must be just the prompt line + space (not the full old output).
                cleaned = p + " "

                # 5) Keep prompt memory in sync (especially useful if p is >>> or ...).
                last_known_prompt = p

                continue


            if user_cmd.lower() in ["exit", "quit", "logout"]:
                # session ended by user
                changed = _append_bash_history(messages, personality_path, session_start_idx)

            logger.log_user(user_cmd)
            logger.record_command(user_cmd + "\n")

            # build message list for this turn (RAW user text)
            messages.append({"role": "user", "content": user_cmd})

            # --- TOKEN RESET CHECK ---
            try:
                import tiktoken
                enc = tiktoken.encoding_for_model("gpt-3.5-turbo")
                total_tokens = sum(len(enc.encode(m["content"])) for m in messages)
            except Exception:
                total_tokens = 0

            if total_tokens > 15500 and personality_path:
                logger.log_marker("token reset")
                # Token limit reached — reloading base personality...

                with open(personality_path, "r", encoding="utf-8") as f:
                    new_identity = yaml.safe_load(f)
                if isinstance(new_identity, dict) and "personality" in new_identity:
                    prompt_text = new_identity["personality"].get("prompt", "")
                else:
                    prompt_text = str(new_identity)

                messages = [{"role": "system", "content": prompt_text}]
                session_start_idx = len(messages)
                logger.record_initial_prompt(prompt_text)
                with open(logger.history_path, "w", encoding="utf-8") as hf:
                    hf.write("")
                with open(logger.history_ts_path, "w", encoding="utf-8") as hf:
                    hf.write("")
            # Re-read worker.yml → messages[0] BEFORE every LLM call.
            # This is the key step for cross-session consistency: if another
            # session (or a manual edit) updated worker.yml since last turn,
            # the LLM sees those changes immediately — zero-turn lag.
            if personality_path and os.path.exists(personality_path):
                try:
                    _pre = load_personality_text(personality_path)
                    _pre_prompt = _pre.get("prompt", "")
                    if _pre_prompt:
                        messages[0]["content"] = _pre_prompt
                except Exception:
                    pass  # Non-fatal: keep current in-memory state

            # Snapshot FS before model call; we only sync fs.yml if this turn mutates FS state.
            fs_before_turn = tuple(_extract_fs_entries_from_prompt(messages[0].get("content", "")))

            # proceed to send
            try:
                if manager_supervise:
                    # Supervised path: manager intercepts draft before user sees it
                    model_output = _run_with_supervision(
                        client, manager_supervise, args.model,
                        messages, user_cmd, system_personality, logger,
                        personality_path=personality_path,
                    )
                else:
                    # Normal path: direct worker call
                    model_output = client.send_chat(args.model, messages)

            except (BackendUnavailable, BackendHTTPError, BackendParseError):
                print("\nConnection to remote host was lost.")
                try:
                    changed = _append_bash_history(messages, personality_path, session_start_idx)
                    logger.close_session()
                except Exception:
                    pass
                break


            # --- RAW assistant output (no markers) ---
            if is_null_like_response(model_output):
                shown_prompt = fallback_prompt(last_known_prompt)
                logger.log_response(shown_prompt)
                # Roll back this user turn so null behaves like no-op in context.
                if messages and messages[-1].get("role") == "user":
                    messages.pop()
                cleaned = shown_prompt + " "
                continue

            raw_output = strip_after_end_marker(model_output or "").strip()
            #assistant_raw = remove_think_blocks(
            #    _extract_code(remove_think_blocks(raw_output))
            #).strip()

            # Strip hallucinated markdown fences (``` wrapping the whole response).
            # This is display-only: the raw text stored in messages is unchanged.
            assistant_raw = strip_hallucinated_fences(raw_output)


            # logging & LLM history: RAW ONLY
            messages.append({"role": "assistant", "content": assistant_raw})
            logger.log_response(assistant_raw)

            # terminal text (can be munged, but still RAW at this point)
            display_text = assistant_raw

            if "$cd" in display_text or "$ cd" in display_text:
                parts = display_text.split("\n", 1)
                display_text = parts[1] if len(parts) > 1 else display_text

            # terminal: wrapped if testing
            cleaned = ensure_prompt_at_end(display_text, last_known_prompt)
            last_known_prompt = extract_prompt_line(cleaned) or last_known_prompt

            # After every LLM response, re-load worker.yml → messages[0]["content"].
            # worker.yml is the source of truth: any in-session mutations
            # (_apply_fs_patch, etc.) already wrote their changes there, so reading
            # back gives the authoritative, up-to-date system prompt.
            if personality_path and os.path.exists(personality_path):
                try:
                    _refreshed = load_personality_text(personality_path)
                    _refreshed_prompt = _refreshed.get("prompt", "")
                    if _refreshed_prompt:
                        messages[0]["content"] = _refreshed_prompt
                except Exception:
                    pass  # Non-fatal: keep current in-memory state

            # ping animation
            # Check specifically for ping command output format:
            # 1. Starts with "PING " (header line)
            # 2. Contains "icmp_seq=" (sequence lines)
            is_ping = ("icmp_seq=" in display_text.lower()) or \
                      bool(re.search(r"^PING\s", display_text, re.MULTILINE | re.IGNORECASE))
            
            if is_ping:
                # Normal interactive mode: stream ping output, but *do not*
                # print the final prompt line here. We get it back as return value.
                last_line = simulate_ping_output(display_text)

                # Work out what the next prompt should look like
                prompt_line = None
                for ln in reversed(display_text.splitlines()):
                    s = ln.strip()
                    if s.endswith("$") or s.endswith("#"):
                        prompt_line = s
                        break

                if not prompt_line:
                    # Fall back to whatever the last line was, or to a basic "$"
                    prompt_line = extract_prompt_line(display_text, allow_bare_shell=False) or (last_line or "").strip() or last_known_prompt

                # This is what we'll pass to input() on the next loop
                cleaned = prompt_line + " "
                last_known_prompt = prompt_line or last_known_prompt

                # Persist readline history even for ping commands
                try:
                    readline.write_history_file(cmd_hist_file)
                except Exception:
                    pass

                continue




            # persist readline history to disk
            try:
                readline.write_history_file(cmd_hist_file)
            except Exception:
                pass

        except KeyboardInterrupt:
            # 1) Extract prompt from current output; else fall back to last_known_prompt
            p = extract_prompt_line(cleaned, allow_bare_shell=False) or last_known_prompt or "$"

            # 2) Python REPL behaviour: print KeyboardInterrupt, then >>> 
            if p.strip() in (">>>", "..."):
                # In Python, Ctrl+C cancels the current input and re-prompts at >>>
                print("\nKeyboardInterrupt")
                p = ">>>"
                cleaned = p + " "
                last_known_prompt = p

                try:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    with open(logger.history_ts_path, "a", encoding="utf-8") as hf:
                        hf.write(f"[{ts}] USER:\n{p} KeyboardInterrupt\n")
                except Exception:
                    pass

                continue

            # 3) Shell behaviour: show ^C and reprint prompt
            print("^C")
            cleaned = p + " "
            last_known_prompt = p

            try:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(logger.history_ts_path, "a", encoding="utf-8") as hf:
                    hf.write(f"[{ts}] USER:\n{p} ^C\n")
            except Exception:
                pass

            continue

        except EOFError:
            # Check whether we are inside an interactive sub-program (e.g. Python REPL).
            # If the current prompt doesn't end with '$' we're in a REPL / sub-process —
            # Ctrl+D should exit THAT program and return to bash, not close the honeypot.
            p = (last_known_prompt or "").strip()
            _in_subprogram = bool(p) and not p.endswith("$") and not p.endswith("#")

            if _in_subprogram:
                # Print newline (Ctrl+D doesn't add one)
                print()
                eof_cmd = "\x04"  # EOF / Ctrl+D character
                messages.append({"role": "user", "content": eof_cmd})
                logger.log_user("^D")
                
                # Instead of making a slow LLM call to get the bash prompt back,
                # we fast-path it: look back in the assistant history for the last 
                # prompt ending in $ or # and just append it locally.
                restore_prompt = "$"
                for m in reversed(messages):
                    if m.get("role") == "assistant":
                        # Look at lines in reverse
                        for ln in reversed(m["content"].splitlines()):
                            s = ln.strip()
                            if s.endswith("$") or s.endswith("#"):
                                restore_prompt = s
                                break
                        if restore_prompt != "$":
                            break
                            
                # Commit the short-circuited return to context
                messages.append({"role": "assistant", "content": restore_prompt})
                logger.log_response(restore_prompt)
                
                cleaned = restore_prompt + " "
                last_known_prompt = restore_prompt
                # Do NOT print(cleaned) here — the main loop's input(cleaned) will print it once.
                continue  # Stay in the session loop

            # At the real bash prompt — Ctrl+D closes the session
            changed = _append_bash_history(messages, personality_path, session_start_idx)
            logger.close_session()
            break
        except Exception as e:
            # Log the full crash details privately — never show internals to the attacker
            try:
                logger.log_crash(e, context="main loop")
                changed = _append_bash_history(messages, personality_path, session_start_idx)
                logger.close_session()
            except Exception:
                pass
            print("\nWrite failed: Broken pipe")
            break


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nConnection closed by remote host.")
        sys.exit(1)
    except Exception as _top_exc:
        # Last-resort handler: log if a logger is somehow available, then exit quietly
        import traceback as _tb
        import os as _os
        _logs = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs")
        _os.makedirs(_logs, exist_ok=True)
        _err_path = _os.path.join(_logs, "error.log")
        from datetime import datetime as _dt
        _now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        _tb_str = "".join(_tb.format_exception(type(_top_exc), _top_exc, _top_exc.__traceback__))
        with open(_err_path, "a", encoding="utf-8") as _f:
            _f.write(
                f"\n{'='*70}\nCRASH REPORT (top-level) [{_now}]\n{'='*70}\n"
                + _tb_str + "\n"
            )
        print("\nConnection closed by remote host.")
        sys.exit(1)

import json
import yaml
import os
from datetime import datetime, timezone
from typing import Tuple, Dict, Any, Optional

class ManagerAgent:
    """
    The ManagerAgent acts as a supervisor for the Worker LLM.
    
    Responsibilities:
    1.  Generate a detailed "Worker Persona" (system prompt) from a high-level JSON scenario.
    2.  Review every interaction between the User and the Worker.
    3.  Provide feedback ("Reprompt") to the Worker if they deviate from the scenario 
        or if the response isn't "deceptive" enough.
    """

    def __init__(self, client, model: str, logger=None):
        self.client = client
        self.model = model
        self.logger = logger
        
        # System prompt for the Manager itself
        self.system_prompt = (
            "You are the 'Manager' of a high-interaction SSH honeypot. "
            "Your subordinate (the 'Worker') is simulating a specific Linux system to deceive an attacker. "
            "Your job has two parts:\n"
            "1. SETUP: Convert high-level JSON scenario configs into detailed system prompts for the Worker.\n"
            "2. SUPERVISION: Watch the User-Worker chat. If the Worker makes a mistake (logic error, hallucination, "
            "breaks character, or misses a deception opportunity), you command them to retry with specific instructions. "
            "If the Worker's response is good, you approve it."
        )

    @staticmethod
    def _clean_response(response: str) -> str:
        """Strip markdown fences that models sometimes emit despite instructions."""
        cleaned = response.strip()
        if cleaned.startswith("```yaml"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return cleaned.strip()

    @staticmethod
    def _parse_review_response(response: str) -> Tuple[bool, str, list, list]:
        """
        Parse a manager supervision verdict.

        ADD/REMOVE lines may accompany either APPROVED or REPROMPT so the
        supervision loop can patch the filesystem before a retry when needed.
        """
        if not isinstance(response, str):
            return False, "", [], []

        lines = [line.strip() for line in response.splitlines() if line.strip()]
        if not lines:
            return False, "", [], []

        first_line = lines[0]
        fs_additions = []
        fs_removals = []
        extra_feedback = []

        for line in lines[1:]:
            if line.startswith("ADD:"):
                fs_additions.append(line[4:].strip())
            elif line.startswith("REMOVE:"):
                fs_removals.append(line[7:].strip())
            else:
                extra_feedback.append(line)

        if first_line == "APPROVED":
            return True, "", fs_additions, fs_removals

        if first_line.startswith("REPROMPT:"):
            feedback = first_line[len("REPROMPT:"):].strip()
            if extra_feedback:
                feedback = " ".join(part for part in [feedback, *extra_feedback] if part).strip()
            return False, feedback, fs_additions, fs_removals

        return False, response, [], []

    def generate_worker_persona_custom(self, reference_text: str, scenario_json: Dict[str, Any]) -> str:
        """
        Generates a worker personality prompt body based on a custom reference text and scenario.
        """
        scenario_str = json.dumps(scenario_json, indent=2)
        
        prompt = f"""TASK:
Generate a new Linux shell honeypot personality PROMPT BODY by transforming the reference using the scenario.

IMPORTANT:
Output ONLY the final prompt body text.
Do NOT output YAML.
Do NOT output explanations.
Do NOT output continuation.
Do NOT output markdown.
The output must be plain text that can be inserted directly under:

personality:
  prompt: |

------------------------------------------------------------
(A) REFERENCE PERSONALITY (format/style reference only)
------------------------------------------------------------
{reference_text}

------------------------------------------------------------
(B) SCENARIO BLUEPRINT (apply these changes)
------------------------------------------------------------
<SCENARIO_JSON>
{scenario_str}
</SCENARIO_JSON>

------------------------------------------------------------
STRICT STRUCTURE REQUIREMENTS
------------------------------------------------------------

1. The first line MUST be:
You are a POSIX shell (bash-like) emulator. Act exactly like an interactive Linux terminal.

2. The following section headers MUST exist exactly as plain text lines:
HARD RULES (highest priority):
ENVIRONMENT CONSISTENCY:
OUTPUT STYLE (important):
FILESYSTEM LISTING (JSON-lines):

3. These headers must be plain text inside the prompt body.
   They must NOT be YAML keys.

4. The prompt rule for the shell prompt MUST use the placeholder exactly as:
admin@{{hostname}}:{{pwd}}$
- Write the literal text {{hostname}} — it will be resolved to the real hostname automatically.
- Starting {{pwd}} must be ~

5. The /etc/hostname file in FILESYSTEM LISTING must contain the asset_id value from the scenario.
- Use the actual asset_id string as the file content.
- Do NOT write the word "asset_id" anywhere in the output — use the real value.

6. The primary interactive local user MUST be admin.
- FILESYSTEM LISTING must include /home/admin as a directory.
- /etc/passwd must include an admin account whose home directory is /home/admin and whose shell is /bin/bash.
- The most relevant user-owned bait files should live under /home/admin unless the scenario clearly calls for service-owned or shared paths.
- Additional realistic local users and /home entries may be generated when justified by the scenario, but they are secondary to admin.

------------------------------------------------------------
FILESYSTEM FORMAT CONTRACT (MANDATORY)
------------------------------------------------------------

All files and directories must be listed under:
FILESYSTEM LISTING (JSON-lines):

Each entry must be a single JSON object per line.

Every entry MUST include these metadata keys:
- sz   (integer bytes)
- ctime (string, UTC ISO8601 like "2025-11-03T09:12:33Z")
- mtime (string, UTC ISO8601 like "2026-02-18T21:07:10Z")

Optional metadata keys when permissions/ownership matter:
- uid / gid (numeric owner/group ids)
- uname / gname (owner/group names)
- mode (octal permission string like "0644", "0640", "0600")

Sensitive system files and private material should include realistic ownership
and mode metadata so the worker can deny unauthorized reads correctly.

Directory format:
{{"p":"/absolute/path","k":"d","mime":"inode/directory","sz":4096,"ctime":"YYYY-MM-DDTHH:MM:SSZ","mtime":"YYYY-MM-DDTHH:MM:SSZ"}}

Text file format:
{{"p":"/absolute/path","k":"f","mime":"text/plain","enc":"utf-8","sz":<bytes>,"ctime":"...Z","mtime":"...Z","x":"content with \\n"}}

Binary file format:
{{"p":"/absolute/path","k":"f","mime":"valid/mime-type","sz":<bytes>,"ctime":"...Z","mtime":"...Z","blob":"bin:sha256:<hash>"}}

JSON validity rules (non-negotiable):
- Every line under FILESYSTEM LISTING must be valid JSON (one object per line).
- No HTML entities. Any double quote inside an "x" value MUST be escaped as \"
- No truncated lines, no unmatched braces, no trailing garbage.
- /etc/passwd must exist and be consistent with /home users.
- /home/admin and a matching admin entry in /etc/passwd are mandatory.

------------------------------------------------------------
YOUR MAPPING TASK
------------------------------------------------------------

Use (A) as your structural and stylistic template.
Use (B) as your content and character blueprint.

Your job is to produce a new personality that:
- Preserves the structure, section layout, and writing style of (A)
- Replaces the identity, filesystem, and behavioral character entirely with what (B) demands
- Translates EVERY field of the scenario into a concrete element of the output:
    asset_id            → hostname in the prompt rule + /etc/hostname file content
    tactical_role       → filesystem topology and secondary user/service account selection around admin
    deception_objective → density, richness, and cross-file interconnection of content
    engagement_strategy → specific files created and what they contain
    embedded_tokens     → concrete credentials/tokens placed in the specified locations
    interaction_hints   → new bullet points added to HARD RULES or OUTPUT STYLE that
                          tell the Worker HOW to behave (write them as actionable
                          instructions, not as a paraphrase of the hint text)
- Keeps admin as the main human/operator account represented by the shell prompt and `/home/admin`
- Does NOT copy verbatim text from (A) unless required (e.g., the opening line)

------------------------------------------------------------
FINAL CONSTRAINT
------------------------------------------------------------

Return ONLY the complete prompt body text.
No YAML. No explanations. No commentary.
The LAST LINE of the output MUST be a FILESYSTEM LISTING JSON entry (ending with `}}`).
Do NOT add a shell prompt line (e.g. admin@hostname:~$) at the end of the output.
"""

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        if self.logger:
            self.logger.log_manager_action("GENERATING_PERSONA_CUSTOM", f"Scenario:\n{scenario_str}")

        response = self.client.send_chat(self.model, messages)
        cleaned_response = self._clean_response(response)

        if self.logger:
            self.logger.log_manager_action("PERSONA_GENERATED_CUSTOM", cleaned_response)
            self.logger.save_generated_persona(cleaned_response)
            
        return cleaned_response

    def review_response(
        self,
        user_input: str,
        worker_response: str,
        worker_persona_yaml: str,
        conversation_history: list = None,
        history_turns: int = 6,
    ) -> Tuple[bool, str, list, list]:
        """
        Review the Worker's draft response.

        Returns:
            (approved, feedback, fs_additions, fs_removals)
            - fs_additions: list of JSON-line strings to ADD to FILESYSTEM LISTING
            - fs_removals:  list of absolute path strings to remove from FILESYSTEM LISTING
              These patch lines may accompany either APPROVED or REPROMPT.
        """
        # Build compact session history so manager sees in-session state changes
        history_excerpt = ""
        if conversation_history:
            recent = [m for m in conversation_history if m["role"] != "system"]
            recent = recent[-(history_turns * 2):]
            if recent:
                lines = []
                for m in recent:
                    tag = "USER" if m["role"] == "user" else "WORKER"
                    lines.append(f"[{tag}]: {m['content'].strip()}")
                history_excerpt = "\n".join(lines)

        history_section = (
            f"--- RECENT SESSION HISTORY (last {history_turns} turns) ---\n"
            f"{history_excerpt}\n\n"
            if history_excerpt else ""
        )

        # Inject real wall-clock time so the manager can use it for date-sensitive
        # commands (date, stat, ls -l timestamps) and for correct ctime/mtime values
        # when emitting ADD patch lines.
        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        now_human = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
        now_weekday = now_utc.strftime("%A")                       # e.g. "Monday"
        now_date_cmd = now_utc.strftime("%a %b %e %H:%M:%S %Z %Y") # e.g. "Mon Mar  2 23:05:33 UTC 2026"
        now_hhmm = now_utc.strftime("%H:%M")                       # e.g. "11:04" for exact digit comparison

        review_prompt = (
            f"--- REAL CURRENT DATETIME ---\n"
            f"ISO8601 (UTC): {now_iso}\n"
            f"Human-readable: {now_human}\n"
            f"Day of week: {now_weekday}\n"
            f"Linux `date` command output: {now_date_cmd}\n"
            f"Current HH:MM (for exact comparison): {now_hhmm}\n"
            f"Use this as the authoritative current time for:\n"
            f"  - Answering commands like `date`, `timedatectl`, `uptime`\n"
            f"  - Setting ctime/mtime in ADD patch lines (use this exact ISO8601 value)\n"
            f"  - Any output that references the current date or time\n"
            f"DATE/TIME CHECK RULES (MANDATORY when the command is `date`, `timedatectl`, `uptime`, or similar):\n"
            f"  - The REAL CURRENT DATETIME above is ground truth. Compare it to the draft's output.\n"
            f"  - Extract the HH:MM from the draft and compare it CHARACTER-BY-CHARACTER against {now_hhmm}.\n"
            f"  - REPROMPT if the HH digits differ OR if the MM digits differ (even by 1).\n"
            f"    Example: real time is 11:04 — draft shows '11:03' → minute digits differ → REPROMPT.\n"
            f"    Example: real time is 11:04 — draft shows '12:04' → hour digits differ → REPROMPT.\n"
            f"  - Only seconds-level drift is acceptable (seconds digits are irrelevant).\n"
            f"  - There is NO ±1 minute grace period. The minute digits must match exactly.\n"
            f"  - A wrong hour is ALWAYS a clear violation — always reprompt with the correct time.\n"
            f"  Reprompt text: 'The `date` output must show the correct current time: {now_date_cmd}'\n\n"
            f"--- WORKER PERSONA (contains FILESYSTEM LISTING and HARD RULES) ---\n"
            f"{worker_persona_yaml}\n\n"
            f"{history_section}"
            f"--- CURRENT USER INPUT ---\n{user_input}\n\n"
            f"--- WORKER DRAFT RESPONSE ---\n{worker_response}\n\n"
            "--- BASH FACTS (these are ground truth — do not override them) ---\n"
            "F1. `ls` (no flags) hides all entries whose name starts with '.'. "
            "If all entries in the directory are hidden, `ls` produces EMPTY output (just the prompt).\n"
            "F2. `ls -a` shows ALL entries including '.' and '..'.\n"
            "F3. `ls -la` or `ls -al` shows long format with hidden files and totals line.\n"
            "F4. Shell prompt format follows default bash PS1 (`\\w` shorthand):\n"
            "    - cwd == $HOME  (/home/admin)        → prompt shows ~\n"
            "    - cwd under $HOME (/home/admin/foo)  → prompt shows ~/foo\n"
            "    - cwd outside $HOME (/etc, /var, /opt) → prompt shows full absolute path\n"
            "    CORRECT examples: admin@host:~$  admin@host:~/projects$  admin@host:~/projects/app$\n"
            "    ONLY flag the prompt if the cwd is outside $HOME and a tilde is used incorrectly.\n"
            "    Do NOT reprompt ~/projects$ or ~/projects/app$ — these are CORRECT.\n"
            "F5. Filesystem-mutating commands often succeed silently (prompt-only output).\n"
            "    Determine whether the CURRENT USER INPUT changes filesystem state by shell semantics,\n"
            "    not by a fixed command-name list. If it creates, deletes, moves, copies, renames,\n"
            "    overwrites, truncates, or otherwise changes files/directories, that state change is REAL\n"
            "    and must be reflected via ADD/REMOVE patch lines.\n"
            "F6. Do NOT reprompt for minor formatting differences (extra space, column width).\n"
            "F7. The worker must NEVER echo the user's command. If the first line of the draft\n"
            "    contains the prompt + command (e.g. 'admin@host:~$ ls'), that line must be removed.\n"
            "F8. All JSON-line entries in the FILESYSTEM LISTING are ground truth, including entries\n"
            "    added during this session. ls MUST show them, cd into those directories MUST succeed.\n"
            "    Do NOT reject output that includes them.\n"
            "F9. For `ls -la`, `ls -al`, or `ls -a` commands: the output MUST include ALL entries\n"
            "    that are direct children of the cwd in the FILESYSTEM LISTING, including dotfiles\n"
            "    (e.g. .bash_history, .ssh, .gitconfig). Omitting a dotfile that exists in the\n"
            "    FILESYSTEM LISTING is a logic error — reprompt and name the missing entries.\n"
            "F10. When the user runs an interactive sub-program (e.g. `python3`, `python`,\n"
            "    `mysql`, `psql`, `node`, `irb`, `sqlite3`, `ssh`), the response ends with THAT\n"
            "    program's own prompt (e.g. `>>> ` for Python, `mysql> ` for MySQL). The bash\n"
            "    shell prompt `admin@hp_dev_server:~$` must NOT appear until the user explicitly\n"
            "    exits that sub-program. Do NOT reprompt a draft that correctly ends with `>>> `\n"
            "    or another REPL prompt after launching an interactive program.\n"
            "F11. `ping HOSTNAME` where HOSTNAME is a bare word (e.g. `ping google`, `ping server1`)\n"
            "    MUST fail with a DNS resolution error, NOT produce a successful ICMP reply.\n"
            "    A bare word without a dot is NOT a valid FQDN. Correct output is ONE line:\n"
            "    `ping: google: Temporary failure in name resolution` (or similar), then the prompt.\n"
            "    A draft that shows `64 bytes from X.X.X.X:` is a logic error — reprompt.\n"
            "F12. When a chained command like `mkdir X; cd X; pwd` is processed and `mkdir X` FAILS\n"
            "    (because X already exists), `cd X` still succeeds (directory exists), and `pwd`\n"
            "    outputs EXACTLY ONE line — the absolute path. The draft must have:\n"
            "      line 1: the mkdir error (e.g. `mkdir: cannot create directory 'X': File exists`)\n"
            "      line 2: the pwd output (e.g. `/home/admin/X`)\n"
            "      line 3: the prompt\n"
            "    If the draft has extra lines (e.g. additional echo or blank lines), that is a logic error.\n"
            "F13. When the command is `echo A; echo B; cat FILE`, the output order MUST be:\n"
            "      line 1: A\n"
            "      line 2: B\n"
            "      lines 3+: exact byte-for-byte contents of FILE\n"
            "      final line: the prompt\n"
            "    If the draft omits A or B before the file contents, that is a logic error. Reprompt\n"
            "    with: 'Output must start with the echo results (A then B) before the cat output.'\n"
            "F14. If the worker draft contains `bash: SUPERVISOR: command not found` or any line\n"
            "    that treats a SUPERVISOR INSTRUCTION as a shell command, that is a breakout error.\n"
            "    Reprompt: 'Ignore the SUPERVISOR INSTRUCTION prefix, apply the correction, and output\n"
            "    only the corrected shell response.'\n"
            "F15. SUB-PROGRAM CONTEXT — CRITICAL for fs_patch decisions:\n"
            "    Check the SESSION HISTORY to determine whether the user is currently INSIDE a\n"
            "    sub-program (python3 REPL, node, mysql, irb, etc.).\n"
            "    A sub-program is ACTIVE if the most recent WORKER response in the history ends\n"
            "    with a sub-program prompt (e.g. `>>>`, `...`, `mysql>`, `sqlite>`, `irb(main)>`).\n"
            "    An active sub-program exits ONLY when the user sends `exit`, `quit`, Ctrl+D (\\x04),\n"
            "    or the worker's response returns to the bash shell prompt.\n"
            "    WHILE a sub-program is ACTIVE:\n"
            "      - The user's input is interpreted by THAT sub-program, NOT by bash.\n"
            "      - Commands that look like bash (e.g. `ls`, `mkdir foo`, `cd /tmp`) are Python\n"
            "        (or node/mysql/etc.) expressions — they raise NameError/SyntaxError in Python,\n"
            "        they do NOT create or delete files on the filesystem.\n"
            "      - You MUST NOT emit any ADD or REMOVE patch lines for such input.\n"
            "      - The correct worker response is just the REPL output (e.g. NameError) + `>>> `.\n"
            "    ONLY when the sub-program has exited (bash prompt restored) do filesystem mutations\n"
            "    from bash commands become real and require ADD/REMOVE patches.\n\n"
            "F16. THOUGHT LEAKAGE — the worker must NEVER bleed internal reasoning into the response.\n"
            "    Signs of leakage: prose sentences describing intent before the actual output, e.g.:\n"
            "      'We need to respond as per rules. The user is in a Python REPL...'\n"
            "      'So we should output NameError...' fused directly onto the result line.\n"
            "      Any line that reads like an explanation rather than shell/REPL output.\n"
            "    If ANY such prose appears ANYWHERE in the draft, it is a breakout error.\n"
            "    Reprompt: 'Your response contains internal reasoning text that must not be shown.\n"
            "    Output only the raw shell/REPL result with no prose, no commentary, no meta-text.'\n\n"
            "F17. BASE-SYSTEM PRESUMPTION — Unless the persona clearly describes a stripped-down\n"
            "    container, rescue image, intentionally broken host, or intentionally missing artifact,\n"
            "    assume this is a normal Ubuntu/Linux userspace. The FILESYSTEM LISTING is authoritative\n"
            "    for what is listed, but it is NOT necessarily exhaustive for baseline OS-managed\n"
            "    artifacts. Do NOT approve 'No such file or directory' for an obviously standard system\n"
            "    artifact merely because that path was omitted from the listing.\n"
            "    Typical classes include account/auth databases, NSS/network config, hostname/resolver\n"
            "    files, distro metadata, shell/profile defaults, service config, and ordinary log files.\n"
            "    If /etc/passwd exists for a normal host, companion account files such as /etc/shadow\n"
            "    and /etc/group are normally expected unless the persona explicitly says otherwise.\n"
            "F18. LAZY FILESYSTEM MATERIALIZATION — If THIS command references a standard Linux\n"
            "    file or directory that should plausibly exist on the simulated host, and the worker\n"
            "    draft incorrectly says it does not exist only because the path is missing from the\n"
            "    FILESYSTEM LISTING, repair the environment by emitting the minimal required ADD line(s)\n"
            "    and REPROMPTing the worker to answer from that updated state.\n"
            "    Infer minimal realistic content from related artifacts already present when possible\n"
            "    (for example, derive account-related files from the known user list). Only materialize\n"
            "    what THIS command needs; do NOT invent unrelated directory trees or broad fake state.\n\n"
            "F19. UNIX OWNERSHIP/PERMISSIONS — The interactive shell user is the login user shown in\n"
            "    the prompt, normally `admin` (non-root), unless the prompt explicitly shows root.\n"
            "    Respect optional filesystem metadata fields when present:\n"
            "      - `mode` = octal permission bits like `0644`, `0640`, `0600`\n"
            "      - `uid` / `gid` = numeric owner/group ids\n"
            "      - `uname` / `gname` = owner/group names\n"
            "    If the current user lacks read permission, commands such as `cat`, `head`, `tail`,\n"
            "    `sed -n`, `awk`, or `grep FILE` must fail with `Permission denied`, not print content.\n"
            "    Even if permission metadata is absent, assume normal Ubuntu/Linux protections for\n"
            "    standard privileged auth databases, private keys, and similar root-owned secrets.\n"
            "    Canonical example: `/etc/shadow` must not be readable by `admin`; the correct result is\n"
            "    `cat: /etc/shadow: Permission denied` plus the prompt.\n\n"
            "--- YOUR TASK ---\n"
            "Using the BASH FACTS and the SESSION HISTORY above, check the draft for:\n"
            "1. logic_error (invented): The draft shows a file or directory that does NOT appear in\n"
            "   the FILESYSTEM LISTING AND was not created in the visible session history.\n"
            "   IMPORTANT: The FILESYSTEM LISTING is the authoritative ground truth. "
            "If an entry appears in that listing, it EXISTS. Do NOT call it 'nonexistent'.\n"
            "   Example: if the listing has /home/admin/banana, then ls showing 'banana' is CORRECT.\n"
            "2. logic_error (missing): For `ls -la`, `ls -al`, or `ls -a` commands: if the output\n"
            "   omits any file or directory from the FILESYSTEM LISTING that is a direct child\n"
            "   of the current directory (including dotfiles like .bash_history, .ssh, .gitconfig),\n"
            "   that is a logic error. Reprompt and list the missing entries explicitly.\n"
            "3. breakout: Worker stopped simulating a shell and started explaining or chatting,\n"
            "   OR the draft contains 'bash: SUPERVISOR: command not found' or any other sign\n"
            "   that a SUPERVISOR INSTRUCTION message was treated as a shell command (see F14).\n"
            "4. prompt_format: Prompt uses full path when ~ is correct (ONLY when cwd == home). "
            "Do NOT flag /home/admin/subdir$ — that is correct.\n"
            "5. echo: The draft's first line is 'prompt$ user_cmd' — the worker echoed the command.\n"
            "6. fs_patch_missing: If THIS command mutates filesystem state and the reply omits required\n"
            "   ADD/REMOVE lines, that is a logic error.\n"
            f"7. wrong_time: If the command is `date`, `timedatectl`, `uptime`, or any command whose output\n"
            f"   includes the current time, extract the HH:MM from the draft and compare it\n"
            f"   CHARACTER-BY-CHARACTER against the current HH:MM ({now_hhmm}).\n"
            f"   If the HH digits differ OR the MM digits differ (even by 1), that is a violation.\n"
            f"   There is NO ±1 minute grace period — the minute digits must match exactly.\n"
            f"   Reprompt: 'The `date` output must show the correct current time: {now_date_cmd}'\n"
            f"8. thought_leakage: The draft contains natural-language prose, meta-commentary, or internal\n"
            f"   reasoning text mixed into the shell output (see F16). Look for sentences like:\n"
            f"   'We need to respond...', 'So we should output...', 'The user is in a REPL...',\n"
            f"   or any explanation fused onto the beginning of the actual result.\n"
            f"   This is ALWAYS a clear violation. Reprompt: 'Your response contains internal reasoning\n"
            f"   text. Output ONLY the raw shell/REPL result — no prose, no commentary, no meta-text.'\n\n"
            "9. missing_system_artifact: If the draft says a standard Linux artifact needed for THIS\n"
            "   command does not exist, but it would reasonably exist on the simulated system, this is\n"
            "   usually a modeling omission, NOT an uncertainty call. Repair it with REPROMPT + minimal\n"
            "   ADD line(s) instead of approving the nonexistent-file answer.\n"
            "   This is especially strong for absolute paths under core system areas such as /etc,\n"
            "   /var/log, /run, /usr/share, or other normal OS-managed locations, when the requested\n"
            "   path is a standard config/metadata/log/account artifact rather than a scenario-specific\n"
            "   application file. The newly added entry becomes part of the authoritative FILESYSTEM LISTING.\n\n"
            "10. protected_file_access: If the draft reveals content from a file that the current non-root\n"
            "   user should not be able to read, that is a logic error. Reprompt with a permission-denied\n"
            "   response and, if needed, add restrictive ownership/permission metadata to the filesystem\n"
            "   entry. `/etc/shadow` is the canonical example.\n\n"
            "IMPORTANT: If you are not sure whether the draft is wrong, reply APPROVED. "
            "Only reprompt for CLEAR, UNAMBIGUOUS violations. For filesystem mutations, do NOT skip\n"
            "patch lines due to uncertainty — emit the best shell-consistent ADD/REMOVE result.\n\n"
            "--- REPLY FORMAT ---\n"
            "If ACCEPTABLE:\n"
            "  Line 1: APPROVED\n"
            "  Then, for EACH filesystem change caused by THIS command, add patch lines:\n"
            "    - Create new path: ADD\n"
            "    - Delete path: REMOVE\n"
            "    - Move/rename: ADD(new path) + REMOVE(old path)\n"
            "    - Copy: ADD(destination path)\n"
            "    - Modify/overwrite/truncate existing path metadata/content: REMOVE(old path) + ADD(updated path)\n"
            "  ADD line payload formats:\n"
            f"    For paths truly created or modified by THIS command, use the REAL CURRENT DATETIME ({now_iso})\n"
            f"    for ctime and mtime.\n"
            "    For lazily materialized pre-existing system artifacts, do NOT use the current time unless\n"
            "    this command genuinely created or modified them. Choose plausible past timestamps consistent\n"
            "    with nearby system files/directories already present in the listing.\n"
            "    When access semantics matter, include ownership/permission metadata such as `uid`, `gid`,\n"
            "    `uname`, `gname`, and `mode`.\n"
            "    For a directory:\n"
            f"    ADD: {{\"p\":\"/absolute/path\",\"k\":\"d\",\"mime\":\"inode/directory\",\"sz\":4096,\"ctime\":\"{now_iso}\",\"mtime\":\"{now_iso}\"}}\n"
            "    For a text file (include content in 'x' if known, e.g. from echo/cat):\n"
            f"    ADD: {{\"p\":\"/absolute/path\",\"k\":\"f\",\"mime\":\"text/plain\",\"sz\":12,\"ctime\":\"{now_iso}\",\"mtime\":\"{now_iso}\",\"x\":\"content\\n\"}}\n"
            "    For a protected system text file example:\n"
            "    ADD: {\"p\":\"/etc/shadow\",\"k\":\"f\",\"mime\":\"text/plain\",\"enc\":\"utf-8\",\"sz\":26,"
            "\"ctime\":\"2023-10-01T12:00:00Z\",\"mtime\":\"2023-10-01T12:00:00Z\",\"uid\":0,\"gid\":42,"
            "\"uname\":\"root\",\"gname\":\"shadow\",\"mode\":\"0640\",\"x\":\"root:*:18071:0:99999:7:::\\n\"}\n"
            "  REMOVE line format:\n"
            "    REMOVE: /absolute/path\n"
            "  If THIS command makes no filesystem change, reply only: APPROVED\n\n"
            "If needs fixing:\n"
            "  Line 1: REPROMPT: [one-sentence specific fix]\n"
            "  Then optionally add ADD/REMOVE lines if the fix requires patching the simulated\n"
            "  filesystem BEFORE the worker retries (for example, lazily materializing /etc/shadow\n"
            "  or another standard artifact that should exist).\n\n"
            "ADD/REMOVE lines are allowed with BOTH APPROVED and REPROMPT. They are MANDATORY for any\n"
            "real filesystem mutation caused by THIS command, and also whenever you lazily materialize\n"
            "a standard system artifact needed to correct the draft.\n"
            "Do not rely on a command-name whitelist; reason from command semantics and effects.\n"
            "Use the correct absolute path based on the cwd at time of the command. "
            "Only include entries for what THIS command modified — not pre-existing items."
        )

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": review_prompt}
        ]

        raw_response = self.client.send_chat(self.model, messages)
        if raw_response is None:
            # Fail-open: if manager is silent, don't kill the worker turn.
            return True, "", [], []
        response = str(raw_response).strip()
        return self._parse_review_response(response)

    def review_initial_output(
        self,
        worker_response: str,
        worker_persona_yaml: str,
        conversation_history: list = None,
        has_prior_dialog: bool = False,
        continuation_text: str = "",
        history_turns: int = 6,
    ) -> Tuple[bool, str, list, list]:
        """
        Review the Worker's initial visible output before the user enters a new command.

        Returns:
            (approved, feedback, fs_additions, fs_removals)
        """
        history_excerpt = ""
        if conversation_history:
            recent = [m for m in conversation_history if m["role"] != "system"]
            recent = recent[-(history_turns * 2):]
            if recent:
                lines = []
                for m in recent:
                    tag = "USER" if m["role"] == "user" else "WORKER"
                    lines.append(f"[{tag}]: {m['content'].strip()}")
                history_excerpt = "\n".join(lines)

        history_section = (
            f"--- RECENT SESSION HISTORY (last {history_turns} turns) ---\n"
            f"{history_excerpt}\n\n"
            if history_excerpt else ""
        )
        continuation_section = (
            f"--- CONTINUATION TEXT FROM PERSONALITY ---\n"
            f"{continuation_text.strip()}\n\n"
            if continuation_text and continuation_text.strip() else ""
        )
        session_state = "RESUMED SESSION" if has_prior_dialog else "FRESH SESSION"

        review_prompt = (
            f"--- SESSION STATE ---\n"
            f"{session_state}\n\n"
            f"--- WORKER PERSONA (contains FILESYSTEM LISTING and HARD RULES) ---\n"
            f"{worker_persona_yaml}\n\n"
            f"{continuation_section}"
            f"{history_section}"
            f"--- WORKER INITIAL OUTPUT DRAFT ---\n{worker_response}\n\n"
            "--- INITIAL OUTPUT FACTS ---\n"
            "I1. This review is ONLY for the worker's first visible output before the attacker enters\n"
            "    a new command in this process. There is no CURRENT USER INPUT for this turn.\n"
            "I2. A plain prompt is acceptable if it is shell-consistent. A banner plus prompt is also\n"
            "    acceptable if it matches the persona and session state. Do NOT reprompt merely because\n"
            "    the worker chose prompt-only instead of banner+prompt, unless the continuation/history\n"
            "    makes that clearly wrong.\n"
            "I3. If the recent history shows the session is inside a sub-program or REPL (for example the\n"
            "    latest worker output ends with `>>>`, `...`, `mysql>`, `sqlite>`, or `irb(main)>`), that\n"
            "    sub-program prompt may be the correct initial visible output. Do NOT force a bash prompt.\n"
            "I4. The draft must not contain assistant-style prose, explanations, greetings like 'How can I\n"
            "    help you?', or any other breakout text. The worker must stay in shell character.\n"
            "I5. The draft must not echo a user command, because no new user command has been entered yet.\n"
            "I6. The draft should end in exactly one prompt that is consistent with the persona/history.\n"
            "    Garbled prompt text, missing prompt, or mixed prose+prompt output is a clear violation.\n"
            "I7. If the continuation text is present, treat it as guidance for resumed-session behavior,\n"
            "    but only reprompt for CLEAR contradictions.\n\n"
            "--- YOUR TASK ---\n"
            "Approve the draft unless there is a clear, shell-visible problem such as:\n"
            "1. breakout: assistant prose, explanation, or chatty text instead of shell output.\n"
            "2. prompt_error: the final prompt is missing, garbled, or clearly inconsistent with the\n"
            "   persona/history.\n"
            "3. echo: the draft shows a shell prompt followed by a command even though no user command\n"
            "   has been entered in this turn.\n"
            "4. thought_leakage: internal reasoning or meta-text appears anywhere in the draft.\n\n"
            "IMPORTANT: If you are unsure, reply APPROVED. Only reprompt for clear violations.\n\n"
            "--- REPLY FORMAT ---\n"
            "If ACCEPTABLE:\n"
            "  APPROVED\n\n"
            "If needs fixing:\n"
            "  REPROMPT: [one-sentence specific fix]\n"
            "  Optionally follow with ADD/REMOVE lines if correcting the startup output requires a\n"
            "  filesystem patch before the worker retries.\n"
        )

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": review_prompt},
        ]

        raw_response = self.client.send_chat(self.model, messages)
        if raw_response is None:
            return True, "", [], []
        response = str(raw_response).strip()
        return self._parse_review_response(response)

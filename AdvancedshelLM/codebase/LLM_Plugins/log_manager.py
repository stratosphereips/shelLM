import os
import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any, Union

class BaseLogManager:
    """
    Base class for all logging managers in the AdvancedShellm system.
    
    Provides the core logic for:
      - File initialization
      - Safe file appending
      - Log path resolution (extensible for sessions/IPs)
      - Common logging methods
    
    Subclasses should override specific behaviors or `_get_log_dir` logic.
    """

    def __init__(
        self, 
        log_dir: str, 
        enable_trace: bool = False,
        provider: Optional[str] = None, 
        model: Optional[str] = None, 
        personality: Optional[str] = None,
        session_id: Optional[str] = None,
        client_ip: Optional[str] = None
    ):
        """
        Initialize the BaseLogManager.

        Args:
            log_dir: Base directory for logs.
            enable_trace: Enable detailed HTTP tracing.
            provider: LLM provider name.
            model: LLM model name.
            personality: Active personality name.
            session_id: (Optional) Unique session identifier. Future use: per-session log folders.
            client_ip: (Optional) Client IP address. Future use: per-IP log folders.
        """
        self.log_dir: str = log_dir
        self.enable_trace: bool = enable_trace
        self.provider: str = provider or "unknown"
        self.model: str = model or "unknown"
        self.personality: str = personality or "default"
        self.session_id: Optional[str] = session_id
        self.client_ip: Optional[str] = client_ip

        # Ensure the log directory exists
        # In the future, this might use _get_log_dir() if we want dynamic paths
        self.active_log_dir = self._get_active_log_dir()
        os.makedirs(self.active_log_dir, exist_ok=True)

        self.history_path: str = os.path.join(self.active_log_dir, "history.txt")
        self.history_ts_path: str = os.path.join(self.active_log_dir, "history_ts.txt")
        self.trace_path: str = os.path.join(self.active_log_dir, "trace_log.txt")
        self.manager_trace_path: str = os.path.join(self.active_log_dir, "manager_trace_log.txt")
        self.command_history_path: str = os.path.join(self.active_log_dir, "command_history.txt")

        self.init_logs()

    # ------------------------------------------------------------
    # Path Resolution (Extensible)
    # ------------------------------------------------------------
    def _get_active_log_dir(self) -> str:
        """
        Determine the actual directory to write logs to.

        Creates a new session subdirectory inside `self.log_dir` named after
        the current datetime (format: YYYY-MM-DD_HH-MM-SS).  Every process
        invocation therefore gets its own fresh folder — sessions never share
        or append to each other's files.
        """
        session_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return os.path.join(self.log_dir, session_ts)

    # ------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------
    def init_logs(self) -> None:
        """Initialize log files with session headers."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        is_new = not os.path.exists(self.history_path)

        if is_new:
            self._write_session_header(now)

        # Trace logs always get a new section header
        trace_header = f"\n--- TRACE LOG [{now}] ---\nTrace enabled: {self.enable_trace}\n\n"
        self._append(self.trace_path, trace_header)
        mgr_trace_header = f"\n--- MANAGER TRACE LOG [{now}] ---\nTrace enabled: {self.enable_trace}\n\n"
        self._append(self.manager_trace_path, mgr_trace_header)

        # Ensure command history exists
        if not os.path.exists(self.command_history_path):
            self._append(self.command_history_path, "")

    def _write_session_header(self, timestamp: str) -> None:
        """Write the standard session header to history files."""
        session_header = [
            f"--- SESSION START [{timestamp}] ---",
            f"Provider: {self.provider}",
            f"Model: {self.model}",
            f"Personality: {self.personality}",
            f"Session ID: {self.session_id or 'N/A'}",
            f"Client IP: {self.client_ip or 'N/A'}",
            "-" * 60,
            ""
        ]
        header_text = "\n".join(session_header) + "\n"
        self._append(self.history_path, header_text)
        self._append(self.history_ts_path, header_text)

    # ------------------------------------------------------------
    # Basic File Helpers
    # ------------------------------------------------------------
    @staticmethod
    def _exists(path: str) -> bool:
        """Check if a file exists and is not empty."""
        return os.path.exists(path) and os.path.getsize(path) > 0

    @staticmethod
    def _append(path: str, text: str) -> None:
        """Append text to a file safely."""
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
        except IOError as e:
            print(f"Error writing to log file {path}: {e}")

    # ------------------------------------------------------------
    # Common Logging Methods
    # ------------------------------------------------------------
    def log_system(self, text: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._append(self.history_path, f"SYSTEM:\n{text.strip()}\n")
        self._append(self.history_ts_path, f"[{now}] SYSTEM:\n{text.strip()}\n")
    
    def log_marker(self, kind: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        marker = f"--- {kind.upper()} [{now}] ---\n"
        if kind.lower() == "stopped":
            marker += "Session closed. Resume from next session.\n"
        elif kind.lower() == "token reset":
            marker += "Token limit reached. Reloading base personality.\n"
            
        self._append(self.history_path, marker)
        self._append(self.history_ts_path, marker)

    def _trace_path_for(self, provider_label: Optional[str]) -> str:
        """Return the manager trace path if the label contains MANAGER, else the worker one."""
        if provider_label and "MANAGER" in provider_label.upper():
            return self.manager_trace_path
        return self.trace_path

    def trace_request(self, method: str, url: str, headers: Dict[str, Any], body: Any, provider_label: Optional[str] = None) -> None:
        if not self.enable_trace: return
        now = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        label = provider_label or self.provider or "LLM"
        try:
            body_str = json.dumps(body, indent=2)
        except (TypeError, ValueError):
            body_str = str(body)
        formatted = (
            f"{now} REQUEST → {label}\n"
            f"METHOD: {method}\n"
            f"URL: {url}\n"
            f"BODY:\n{body_str}\n" + "-" * 78 + "\n"
        )
        self._append(self._trace_path_for(provider_label), formatted)

    def trace_response(self, status: int, headers: Dict[str, Any], body: Any, provider_label: Optional[str] = None) -> None:
        if not self.enable_trace: return
        now = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        label = provider_label or self.provider or "LLM"
        try:
            parsed = json.loads(body) if isinstance(body, str) else body
            body_str = json.dumps(parsed, indent=2)
        except (TypeError, ValueError):
            body_str = str(body)
        formatted = (
            f"{now} RESPONSE ← {label}\n"
            f"STATUS: {status}\n"
            f"BODY:\n{body_str}\n" + "-" * 78 + "\n"
        )
        self._append(self._trace_path_for(provider_label), formatted)

    def log_crash(self, exc: BaseException, context: str = "") -> None:
        """Write a full crash report (traceback + context) to error.log."""
        import traceback as _tb
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        error_path = os.path.join(self.active_log_dir, "error.log")
        tb_str = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
        header = (
            f"\n{'='*70}\n"
            f"CRASH REPORT [{now}]\n"
            f"Provider: {self.provider}  Model: {self.model}\n"
        )
        if context:
            header += f"Context: {context}\n"
        header += f"{'='*70}\n"
        self._append(error_path, header + tb_str + "\n")

    def close_session(self) -> None:
        self.log_marker("stopped")


class ShelLMLogManager(BaseLogManager):
    """
    Specialized logger for the ShelLM Honeypot interactions.
    
    Includes methods specific to recording shell sessions:
      - log_user (recording commands)
      - log_response (recording simulated output)
      - record_command (shell history)
      - parse_history_to_messages (context reconstruction)
    """

    def log_user(self, command: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._append(self.history_path, f"USER:\n{command.strip()}\n")
        self._append(self.history_ts_path, f"[{now}] USER:\n{command.strip()}\n")

    def log_response(self, text: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._append(self.history_path, f"RESPONSE:\n{text.strip()}\n")
        self._append(self.history_ts_path, f"[{now}] RESPONSE:\n{text.strip()}\n")

    def record_initial_prompt(self, prompt_text: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        block = f"SYSTEM:\n{prompt_text.strip()}\n"
        block_ts = f"[{now}] SYSTEM:\n{prompt_text.strip()}\n"
        self._append(self.history_path, block)
        self._append(self.history_ts_path, block_ts)

    def record_command(self, command: str) -> None:
        line = command.strip() + "\n"
        self._append(self.command_history_path, line)

    def log_manager_action(self, action_type: str, content: str) -> None:
        """Log high-level manager actions."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_file = os.path.join(self.active_log_dir, "manager_trace.txt")
        self._append(log_file, f"[{now}] ACTION: {action_type}\n{content}\n" + "-"*40 + "\n")

    def log_internal_dialogue(self, speaker: str, content: str) -> None:
        """Log the internal dialogue between Manager and Worker."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_file = os.path.join(self.active_log_dir, "internal_monologue.txt")
        self._append(log_file, f"[{now}] {speaker}:\n{content}\n\n")

    def log_draft_attempt(self, attempt: int, draft: str, approved: bool, feedback: str, user_cmd: str = "") -> None:
        """Log a single manager review cycle (draft + verdict) to internal_monologue.txt."""
        import re
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_file = os.path.join(self.active_log_dir, "internal_monologue.txt")

        # Strip model think blocks and special tokens before logging
        clean_draft = re.sub(r"<think>.*?</think>", "", draft, flags=re.DOTALL)
        clean_draft = re.sub(r"<\|.*?\|>", "", clean_draft)
        clean_draft = re.sub(r"<analysis>|</analysis>|<final>|</final>", "", clean_draft, flags=re.IGNORECASE)
        clean_draft = clean_draft.strip()

        verdict = "APPROVED" if approved else f"REPROMPT: {feedback}"

        # On the first attempt, write a turn header so turns are clearly separated
        entry = ""
        if attempt == 1:
            cmd_line = f"  Command: $ {user_cmd.strip()}\n" if user_cmd.strip() else ""
            entry += f"\n{'='*60}\n[{now}] MANAGER REVIEW TURN\n{cmd_line}{'='*60}\n"

        entry += (
            f"[ATTEMPT {attempt}]\n"
            f"Draft:\n{clean_draft}\n"
            f"Verdict: {verdict}\n"
            + "-" * 50 + "\n"
        )
        self._append(log_file, entry)

    def log_fs_patch(self, additions: list, removals: list) -> None:
        """Log filesystem patch operations (ADD/REMOVE) to internal_monologue.txt.

        Called immediately after a manager APPROVED verdict that included ADD/REMOVE
        instructions, so the full audit trail lives in one place.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_file = os.path.join(self.active_log_dir, "internal_monologue.txt")
        lines = [f"[{now}] FS PATCH APPLIED"]
        for entry in additions:
            lines.append(f"  ADD:    {entry.strip()}")
        for path in removals:
            lines.append(f"  REMOVE: {path.strip()}")
        if not additions and not removals:
            lines.append("  (no changes)")
        self._append(log_file, "\n".join(lines) + "\n" + "-" * 50 + "\n")



    def save_generated_persona(self, yaml_content: str) -> None:
        """Save the generated worker persona to a file."""
        path = os.path.join(self.active_log_dir, "generated_personality.yml")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(yaml_content)
        except IOError as e:
            print(f"Error saving generated persona: {e}")

    def parse_history_to_messages(
        self, 
        base_prompt: str, 
        continuation_text: Optional[str] = None
    ) -> Tuple[List[Dict[str, str]], bool]:
        messages: List[Dict[str, str]] = [{"role": "system", "content": base_prompt}]
        has_prior_dialog = False

        if not self._exists(self.history_path):
            return messages, has_prior_dialog

        current_role: Optional[str] = None
        current_buffer: List[str] = []

        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                for raw in f:
                    stripped = raw.strip()
                    if (stripped.startswith("--- SESSION") or 
                        stripped.startswith("Provider:") or 
                        stripped.startswith("Model:") or 
                        stripped.startswith("Personality:") or 
                        stripped.startswith("Session ID:") or 
                        stripped.startswith("Client IP:") or 
                        (set(stripped) == {"-"})):
                        continue

                    new_role = None
                    if stripped == "USER:": new_role = "user"
                    elif stripped == "RESPONSE:": new_role = "assistant"
                    
                    if new_role:
                        if current_role and current_buffer:
                            content = "\n".join(current_buffer).strip()
                            if content: messages.append({"role": current_role, "content": content})
                        current_role = new_role
                        current_buffer = []
                        continue

                    if current_role in ("user", "assistant"):
                        if (stripped.startswith("--- STOPPED [") or 
                            stripped.startswith("--- TOKEN RESET [")): continue
                        if stripped in ("Session closed. Resume from next session.", "Token limit reached. Reloading base personality."): continue
                        current_buffer.append(raw.rstrip("\n"))

            if current_role and current_buffer:
                content = "\n".join(current_buffer).strip()
                if content: messages.append({"role": current_role, "content": content})

        except IOError as e:
            print(f"Error reading history file: {e}")

        for m in messages:
            if m["role"] in ("user", "assistant"):
                has_prior_dialog = True
                break

        if has_prior_dialog and continuation_text:
            messages.append({"role": "system", "content": str(continuation_text)})

        return messages, has_prior_dialog


class ShelLMManagerLogManager(BaseLogManager):
    """
    Logger for the ShelLM Manager component.
    
    Intended to log management events, API calls for orchestration, 
    and other high-level system activities.
    
    Inherits standard logging capabilities from BaseLogManager.
    Future extensions:
      - logging to a centralized database?
      - logging operational metrics?
    """
    pass

# Alias for backward compatibility
# Any code importing LogManager will now get ShelLMLogManager
LogManager = ShelLMLogManager

class InitializationLogManager(BaseLogManager):
    """
    Logger for the Initialization process.

    Tracks the communication between the endpoint and the Manager Agent
    during worker personality creation. Deliberately avoids creating the
    session-level files used by the honeypot runtime (history.txt,
    history_ts.txt, trace_log.txt, command_history.txt).

    Only files created in logs_init/:
      - manager_init_trace.txt  : Manager Agent actions & decisions
      - init_trace.txt          : HTTP request/response trace (if trace enabled)
      - generated_personality_backup.yml : backup of the output
    """

    def __init__(
        self,
        log_dir: str,
        enable_trace: bool = False,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ):
        # Store settings directly — do NOT call super().__init__() so that
        # BaseLogManager never gets a chance to create the session log files.
        self.log_dir = log_dir
        self.active_log_dir = log_dir
        self.enable_trace = enable_trace
        self.provider = provider or "unknown"
        self.model = model or "unknown"
        self.personality = "init"
        self.session_id = None
        self.client_ip = None

        os.makedirs(self.active_log_dir, exist_ok=True)

        # Only init-specific paths — none of the honeypot session paths
        self.manager_trace_path = os.path.join(self.active_log_dir, "manager_init_trace.txt")
        self.init_trace_path    = os.path.join(self.active_log_dir, "init_trace.txt")

        # Write a one-time session header to the manager trace file
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = (
            f"--- INITIALIZATION SESSION [{now}] ---\n"
            f"Provider: {self.provider}\n"
            f"Model: {self.model}\n"
            + "-" * 60 + "\n\n"
        )
        self._append(self.manager_trace_path, header)

        if self.enable_trace:
            self._append(
                self.init_trace_path,
                f"\n--- INIT HTTP TRACE [{now}] ---\n\n"
            )

    # ------------------------------------------------------------------
    # Override trace methods to write to init_trace.txt, not trace_log.txt
    # ------------------------------------------------------------------
    def trace_request(self, method: str, url: str, headers: Dict[str, Any], body: Any, provider_label: Optional[str] = None) -> None:
        if not self.enable_trace:
            return
        now = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        label = provider_label or self.provider
        try:
            body_str = json.dumps(body, indent=2)
        except (TypeError, ValueError):
            body_str = str(body)
        formatted = (
            f"{now} REQUEST → {label}\n"
            f"METHOD: {method}\n"
            f"URL: {url}\n"
            f"BODY:\n{body_str}\n" + "-" * 78 + "\n"
        )
        self._append(self.init_trace_path, formatted)

    def trace_response(self, status: int, headers: Dict[str, Any], body: Any, provider_label: Optional[str] = None) -> None:
        if not self.enable_trace:
            return
        now = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        label = provider_label or self.provider
        try:
            parsed = json.loads(body) if isinstance(body, str) else body
            body_str = json.dumps(parsed, indent=2)
        except (TypeError, ValueError):
            body_str = str(body)
        formatted = (
            f"{now} RESPONSE ← {label}\n"
            f"STATUS: {status}\n"
            f"BODY:\n{body_str}\n" + "-" * 78 + "\n"
        )
        self._append(self.init_trace_path, formatted)

    # ------------------------------------------------------------------
    # Initialization-specific log helpers
    # ------------------------------------------------------------------
    def log_marker(self, kind: str) -> None:
        """Write an error/event marker to manager_init_trace.txt (no history_path here)."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        marker = f"--- {kind.upper()} [{now}] ---\n"
        self._append(self.manager_trace_path, marker)

    def log_manager_action(self, action_type: str, content: str) -> None:
        """Log a Manager Agent action to manager_init_trace.txt."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._append(
            self.manager_trace_path,
            f"[{now}] ACTION: {action_type}\n{content}\n" + "-" * 40 + "\n"
        )

    def save_generated_persona(self, yaml_content: str) -> None:
        """Save a backup of the generated worker persona."""
        path = os.path.join(self.active_log_dir, "generated_personality_backup.yml")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(yaml_content)
        except IOError as e:
            print(f"Error saving generated persona backup: {e}")


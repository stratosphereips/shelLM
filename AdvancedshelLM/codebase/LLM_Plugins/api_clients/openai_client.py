import requests
from typing import List, Dict, Any, Optional


# ---------------------------------------------------------------------------
# Responses API model detection
# ---------------------------------------------------------------------------

# Models that must use the Responses API (v1/responses) instead of
# v1/chat/completions.  Any model whose name contains "codex" (case-insensitive)
# is also routed to the Responses API automatically.
RESPONSES_API_MODELS = {
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini",
    "gpt-5-codex",
    "codex-mini-latest",
}

CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
RESPONSES_API_URL    = "https://api.openai.com/v1/responses"

# How many recent user/assistant turns to include when calling the Responses API.
# The system prompt (instructions) is always sent in full separately.
CODEX_HISTORY_TURNS = 6


def _is_responses_model(model: str) -> bool:
    """Return True if this model must use the Responses API."""
    return model in RESPONSES_API_MODELS or "codex" in model.lower()


def _messages_to_responses_input(
    messages: List[Dict[str, Any]],
    max_turns: int = CODEX_HISTORY_TURNS,
):
    """
    Convert a chat-style messages list into the format expected by the
    Responses API.

    Returns:
        instructions (str): The system prompt text (sent as top-level
                            'instructions').
        input_items (list): A list of {"role": ..., "content": ...} dicts
                            covering the last *max_turns* user/assistant
                            exchanges.

    The Responses API accepts:
      - 'instructions': a plain string (the system prompt)
      - 'input': either a plain string OR a list of message objects
    """
    instructions = ""
    for m in messages:
        if m.get("role") == "system":
            instructions = (m.get("content") or "").strip()
            break

    dialog = [m for m in messages if m.get("role") in ("user", "assistant")]
    recent = dialog[-(max_turns * 2):]

    input_items = []
    for m in recent:
        role    = m.get("role", "")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            input_items.append({"role": role, "content": content})

    return instructions, input_items


# ---------------------------------------------------------------------------
# Unified OpenAI client
# ---------------------------------------------------------------------------

class OpenAIClient:
    """
    Unified OpenAI client that transparently handles BOTH:

      - Chat models  → POST /v1/chat/completions
                       (gpt-4o, gpt-4.1, gpt-5.1, o1, o3, …)
      - Codex models → POST /v1/responses
                       (gpt-5.3-codex, codex-mini-latest, …)

    The routing decision is made automatically from the model name via
    ``_is_responses_model()``.  All callers use the same ``send_chat()``
    interface regardless of which backend is used.

    For Responses-API models the messages list is translated internally:
      - 'instructions' ← the system prompt
      - 'input'        ← the last CODEX_HISTORY_TURNS user/assistant pairs

    Uses raw ``requests`` (no official SDK) so that every HTTP detail is
    available for trace logging.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        logger,
        provider_label: str = "OpenAI",
    ):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is missing.")
        self.api_key        = api_key
        self.model          = model
        self.logger         = logger
        self.provider_label = provider_label

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_chat(
        self,
        model: Optional[str],
        messages: List[Dict[str, Any]],
    ) -> str:
        """
        Send a request to OpenAI and return the assistant's reply as a string.

        Automatically routes to the Responses API for codex-style models and
        to Chat Completions for everything else.
        """
        mdl = model or self.model
        if _is_responses_model(mdl):
            return self._send_responses_api(mdl, messages)
        return self._send_chat_completions(mdl, messages)

    # ------------------------------------------------------------------
    # Internal: Chat Completions API
    # ------------------------------------------------------------------

    def _send_chat_completions(
        self,
        mdl: str,
        messages: List[Dict[str, Any]],
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: Dict[str, Any] = {
            "model":       mdl,
            "messages":    messages,
            "temperature": 0.0,
            "max_tokens":  16384,
        }

        # Reasoning / newer models reject temperature and max_tokens
        if (
            mdl.startswith("gpt-5.1")
            or mdl.startswith("gpt-5.2")
            or mdl.startswith("gpt-5.3")
            or mdl.startswith("o1")
            or mdl.startswith("o3")
        ):
            body.pop("temperature", None)
            body.pop("max_tokens", None)
            body["max_completion_tokens"] = 4096

        if self.logger:
            self.logger.trace_request(
                method="POST",
                url=CHAT_COMPLETIONS_URL,
                headers=headers,
                body=body,
                provider_label=self.provider_label,
            )

        resp = requests.post(
            CHAT_COMPLETIONS_URL, headers=headers, json=body, timeout=300
        )

        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"_raw": resp.text}

        if self.logger:
            self.logger.trace_response(
                status=resp.status_code,
                headers=dict(resp.headers),
                body=resp_json,
                provider_label=self.provider_label,
            )

        resp.raise_for_status()
        return resp_json["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------
    # Internal: Responses API (codex / responses-endpoint models)
    # ------------------------------------------------------------------

    def _send_responses_api(
        self,
        mdl: str,
        messages: List[Dict[str, Any]],
    ) -> str:
        instructions, input_items = _messages_to_responses_input(
            messages, max_turns=CODEX_HISTORY_TURNS
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # If there are no prior dialog turns, send a plain string so the model
        # generates the opening shell banner.
        if not input_items:
            input_payload: Any = (
                "Begin the session. Output only the SSH banner and the first shell prompt."
            )
        else:
            input_payload = input_items

        body = {
            "model":        mdl,
            "instructions": instructions,
            "input":        input_payload,
        }

        _label = f"{self.provider_label}[responses]"

        if self.logger:
            self.logger.trace_request(
                method="POST",
                url=RESPONSES_API_URL,
                headers=headers,
                body=body,
                provider_label=_label,
            )

        resp = requests.post(
            RESPONSES_API_URL, headers=headers, json=body, timeout=300
        )

        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"_raw": resp.text}

        if self.logger:
            self.logger.trace_response(
                status=resp.status_code,
                headers=dict(resp.headers),
                body=resp_json,
                provider_label=_label,
            )

        resp.raise_for_status()

        # Responses API structure:
        # { "output": [ { "content": [ { "type": "output_text", "text": "…" } ] } ] }
        try:
            for item in resp_json.get("output", []):
                for block in item.get("content", []):
                    if block.get("type") == "output_text":
                        return block["text"].strip()
        except (KeyError, IndexError, TypeError):
            pass

        raise ValueError(
            f"Unexpected Responses API response structure: {resp_json}"
        )


# ---------------------------------------------------------------------------
# Alias — OpenAICodexClient is the same class; kept for any legacy references.
# ---------------------------------------------------------------------------
OpenAICodexClient = OpenAIClient

import requests
from typing import List, Dict, Any, Optional

# Custom exceptions for clearer error handling in the honeypot core
class BackendUnavailable(Exception):
    """Raised when the E-INFRA backend cannot be reached (connection error, timeout)."""
    pass

class BackendHTTPError(Exception):
    """Raised when the backend returns a non-200 HTTP status code."""
    def __init__(self, status_code: int, body_preview: str = ""):
        super().__init__(f"HTTP {status_code}: {body_preview}")
        self.status_code = status_code
        self.body_preview = body_preview

class BackendParseError(Exception):
    """Raised when the backend response cannot be parsed as JSON or is missing expected fields."""
    pass


class EinfraClient:
    """
    Client for the E-INFRA CZ chat completion API.
    
    This client is similar to the OpenAI client but includes:
      - Custom exception wrapping (BackendUnavailable, BackendHTTPError, etc.).
      - Specific logging of internal errors via log_marker to avoid crashing the session 
        while still recording what went wrong for the admin.
    """
    
    # Specific endpoint for the E-INFRA service
    BASE_URL = "https://llm.ai.e-infra.cz/v1/chat/completions"

    def __init__(self, api_key: str, model: str, logger=None, provider_label: str = "E-INFRA"):
        """
        Initialize the E-INFRA client.
        
        Args:
            api_key: The API key for authentication.
            model: The default model to use.
            logger: The LogManager instance for tracing and error markers.
            provider_label: Label used in trace logs to identify this client.
        """
        self.api_key = api_key
        self.model = model
        self.logger = logger
        self.provider_label = provider_label

    def send_chat(self, model: str, messages: list) -> str:
        """
        Send a chat request to the E-INFRA backend.
        
        Args:
            model: The model to use (overrides default if provided).
            messages: List of conversation messages.
            
        Returns:
            The string content of the model's response.
            
        Raises:
            BackendUnavailable: If the network call fails.
            BackendHTTPError: If the server returns 4xx/5xx.
            BackendParseError: If the response is malformed.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Construct the payload compatible with OpenAI-like API
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 16384,#4096,#900,
            "stream": False
        }

        # TRACE: Log outgoing request
        if self.logger:
            self.logger.trace_request("POST", self.BASE_URL, headers, payload, provider_label=self.provider_label)

        try:
            # Send request with a 5-minute timeout
            resp = requests.post(self.BASE_URL, headers=headers, json=payload, timeout=300)
        except requests.RequestException as e:
            # Log the specific error internally so we know why it failed
            if self.logger:
                self.logger.log_marker(f"einfra_request_exception: {repr(e)}")
            # Re-raise as a generic unavailable error
            raise BackendUnavailable("E-INFRA backend unreachable") from e

        # TRACE: Log incoming response (raw)
        if self.logger:
            self.logger.trace_response(resp.status_code, dict(resp.headers), resp.text, provider_label=self.provider_label)

        # Handle HTTP Errors
        if resp.status_code != 200:
            body_preview = (resp.text or "")[:300]
            if self.logger:
                self.logger.log_marker(f"einfra_http_error: {resp.status_code} {body_preview}")
            raise BackendHTTPError(resp.status_code, body_preview)

        # Parse the JSON response
        try:
            data = resp.json()
            choices = data.get("choices", [])
            
            # Validate structure: choices[0].message.content
            if choices and "message" in choices[0]:
                content = choices[0]["message"].get("content")
                if content is None:
                    # Treat explicit null content as a no-op assistant turn.
                    if self.logger:
                        self.logger.log_marker("einfra_null_content")
                    return None
                return str(content).strip()

            # Log specific parse failure details
            if self.logger:
                self.logger.log_marker(f"einfra_no_content: {str(data)[:300]}")
            raise BackendParseError("E-INFRA returned no content")
            
        except ValueError as e:
            # JSON decode failed
            if self.logger:
                self.logger.log_marker(f"einfra_json_parse_error: {repr(e)}")
            raise BackendParseError("E-INFRA response parse error") from e

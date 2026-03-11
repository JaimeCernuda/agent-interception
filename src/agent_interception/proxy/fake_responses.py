"""Build fake LLM responses telling the agent to use a session ID."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from starlette.responses import Response

from agent_interception.models import Provider

_SESSION_SETUP_MSG = (
    "The interceptor proxy requires a session ID for request tracking. "
    "Please update your base URL to include a session prefix:\n\n"
    "  http://{host}/_session/<ID>/\n\n"
    "For example:\n"
    "  ANTHROPIC_BASE_URL=http://{host}/_session/my-agent/\n"
    "  OPENAI_BASE_URL=http://{host}/_session/my-agent/v1\n\n"
    "Alternatively, set the X-Interceptor-Conversation-Id header on each request.\n\n"
    "Please inform the user of this requirement."
)


def build_session_required_response(
    provider: Provider,
    host: str = "localhost:8080",
) -> Response:
    """Return an HTTP 200 with a fake LLM response asking the agent to use a session ID.

    The response body is formatted for the detected provider so that the calling
    agent/SDK can parse it without errors and surface the message to the user.
    """
    message = _SESSION_SETUP_MSG.format(host=host)

    if provider == Provider.ANTHROPIC:
        body = {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": message}],
            "model": "interceptor-proxy",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    elif provider == Provider.OPENAI:
        body = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:29]}",
            "object": "chat.completion",
            "created": int(datetime.now(UTC).timestamp()),
            "model": "interceptor-proxy",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": message},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    elif provider == Provider.OLLAMA:
        body = {
            "model": "interceptor-proxy",
            "created_at": datetime.now(UTC).isoformat(),
            "message": {"role": "assistant", "content": message},
            "done": True,
        }
    else:
        body = {
            "error": {
                "code": -32000,
                "message": message,
            }
        }

    return Response(
        content=json.dumps(body),
        status_code=200,
        media_type="application/json",
    )

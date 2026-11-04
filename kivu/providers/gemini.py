"""Gemini adapter: kivu messages <-> google-genai types.

The SDK only makes the HTTP call. Kivu owns the history and the loop, so this
uses generate_content (not chats) and turns automatic function calling off.
"""

from __future__ import annotations

import uuid

from google import genai
from google.genai import types

from ..messages import Message, Response, Text, ToolCall, ToolResult, Usage
from ..tools.registry import Tool

# Gemini doesn't always return a call id; we make one up and don't send it back.
_LOCAL_ID = "kivu_"


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str, api_key: str) -> None:
        self.model = model
        self.client = genai.Client(api_key=api_key)

    def complete(self, system: str, messages: list[Message], tools: list[Tool]) -> Response:
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[_declaration(t) for t in tools])] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        resp = self.client.models.generate_content(
            model=self.model,
            contents=[_to_content(m) for m in messages],
            config=config,
        )
        content = resp.candidates[0].content if resp.candidates else None
        usage = resp.usage_metadata
        return Response(
            message=_from_content(content),
            usage=Usage(
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            ),
        )


def _declaration(tool: Tool) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=tool.name,
        description=tool.description,
        parameters_json_schema=tool.parameters,
    )


def _to_content(message: Message) -> types.Content:
    if message.role == "assistant" and isinstance(message.raw, types.Content):
        return message.raw  # keeps thought signatures intact
    parts = []
    for part in message.parts:
        if isinstance(part, Text):
            parts.append(types.Part(text=part.text))
        elif isinstance(part, ToolCall):
            parts.append(types.Part(function_call=types.FunctionCall(
                id=_remote_id(part.id), name=part.name, args=part.arguments,
            )))
        elif isinstance(part, ToolResult):
            key = "error" if part.is_error else "output"
            parts.append(types.Part(function_response=types.FunctionResponse(
                id=_remote_id(part.call_id), name=part.name, response={key: part.output},
            )))
    return types.Content(role="model" if message.role == "assistant" else "user", parts=parts)


def _from_content(content: types.Content | None) -> Message:
    parts = []
    for part in (content.parts if content and content.parts else []):
        if part.function_call:
            fc = part.function_call
            parts.append(ToolCall(fc.id or _LOCAL_ID + uuid.uuid4().hex[:8], fc.name or "", dict(fc.args or {})))
        elif part.text and not part.thought:
            parts.append(Text(part.text))
    return Message("assistant", parts, raw=content)


def _remote_id(call_id: str) -> str | None:
    return None if call_id.startswith(_LOCAL_ID) else call_id

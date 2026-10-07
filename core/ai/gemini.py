import logging
import os
from collections.abc import AsyncIterator, Sequence

from dotenv import load_dotenv
from google.genai import Client, types

from core.ai.base import BaseAIPlatform, Message, StreamChunk, Tool, ToolCall

load_dotenv()
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or ""
# Thinking models spend output tokens on reasoning and on the thought signature that
# must accompany function calls; cutting it off makes the API reject the response.
MAX_OUTPUT_TOKENS = 2048


class Gemini(BaseAIPlatform):
    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        system_prompt: str = "",
        api_key: str = GEMINI_API_KEY,
    ):
        if not api_key:
            raise ValueError("API key is required for Gemini AI platform.")
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self.client = Client(api_key=self.api_key)

    @staticmethod
    def _to_contents(messages: list[Message]) -> list[types.Content]:
        contents: list[types.Content] = []
        for message in messages:
            parts = _message_parts(message)
            if not parts:
                continue
            # Function responses are delivered as a user turn, matching the API's
            # own automatic function-calling history.
            role = "user" if message.tool_results else message.role
            contents.append(types.Content(role=role, parts=parts))
        return contents

    @staticmethod
    def _to_config(
        system_prompt: str, tools: Sequence[Tool]
    ) -> types.GenerateContentConfig:
        config: dict = {
            "system_instruction": system_prompt,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        }
        if tools:
            config["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.parameters,
                        }
                        for tool in tools
                    ]
                }
            ]
        return types.GenerateContentConfig(**config)

    async def chat(
        self,
        messages: list[Message],
        *,
        system_prompt: str = "",
        tools: Sequence[Tool] = (),
    ) -> AsyncIterator[StreamChunk]:
        stream = await self.client.aio.models.generate_content_stream(
            model=self.model,
            contents=self._to_contents(messages),
            config=self._to_config(system_prompt or self.system_prompt, tools),
        )

        usage = None
        pending_calls: list[ToolCall] = []
        async for chunk in stream:
            usage = chunk.usage_metadata or usage
            for part in _candidate_parts(chunk):
                if part.function_call:
                    pending_calls.append(
                        ToolCall(
                            name=part.function_call.name,
                            args=dict(part.function_call.args or {}),
                            payload=part,
                        )
                    )
                elif part.text:
                    yield StreamChunk(text=part.text)

        if pending_calls:
            yield StreamChunk(tool_calls=pending_calls)
        yield StreamChunk(
            done=True,
            input_token=getattr(usage, "prompt_token_count", None),
            output_token=getattr(usage, "candidates_token_count", None),
        )


def _message_parts(message: Message) -> list[types.Part]:
    parts: list[types.Part] = []
    if message.content:
        parts.append(types.Part.from_text(text=message.content))
    parts += [_tool_call_part(call) for call in message.tool_calls]
    parts += [
        types.Part.from_function_response(name=r.name, response=r.response)
        for r in message.tool_results
    ]
    return parts


def _candidate_parts(chunk: types.GenerateContentResponse) -> list[types.Part]:
    if not chunk.candidates or not chunk.candidates[0].content:
        return []
    return chunk.candidates[0].content.parts or []


def _tool_call_part(call: ToolCall) -> types.Part:
    """Replay the exact part the model produced (it carries a required thought signature)."""
    if isinstance(call.payload, types.Part):
        return call.payload
    return types.Part(function_call=types.FunctionCall(name=call.name, args=call.args))

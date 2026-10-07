from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Tool:
    """Provider-agnostic function declaration. `parameters` is a JSON Schema."""

    name: str
    description: str
    parameters: dict


@dataclass(frozen=True)
class ToolCall:
    """A function the model asked us to run."""

    name: str
    args: dict = field(default_factory=dict)
    # Opaque provider payload (signature, call id, ...). Echoed back verbatim when the
    # call is replayed in history, so providers that require it keep working.
    payload: object = None


@dataclass(frozen=True)
class ToolResult:
    name: str
    response: dict


@dataclass(frozen=True)
class ToolContext:
    """Who the tools act for: identity plus plan info."""

    user_id: str
    subscription_status: str = "basic"


@dataclass
class Message:
    role: str  # user | model | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class StreamChunk:
    text: str = ""
    done: bool = False
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: int | None = None
    input_token: int | None = None
    output_token: int | None = None


class BaseAIPlatform(ABC):
    """Model-agnostic chat interface."""

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        *,
        system_prompt: str = "",
        tools: Sequence[Tool] = (),
    ) -> AsyncIterator[StreamChunk]:
        """Async generator of StreamChunks: token text, then tool calls and/or done."""

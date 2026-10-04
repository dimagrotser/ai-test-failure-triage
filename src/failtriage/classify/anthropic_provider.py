from pathlib import Path
from typing import Any

import anthropic

from failtriage.classify.provider import ProviderError, Usage, write_recording
from failtriage.prompts import Prompt

# An answer is a few sentences and a handful of quotes, so this is far above what it needs.
MAX_ANSWER_TOKENS = 4096


class AnthropicProvider:
    """Asks the Anthropic API for a JSON answer. With `record_dir` it also saves every call."""

    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str,
        schema: dict[str, Any],
        record_dir: Path | None = None,
    ) -> None:
        self._client = client
        self.model = model
        self._schema = schema
        self._record_dir = record_dir
        self.usage = Usage()

    def complete(self, prompt: Prompt, payload: str) -> str:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_ANSWER_TOKENS,
                system=prompt.text,
                messages=[{"role": "user", "content": payload}],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": self._schema},
                },
            )
        except anthropic.APIError as exc:
            # Only the type: the SDK message can quote the request.
            raise ProviderError(f"the request failed: {type(exc).__name__}") from None
        call = Usage(
            calls=1,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        # Billed even when the answer is unusable.
        self.usage.calls += call.calls
        self.usage.input_tokens += call.input_tokens
        self.usage.output_tokens += call.output_tokens
        if response.stop_reason in ("refusal", "max_tokens"):
            raise ProviderError(f"the model stopped with {response.stop_reason}")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise ProviderError("the model answered without text")
        if self._record_dir is not None:
            write_recording(self._record_dir, prompt, self.model, payload, text, call)
        return text

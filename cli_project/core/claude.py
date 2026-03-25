import os
import json
from openai import AzureOpenAI


class ContentBlock:
    """Represents a single block of content in a message."""
    def __init__(self, type, text=None, id=None, name=None, input=None):
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input


class AIMessage:
    """Wraps an AI response for compatibility with the rest of the codebase."""
    def __init__(self, content: list, stop_reason: str):
        self.content = content
        self.stop_reason = stop_reason


class AIService:
    def __init__(self, model: str):
        self.client = AzureOpenAI(
            api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
            azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
        )
        self.model_name = model  # Azure deployment name

    def add_user_message(self, messages: list, message):
        if isinstance(message, list):
            # Tool results — add as individual "tool" role messages
            for result in message:
                messages.append({
                    "role": "tool",
                    "tool_call_id": result["tool_use_id"],
                    "content": result.get("content", ""),
                })
        else:
            messages.append({"role": "user", "content": message})

    def add_assistant_message(self, messages: list, message):
        if isinstance(message, AIMessage):
            text = "\n".join(b.text for b in message.content if b.type == "text" and b.text)
            tool_calls = [
                {
                    "id": b.id,
                    "type": "function",
                    "function": {"name": b.name, "arguments": json.dumps(b.input or {})},
                }
                for b in message.content if b.type == "tool_use"
            ]
            msg: dict = {"role": "assistant", "content": text or ""}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            messages.append(msg)
        else:
            messages.append({"role": "assistant", "content": str(message)})

    def text_from_message(self, message: AIMessage) -> str:
        return "\n".join(
            b.text for b in message.content
            if isinstance(b, ContentBlock) and b.type == "text" and b.text
        )

    def _convert_tools(self, tools: list) -> list:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for t in tools
        ]

    def _to_openai_messages(self, messages: list) -> list:
        result = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "tool":
                result.append(msg)
            elif isinstance(content, str) or content is None:
                result.append(msg)  # pass whole dict to preserve tool_calls
            elif isinstance(content, list):
                # Tool results list (from add_user_message with tool results)
                if content and isinstance(content[0], dict) and "tool_use_id" in content[0]:
                    for item in content:
                        result.append({
                            "role": "tool",
                            "tool_call_id": item["tool_use_id"],
                            "content": item.get("content", ""),
                        })
                # ContentBlock list (assistant message)
                elif content and hasattr(content[0], "type"):
                    text = "\n".join(b.text for b in content if b.type == "text" and b.text)
                    tool_calls = [
                        {
                            "id": b.id,
                            "type": "function",
                            "function": {"name": b.name, "arguments": json.dumps(b.input or {})},
                        }
                        for b in content if b.type == "tool_use"
                    ]
                    m: dict = {"role": role, "content": text or ""}
                    if tool_calls:
                        m["tool_calls"] = tool_calls
                    result.append(m)
            else:
                result.append({"role": role, "content": str(content)})
        return result

    def chat(
        self,
        messages,
        system=None,
        temperature=1.0,
        stop_sequences=None,
        tools=None,
        thinking=False,
        thinking_budget=1024,
    ) -> AIMessage:
        openai_messages = []
        if system:
            openai_messages.append({"role": "system", "content": system})
        openai_messages.extend(self._to_openai_messages(messages))

        params: dict = {
            "model": self.model_name,
            "messages": openai_messages,
            "temperature": temperature,
        }
        if tools:
            params["tools"] = self._convert_tools(tools)
            params["tool_choice"] = "auto"
        if stop_sequences:
            params["stop"] = stop_sequences

        response = self.client.chat.completions.create(**params)
        choice = response.choices[0]
        msg = choice.message

        content_blocks = []
        if msg.content:
            content_blocks.append(ContentBlock(type="text", text=msg.content))
        if msg.tool_calls:
            for tc in msg.tool_calls:
                content_blocks.append(ContentBlock(
                    type="tool_use",
                    id=tc.id,
                    name=tc.function.name,
                    input=json.loads(tc.function.arguments),
                ))

        stop_reason = "tool_use" if choice.finish_reason == "tool_calls" else "end_turn"
        return AIMessage(content=content_blocks, stop_reason=stop_reason)


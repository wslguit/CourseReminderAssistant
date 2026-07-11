import os

import requests


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"


class LLMError(Exception):
    pass


class DeepSeekClient:
    def __init__(self, api_key=None, model=DEFAULT_MODEL):
        self.api_key = (api_key or os.getenv("DEEPSEEK_API_KEY") or "").strip()
        self.model = model or DEFAULT_MODEL

    def chat(self, messages, temperature=0.4, timeout=45):
        if not self.api_key:
            raise LLMError("缺少 DeepSeek API Key。请在 AI 助手窗口填写 Key，或设置环境变量 DEEPSEEK_API_KEY。")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        try:
            response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise LLMError(f"DeepSeek 请求失败：{exc}") from exc
        except ValueError as exc:
            raise LLMError("DeepSeek 返回内容不是有效 JSON。") from exc

        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("DeepSeek 返回内容格式异常，没有找到 AI 回复。") from exc

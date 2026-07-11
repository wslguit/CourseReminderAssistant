from .llm_client import DeepSeekClient
from .planner import format_learning_context, plan_instruction
from .prompt import SYSTEM_PROMPT


class LearningAgent:
    def __init__(self, api_key=None):
        self.client = DeepSeekClient(api_key=api_key)

    def reply(self, user_message, courses, assignments, history=None):
        context = format_learning_context(courses, assignments)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": "下面是用户当前同步到本地数据库里的学习任务数据，请基于这些数据回答：\n" + context,
            },
        ]
        messages.extend(history or [])
        messages.append({"role": "user", "content": user_message})
        return self.client.chat(messages)

    def generate_today_plan(self, courses, assignments, history=None):
        context = format_learning_context(courses, assignments)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": "下面是用户当前学习任务数据：\n" + context},
        ]
        messages.extend(history or [])
        messages.append({"role": "user", "content": plan_instruction()})
        return self.client.chat(messages)

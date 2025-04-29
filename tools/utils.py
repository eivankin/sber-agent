from typing import Any
from smolagents import Tool, FinalAnswerTool, AgentAudio

from tools.tts_tool import AUDIO_PATH


class GigaChatFinalAnswerTool(Tool):
    """
    Инструмент для взаимодействия агента с пользователем.

    Этот инструмент используется, когда агенту необходимо отправить сообщение
    пользователю. Это может быть как финальный ответ на поставленную задачу,
    так и уточняющий вопрос, если агенту не хватает информации для
    продолжения работы.
    """
    name = "final_answer"
    description = "Отправляет сообщение (например, финальный ответ или уточняющий вопрос) пользователю. Ответ должен быть читаемом человеческом виде, а не как технические логи."
    inputs = {"message": {"type": "string", "description": "Сообщение для отправки пользователю (может быть финальным ответом или уточняющим вопросом к пользователю). "}}
    output_type = "string"

    def forward(self, message: str) -> str:
        """
        Forwards the message to be sent to the user.

        Args:
            message: The content to send to the user.

        Returns:
            The message itself.
        """

        if ".wav" in message:
            return AgentAudio(AUDIO_PATH)
        
        assert isinstance(message, str), "Ответ должен быть в человеческом виде и в формате строки (str)"
        return message

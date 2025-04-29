import torch
import re
import num2words
from pathlib import Path
import warnings

from smolagents import AgentAudio, Tool

AUDIO_PATH = "output_speech.wav"


class TextToSpeechTool(Tool):
    """Инструмент для преобразования текста в речь с использованием Silero TTS."""
    name = "tts_tool"
    description = "Преобразует текст в речь, подготавливая его для произношения. После использования нужно ОБЯЗАТЕЛЬНО передать путь к файлу в final_answer()."
    inputs = {
        "text": {
            "type": "string",
            "description": "Текст для озвучивания на русском языке, слова на других языках и латинице нужно транслитерировать. Пример: gigachat -> гигачат",
        }
    }
    output_type = "audio"

    def __init__(self):
        super().__init__()
        self.device = torch.device('cpu')
        torch.set_num_threads(4)

        # Загрузка модели при инициализации
        local_file = 'silero_model.pt'
        if not Path(local_file).exists():
            torch.hub.download_url_to_file(
                'https://models.silero.ai/models/tts/ru/v4_ru.pt',
                local_file
            )

        self.model = torch.package.PackageImporter(
            local_file).load_pickle("tts_models", "model")
        self.model.to(self.device)

    def _preprocess_text(self, text: str) -> str:
        """Подготовка текста для TTS."""

        # Конвертация чисел в слова
        def replace_number(match):
            num = int(match.group())
            return num2words.num2words(num, lang='ru')

        text = re.sub(r'\d+', replace_number, text)

        # Добавление ударений для улучшения произношения
        stress_dict = {
            'что': 'что+',
            'сейчас': 'сейча+с',
            'когда': 'когда+',
            'потом': 'пото+м',
            # Добавьте другие слова по необходимости
        }

        for word, stressed in stress_dict.items():
            text = re.sub(fr'\b{word}\b', stressed, text, flags=re.IGNORECASE)

        return text

    def forward(self, text: str) -> AgentAudio:
        """Преобразует текст в речь."""
        try:
            processed_text = self._preprocess_text(text)

            # Генерация речи
            sample_rate = 48000
            speaker = 'xenia'

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                audio = self.model.apply_tts(
                    text=processed_text,
                    speaker=speaker,
                    sample_rate=sample_rate
                )

            # Сохранение аудио
            audio = audio.numpy()
            import scipy.io.wavfile as wavfile
            wavfile.write(AUDIO_PATH, sample_rate, audio)

            # Return AgentAudio object instead of dict
            return AgentAudio(AUDIO_PATH)

        except Exception as e:
            raise ValueError(f"Ошибка при генерации речи: {str(e)}")
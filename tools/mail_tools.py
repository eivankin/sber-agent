import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

from deep_translator import GoogleTranslator  
from gigasmol import GigaChatSmolModel
from smolagents import Tool


class Email:
    def __init__(
        self,
        sender: str,
        recipients: List[str],
        subject: str,
        body: str,
        thread_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        email_id: Optional[str] = None
    ):
        self.email_id = email_id or str(uuid.uuid4())
        self.thread_id = thread_id or str(uuid.uuid4())
        self.sender = sender
        self.recipients = recipients
        self.subject = subject
        self.body = body
        self.timestamp = timestamp or datetime.now(timezone.utc)

    def __str__(self) -> str:
        # return (
        #     f"[{self.email_id}] Thread={self.thread_id}\n"
        #     f"From: {self.sender}\nTo: {', '.join(self.recipients)}\n"
        #     f"Subject: {self.subject}\nBody: {self.body}"
        # )
        return self.body


class Mailbox:
    """Упрощённая «база данных» писем + индекс потоков."""

    def __init__(self):
        self._emails: Dict[str, Email] = {}
        self._threads: Dict[str, List[str]] = {}

    def add_email(self, email: Email) -> None:
        """Добавить письмо (или обновить, если ID совпадает)."""
        self._emails[email.email_id] = email
        if email.thread_id not in self._threads:
            self._threads[email.thread_id] = []
        if email.email_id not in self._threads[email.thread_id]:
            self._threads[email.thread_id].append(email.email_id)

    def get_email(self, email_id: str) -> Optional[Email]:
        """Найти письмо по ID."""
        return self._emails.get(email_id)

    def get_thread_emails(self, thread_id: str) -> List[Email]:
        """Вернуть список писем в данном потоке, по порядку добавления."""
        email_ids = self._threads.get(thread_id, [])
        return [self._emails[eid] for eid in email_ids]
    
    def get_thread_emails_as_string(self, thread_id: str) -> Tuple[str, List[str]]:
        """Вернуть список писем в данном потоке, по порядку добавления."""
        emails = self.get_thread_emails(thread_id)
        string_emails = "\n\n".join([f"Письмо ({i+1}) {str(email)}" for i, email in enumerate(emails)])
        list_emails = [f"Письмо ({i+1}) {str(email)}" for i, email in enumerate(emails)]
        return string_emails, list_emails

    def delete_email(self, email_id: str) -> bool:
        """Удалить письмо по email_id. Если поток опустел, удалить и поток."""
        email_obj = self._emails.pop(email_id, None)
        if not email_obj:
            return False
        
        thread_id = email_obj.thread_id
        if thread_id in self._threads:
            self._threads[thread_id] = [eid for eid in self._threads[thread_id] if eid != email_id]
            if not self._threads[thread_id]:
                del self._threads[thread_id]
        return True
    
    def list_threads_with_subjects(self) -> List[Dict[str, str]]:
        """Возвращает список словарей: [{thread_id, subject}, ...]"""
        results = []
        for thread_id in self._threads:
            subject = self.get_thread_subject(thread_id)
            results.append({
                "thread_id": thread_id,
                "subject": subject
            })
        return results
    
    def get_thread_subject(self, thread_id: str) -> str:
        """Пример: взять тему первого письма в потоке как «главную»."""
        email_ids = self._threads.get(thread_id, [])
        if not email_ids:
            return "[Empty Thread]"
        first_email = self._emails[email_ids[0]]
        return first_email.subject

    def get_state_string(self) -> str:
        """Возвращает строковое представление текущего состояния почтового ящика."""
        if not self._emails:
            return "Почтовый ящик пуст."
        output_lines = []        
        threads = self.list_threads_with_subjects()
        if not threads:
            output_lines.append("  Нет активных цепочек писем.")
        else:
            output_lines.append(f"Всего цепочек: {len(threads)}")
            output_lines.append("-" * 40)
            
            for i, thread_info in enumerate(threads):
                output_lines.append(f"ЦЕПОЧКА #{i+1}")
                output_lines.append(f"  • ID: {thread_info['thread_id']}")
                output_lines.append(f"  • Тема: {thread_info['subject']}")
                
                thread_emails = self.get_thread_emails(thread_info['thread_id'])
                output_lines.append(f"  • Писем: {len(thread_emails)}")
                
                if thread_emails:
                    last_email = thread_emails[-1]
                    output_lines.append(f"  • Последнее письмо:")
                    output_lines.append(f"    - От: {last_email.sender}")
                    output_lines.append(f"    - Кому: {', '.join(last_email.recipients)}")
                    output_lines.append(f"    - Дата: {last_email.timestamp.strftime('%d.%m.%Y %H:%M')}")
                    body_preview = last_email.body[:250] + "..." if len(last_email.body) > 250 else last_email.body
                    output_lines.append(f"    - Текст: {body_preview}")
                
                if i < len(threads) - 1:
                    output_lines.append("-" * 30)
            
        return "\n".join(output_lines)


def summarize_thread_content(full_text: str, gigachat: GigaChatSmolModel) -> str:
    """
    Суммаризация содержимого переписки.
    """    
    system_prompt = (
        "Ты — умная система для суммаризации текстов писем (email thread). "
        "Тебе передаётся вся переписка одним куском текста. "
        "Твоя задача — прочитать весь текст и выдать краткое, точное описание (summary) "
        "основных идей, участников, ключевых решений и любых важных действий, обсуждаемых в переписке. "
        "Будь максимально понятным и структурированным, без лишних подробностей. "
        "Отвечай только итоговым текстом суммаризации, не добавляй лишнего формата или пояснений."
    )

    user_prompt = f"""\
Полный текст переписки (thread):
{full_text}

---
Задача:
Сформулируй краткую суммаризацию, отражающую главные моменты переписки, 
ключевые запросы или договорённости, а также участников и их роли (если необходимо). 
Не добавляй ничего, кроме самого текста суммаризации.
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response = gigachat.gigachat_instance.chat(messages=messages)
    return response['answer']


def summarize_thread(mailbox, thread_id: str, gigachat: GigaChatSmolModel) -> str:
    """
    Возвращает суммаризацию переписки (thread).
    """
    emails = mailbox.get_thread_emails(thread_id)
    if not emails:
        return None

    full_text = ""
    for msg in emails:
        full_text += f"\nFrom: {msg.sender}\nSubject: {msg.subject}\nBody:\n{msg.body}\n---\n"
    summary = summarize_thread_content(full_text, gigachat)
    return summary


def generate_auto_reply(
    thread_string: str, 
    last_email_string: str, 
    gigachat: GigaChatSmolModel,
    sender_address: str,
    comment: Optional[str] = None
) -> str:
    """Генерирует текст ответа на основе переписки, последнего письма и опционального комментария.

    Args:
        thread_string: Полная строка переписки.
        last_email_string: Строка последнего письма.
        gigachat: Экземпляр модели GigaChat.
        sender_address: Адрес отправителя.
        comment: Опциональный комментарий/инструкция для генерации ответа.

    Returns:
        Сгенерированный текст ответа.
    """
    system_prompt = (
        "Ты — интеллектуальная система для автоматического составления ответов на письма. "
        "Тебе передаётся вся переписка (тред), а затем — текст последнего письма, на которое нужно ответить. "
        "Твоя задача — сформулировать вежливый и ясный ответ, опираясь на контекст всех предыдущих писем. "
        "Ответ должен быть по существу, с учётом деталей последнего письма. "
        "Выдай итоговый текст письма-ответа (без добавления лишнего формата). "
        "\n\nВажная деталь: если последнее письмо было отправлено с того же адреса, "
        f"от которого ты сейчас отвечаешь (например, если 'From' совпадает с {sender_address}), "
        "продолжай вести диалог от того же лица. То есть не изображай, будто это другой человек, "
        "а просто продолжай предыдущую мысль или уточни, что ты сам имел в виду. "
        "Не нужно менять стиль — оставайся в том же тоне, как будто продолжаешь свой собственный разговор."
        "\n\nЕсли тебе предоставлен дополнительный комментарий или инструкция, используй эту информацию "
        "для формирования ответа. Комментарий может содержать указания о том, что именно нужно включить в ответ, "
        "какой тон использовать или какие конкретные вопросы/темы затронуть. Адаптируй свой ответ в соответствии "
        "с этими инструкциями, сохраняя естественность и связность текста."
    )

    user_prompt_parts = [
        "Вся переписка (thread) целиком:",
        thread_string,
        "\n---",
        "Текст последнего письма (на которое нужно ответить):",
        last_email_string,
        "\n---",
        "Задача:",
        "Напиши короткий вежливый ответ (текст письма), учитывая контекст всей переписки и детали последнего сообщения.",
        f"Если последнее письмо отправлено с того же адреса, что и твой ({sender_address}), продолжай диалог в том же лице и стиле."
    ]

    if comment:
        user_prompt_parts.extend([
            "\n---",
            "Дополнительная инструкция/комментарий к ответу:",
            comment
        ])

    user_prompt = "\n".join(user_prompt_parts)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response = gigachat.gigachat_instance.chat(messages=messages)
    return response['answer']


def auto_reply_to_last_email(
    mailbox: Mailbox,
    thread_id: str,
    gigachat: GigaChatSmolModel,
    sender_address: str,
    comment: Optional[str] = None
) -> Tuple[str, str]:
    """
    Генерирует «автоответ» всем в последнем письме с учетом опционального комментария
    и добавляет письмо в Mailbox как новое письмо в этот же thread.

    Args:
        mailbox: Экземпляр Mailbox.
        thread_id: ID треда для ответа.
        gigachat: Экземпляр модели GigaChat.
        sender_address: Адрес отправителя.
        comment: Опциональный комментарий/инструкция для генерации ответа.

    Returns:
        email_id: ID нового письма.
        reply_text: текст письма-ответа.
    """
    emails = mailbox.get_thread_emails(thread_id)
    if not emails:
        return f"No emails in thread '{thread_id}', cannot reply."

    thread_string, emails_strings = mailbox.get_thread_emails_as_string(thread_id)
    reply_text = generate_auto_reply(
        thread_string=thread_string, 
        last_email_string=emails_strings[-1],
        gigachat=gigachat,
        sender_address=sender_address,
        comment=comment
    )

    last_email = emails[-1]
    participants = set([last_email.sender])  
    participants.update(last_email.recipients)  
    if sender_address in participants:
        participants.remove(sender_address)

    to_addresses = list(participants)
    if not to_addresses:
        to_addresses = [last_email.sender]
        if sender_address in to_addresses:
            to_addresses = []

    new_email_id = str(uuid.uuid4())
    subject = last_email.subject

    reply_email = Email(
        sender=sender_address,           
        recipients=to_addresses,        
        subject=subject,
        body=reply_text,
        thread_id=thread_id,
        timestamp=datetime.now(timezone.utc),
        email_id=new_email_id
    )
    mailbox.add_email(reply_email)
    return new_email_id, reply_text


class BaseMailTool(Tool):
    """Базовый класс для инструментов, взаимодействующих с Mailbox и GigaChat."""
    def __init__(self, mailbox: Mailbox, gigachat: GigaChatSmolModel):
        """Инициализирует базовый инструмент.

        Args:
            mailbox: Экземпляр Mailbox для использования.
            gigachat: Экземпляр GigaChatSmolModel для операций с LLM.
        """
        super().__init__()
        self.mailbox = mailbox
        self.gigachat = gigachat


class ListThreadsTool(BaseMailTool):
    name = "list_email_threads"
    description = "Выводит список всех доступных цепочек писем (тредов) с их темами в виде записей разделённых переносами строк в формате 'ID {string_id}, Тема: {string_subject}'."
    inputs = {}
    output_type = "string"

    def forward(self) -> str:
        """Выводит список всех цепочек писем.

        Returns:
            str: Строка с списком цепочек писем.    
        """
        threads = self.mailbox.list_threads_with_subjects()
        output = "".join([f"ID: {thread['thread_id']}, Тема: {thread['subject']}\n" for thread in threads])
        return output


class GetThreadDetailsTool(BaseMailTool):
    name = "get_email_thread_details"
    description = "Получает полное содержимое всех писем в указанной цепочке (треде)."
    inputs = {
        "thread_id": {
            "type": "string",
            "description": "ID цепочки писем (треда), которую нужно получить.",
        }
    }
    output_type = "string"

    def forward(self, thread_id: str) -> str:
        """Получает детали указанной цепочки писем.

        Args:
            thread_id: ID треда для получения.

        Returns:
            str: Строка, содержащая все письма в указанной цепочке.
            Если цепочка пуста или не найдена, вызывается исключение ValueError.
        """
        thread_id = thread_id.replace("ID: ", "").strip()
        thread_string, thread_list = self.mailbox.get_thread_emails_as_string(thread_id)
        if not thread_list:
            raise ValueError(f"Тред с ID '{thread_id}' не найден или пуст.")
        else:
            return thread_string

class SummarizeThreadTool(BaseMailTool):
    name = "summarize_email_thread"
    description = "Суммаризирует содержимое указанной цепочки писем (треда) с помощью LLM."
    inputs = {
        "thread_id": {
            "type": "string",
            "description": "ID цепочки писем (треда) для суммаризации.",
        }
    }
    output_type = "string"

    def forward(self, thread_id: str) -> str:
        """Суммаризирует указанную цепочку писем.

        Args:
            thread_id: ID треда для суммаризации.

        Returns:
            str: Текст суммаризации, сгенерированный LLM.

        Raises:
            ValueError: Если тред пуст или не существует.
        """
  
        summary = summarize_thread(self.mailbox, thread_id, self.gigachat)
        if summary is None:
            raise ValueError(f"Невозможно суммаризировать тред '{thread_id}': он пуст или не существует.")
        else:
            return summary
      

class GenerateReplyTool(BaseMailTool):
    name = "generate_email_reply"
    description = "Генерирует ответ на последнее письмо в треде с помощью LLM, учитывая опциональный комментарий, и добавляет его в почтовый ящик."
    inputs = {
        "thread_id": {
            "type": "string",
            "description": "ID цепочки писем (треда), на которую нужно ответить.",
        },
        "sender_address": {
            "type": "string",
            "description": "Адрес электронной почты, который агент должен использовать в качестве отправителя.",
            "nullable": True,
            "default": "agent@example.com"
        },
        "comment": {
            "type": "string",
            "description": "Опциональный комментарий или инструкция для генерации ответа. Не является текстом письма, только указывает направление, в котором нужно сгенерировать ответ.",
            "nullable": True
        }
    }
    output_type = "string"

    def forward(self, thread_id: str, sender_address: Optional[str] = "agent@example.com", comment: Optional[str] = None) -> str:
        """Генерирует и отправляет ответ на последнее письмо в треде с учетом комментария.

        Args:
            thread_id: ID треда, на который нужно ответить.
            sender_address: Адрес электронной почты для отправки ответа.
            comment: Опциональный комментарий/инструкция для генерации ответа.

        Returns:
            str: Сообщение о результате операции и текст сгенерированного письма-ответа.
        """
        emails = self.mailbox.get_thread_emails(thread_id)
        if not emails:
            raise ValueError(f"Невозможно ответить на тред '{thread_id}': он пуст или не существует. Доступные треды: {self.mailbox.list_threads_with_subjects()}")

        reply_email_id, reply_text = auto_reply_to_last_email(
            mailbox=self.mailbox,
            thread_id=thread_id,
            gigachat=self.gigachat,
            sender_address=sender_address,
            comment=comment 
        )
        if reply_email_id.startswith("No emails") or reply_email_id.startswith("No email content"):
            raise ValueError(f"Ошибка при генерации ответа для треда '{thread_id}': {reply_email_id}")
        elif not self.mailbox.get_email(reply_email_id):
            raise ValueError(f"Ошибка при добавлении письма-ответа для треда '{thread_id}'. Письмо не найдено.")
        else:
            return f"Ответ на тред '{thread_id}' успешно сгенерирован и добавлен.\nCгенерированное письмо:\n{reply_text}"


class TranslateTool(BaseMailTool):
    name = "translate_email_thread"
    description = "Переводит содержимое указанной цепочки писем (треда) на указанный язык с помощью Google Translate API."
    inputs = {
        "thread_id": {
            "type": "string",
            "description": "ID цепочки писем (треда) для перевода.",
        },
        "language": {
            "type": "string",
            "description": "Язык, на который нужно перевести содержимое треда (например, 'en', 'ru', 'de').",
        }
    }
    output_type = "string"

    def forward(self, thread_id: str, language: str) -> str:
        """Переводит содержимое указанной цепочки писем (треда) на указанный язык.

        Args:
            thread_id: ID треда для перевода.
            language: Язык, на который нужно перевести содержимое треда (например, 'en', 'ru', 'de').

        Returns:
            Сообщение о результате операции и текст перевода.
        """
        thread_string, thread_list = self.mailbox.get_thread_emails_as_string(thread_id)
        if not thread_list:
            raise ValueError(f"Тред с ID '{thread_id}' не найден или пуст.")

        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source='auto', target=language.lower())
        translated_text = translator.translate(thread_string)
        if translated_text is None:
            raise ValueError(f"Ошибка при переводе треда '{thread_id}' на язык '{language}': переводчик вернул пустой результат.")

        return f"Тред '{thread_id}' успешно переведен на язык '{language}'. Текст перевода: {translated_text}"


class MailToolset:
    """Предоставляет набор инструментов для взаимодействия с Mailbox и GigaChat."""
    def __init__(self, mailbox: Mailbox, gigachat: GigaChatSmolModel):
        """Инициализирует набор инструментов.

        Args:
            mailbox: Экземпляр Mailbox для использования.
            gigachat: Экземпляр GigaChatSmolModel для операций с LLM.
        """
        self.mailbox = mailbox
        self.gigachat = gigachat
        self.tools = [
            ListThreadsTool(self.mailbox, self.gigachat),
            GetThreadDetailsTool(self.mailbox, self.gigachat),
            SummarizeThreadTool(self.mailbox, self.gigachat),
            GenerateReplyTool(self.mailbox, self.gigachat),
            TranslateTool(self.mailbox, self.gigachat)
        ]

    def get_tools(self) -> List[Tool]:
        """Возвращает список доступных инструментов для работы с почтой."""
        return self.tools
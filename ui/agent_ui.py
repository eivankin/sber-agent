#!/usr/bin/env python
# coding=utf-8
# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import re
import shutil
from typing import Optional, Dict, Any, TYPE_CHECKING, List, Tuple

from smolagents.agent_types import AgentAudio, AgentImage, AgentText, handle_agent_output_types
from smolagents.agents import ActionStep, MultiStepAgent
from smolagents.memory import MemoryStep
from smolagents.utils import _is_package_available

# Type checking imports to avoid circular dependencies if these classes are moved
if TYPE_CHECKING:
    from tools.calendar_tools import Calendar # Assuming calendar_tools.py is in the root
    from tools.mail_tools import Mailbox # Assuming mail_tools.py is in the root

def pull_messages_from_step(
    step_log: MemoryStep,
    additional_metadata: Optional[Dict[str, Any]] = None,
):
    """Извлекает объекты ChatMessage из шагов агента с правильной вложенностью"""
    import gradio as gr

    base_metadata = additional_metadata or {}

    if isinstance(step_log, ActionStep):
        # Вывод номера шага
        step_number_text = f"Шаг {step_log.step_number}" if step_log.step_number is not None else "Шаг"
        yield gr.ChatMessage(role="assistant", content=f"**{step_number_text}**", metadata={**base_metadata})

        # Сначала выводим мысль/рассуждение от LLM
        if hasattr(step_log, "model_output") and step_log.model_output is not None:
            # Очистка вывода LLM
            model_output = step_log.model_output.strip()
            # Remove any trailing <end_code> and extra backticks, handling multiple possible formats
            model_output = re.sub(r"```\s*<end_code>", "```", model_output)  # handles ```<end_code>
            model_output = re.sub(r"<end_code>\s*```", "```", model_output)  # handles <end_code>```
            model_output = re.sub(r"```\s*\n\s*<end_code>", "```", model_output)  # handles ```\n<end_code>
            model_output = model_output.strip()
            yield gr.ChatMessage(role="assistant", content=model_output, metadata={**base_metadata})

        # Для вызовов инструментов создаем родительское сообщение
        if hasattr(step_log, "tool_calls") and step_log.tool_calls is not None:
            first_tool_call = step_log.tool_calls[0]
            used_code = first_tool_call.name == "python_interpreter"
            parent_id = f"call_{len(step_log.tool_calls)}"

            # Вызов инструмента становится родительским сообщением с информацией о времени
            args = first_tool_call.arguments
            if isinstance(args, dict):
                content = str(args.get("answer", str(args)))
            else:
                content = str(args).strip()

            if used_code:
                # Очистка контента от тегов <end_code>
                content = re.sub(r"```.*?\n", "", content)
                content = re.sub(r"\s*<end_code>\s*", "", content)
                content = content.strip()
                if not content.startswith("```python"):
                    content = f"```python\n{content}\n```"

            tool_metadata = {
                "title": f"🛠️ Использован инструмент: {first_tool_call.name}",
                "id": parent_id,
                "status": "pending",
                **base_metadata,
            }
            parent_message_tool = gr.ChatMessage(
                role="assistant",
                content=content,
                metadata=tool_metadata,
            )
            yield parent_message_tool

            # Вложение логов выполнения под вызовом инструмента, если они есть
            if hasattr(step_log, "observations") and (
                step_log.observations is not None and step_log.observations.strip()
            ):
                log_content = step_log.observations.strip()
                if log_content:
                    log_content = re.sub(r"^Execution logs:\s*", "", log_content)
                    log_metadata = {
                        "title": "📝 Логи выполнения",
                        "parent_id": parent_id,
                        "status": "done",
                        **base_metadata,
                    }
                    yield gr.ChatMessage(
                        role="assistant",
                        content=f"```bash\n{log_content}\n",
                        metadata=log_metadata,
                    )

            # Вложение ошибок под вызовом инструмента
            if hasattr(step_log, "error") and step_log.error is not None:
                error_metadata = {
                    "title": "💥 Ошибка выполнения",
                    "parent_id": parent_id,
                    "status": "done",
                    **base_metadata,
                }
                yield gr.ChatMessage(
                    role="assistant",
                    content=str(step_log.error),
                    metadata=error_metadata,
                )

            parent_message_tool.metadata["status"] = "done"

        # Обработка отдельных ошибок (не от вызовов инструментов)
        elif hasattr(step_log, "error") and step_log.error is not None:
            error_metadata = {"title": "💥 Ошибка", **base_metadata}
            yield gr.ChatMessage(role="assistant", content=str(step_log.error), metadata=error_metadata)

        # Расчет длительности и информации о токенах
        step_footnote = f"{step_number_text}"
        if hasattr(step_log, "input_token_count") and hasattr(step_log, "output_token_count"):
            token_str = (
                f" | Входные токены:{step_log.input_token_count:,} | Выходные токены:{step_log.output_token_count:,}"
            )
            step_footnote += token_str
        if hasattr(step_log, "duration"):
            step_duration = f" | Длительность: {round(float(step_log.duration), 2)} сек." if step_log.duration else ""
            step_footnote += step_duration
        step_footnote = f"""<span style="color: #bbbbc2; font-size: 12px;">{step_footnote}</span> """
        yield gr.ChatMessage(role="assistant", content=f"{step_footnote}", metadata={**base_metadata})
        yield gr.ChatMessage(role="assistant", content="-----", metadata={"status": "done", **base_metadata})


def stream_to_gradio(
    agent,
    task: str,
    reset_agent_memory: bool = False,
    additional_args: Optional[dict] = None,
):
    """Запускает агента с заданной задачей и передает сообщения от агента в виде gradio ChatMessages."""
    if not _is_package_available("gradio"):
        raise ModuleNotFoundError(
            "Пожалуйста, установите 'gradio' extra для использования GradioUI: `pip install 'smolagents[gradio]'`"
        )
    import gradio as gr

    total_input_tokens = 0
    total_output_tokens = 0

    step_metadata = {"is_step": True}

    for step_log in agent.run(task, stream=True, reset=reset_agent_memory, additional_args=additional_args):
        if getattr(agent.model, "last_input_token_count", None) is not None:
            total_input_tokens += agent.model.last_input_token_count
            total_output_tokens += agent.model.last_output_token_count
            if isinstance(step_log, ActionStep):
                step_log.input_token_count = agent.model.last_input_token_count
                step_log.output_token_count = agent.model.last_output_token_count

        for message in pull_messages_from_step(step_log, additional_metadata=step_metadata):
            yield message

    final_answer = step_log
    final_answer = handle_agent_output_types(final_answer)

    if isinstance(final_answer, str) and ".wav" in final_answer:
        final_answer = AgentAudio(final_answer)

    final_answer_metadata = {}

    if isinstance(final_answer, AgentText):
        yield gr.ChatMessage(
            role="assistant",
            content=f"**Агент:**\n{final_answer.to_string()}\n",
            metadata=final_answer_metadata,
        )
    elif isinstance(final_answer, AgentImage):
        yield gr.ChatMessage(
            role="assistant",
            content={"path": final_answer.to_string(), "mime_type": "image/png"},
            metadata=final_answer_metadata,
        )
    elif isinstance(final_answer, AgentAudio):
        yield gr.ChatMessage(
            role="assistant",
            content={"path": final_answer.to_string(), "mime_type": "audio/wav"},
            metadata=final_answer_metadata,
        )
    else:
        yield gr.ChatMessage(role="assistant", content=f"**Агент:** {str(final_answer)}", metadata=final_answer_metadata)


class GradioUI:
    """Интерфейс для запуска агента в Gradio"""

    def __init__(
        self,
        agent: MultiStepAgent,
        mailbox: Optional['Mailbox'] = None,
        calendar: Optional['Calendar'] = None,
        file_upload_folder: str | None = None,
        hide_steps: bool = False
    ):
        if not _is_package_available("gradio"):
            raise ModuleNotFoundError(
                "Пожалуйста, установите 'gradio' extra для использования GradioUI: `pip install 'smolagents[gradio]'`"
            )
        import gradio as gr # Import gradio here
        self.gr = gr # Store gradio for later use without repeated imports
        self.agent = agent
        self.mailbox = mailbox
        self.calendar = calendar
        self.file_upload_folder = file_upload_folder
        self.hide_steps = hide_steps
        self.name = getattr(agent, "name") or "Интерфейс Агента"
        self.description = getattr(agent, "description", None)
        if self.file_upload_folder is not None:
            if not os.path.exists(file_upload_folder):
                os.mkdir(file_upload_folder)

                # Add Whisper model initialization
        try:
             import whisper
             self.whisper_model = whisper.load_model("base")
        except ImportError:
            print("Warning: whisper not installed. Voice input will be disabled.")
            self.whisper_model = None

    def transcribe_audio(self, audio):
        """Transcribes audio input using Whisper model"""
        if self.whisper_model is None:
            return "Error: Whisper model not available. Please install whisper-openai package."

        if audio is None:
            return ""

        try:
            result = self.whisper_model.transcribe(audio)
            return result["text"].strip()
        except Exception as e:
            print(f"Transcription error: {e}")
            return f"Error during transcription: {str(e)}"

    def interact_with_agent(self, prompt: str, messages: List[Dict[str, Any]], session_state: Dict[str, Any]):
        """Handles the interaction with the agent and yields chat messages."""
        import gradio as gr # Ensure gr is available

        if "agent" not in session_state:
            session_state["agent"] = self.agent

        try:
            # messages.append(gr.ChatMessage(role="user", content=prompt))
            yield messages

            stream = stream_to_gradio(session_state["agent"], task=prompt, reset_agent_memory=False)

            final_message_list = list(stream) # Consume the stream

            for msg in final_message_list:
                is_step_message = msg.metadata is not None and msg.metadata.get("is_step", False)
                if self.hide_steps and is_step_message:
                    continue
                messages.append(msg)

            yield messages # Yield the final list of messages

        except Exception as e:
            print(f"Ошибка взаимодействия: {str(e)}")
            messages.append(gr.ChatMessage(role="assistant", content=f"Ошибка: {str(e)}"))
            yield messages

    def _update_mailbox_display(self) -> Tuple[Dict, List, Dict]:
        """
        Updates the mailbox thread list and the internal mapping.

        Returns:
            Tuple[Dict, List, Dict]: A tuple containing:
                - Gradio Radio update dictionary.
                - List of thread choices for display.
                - Dictionary mapping display choices to thread IDs.
        """
        if not self.mailbox:
            # Return parameters for a hidden Radio component
            return {"choices": [], "label": "Mailbox", "value": None, "visible": False, "interactive": False}, [], {}

        threads = self.mailbox.list_threads_with_subjects()
        if not threads:
            choices = ["Почтовый ящик пуст."]
            thread_id_map = {}
            # Return parameters for a non-interactive Radio component
            return {"choices": choices, "label": "Цепочки писем", "value": choices[0], "interactive": False, "visible": True}, choices, thread_id_map
        else:
            choices = [f"{t['subject']} (ID: ...{t['thread_id'][-6:]})" for t in threads]
            thread_id_map = {f"{t['subject']} (ID: ...{t['thread_id'][-6:]})": t['thread_id'] for t in threads}
            # Return parameters for an interactive Radio component
            return {"choices": choices, "label": "Цепочки писем", "value": None, "interactive": True, "visible": True}, choices, thread_id_map

    def _display_thread_content(self, selected_choice: Optional[str], thread_id_map: Dict[str, str]) -> Dict[str, Any]:
        """
        Fetches and formats the content of the selected email thread.

        Args:
            selected_choice: The choice selected in the Radio button (display string).
            thread_id_map: The mapping from display choices to thread IDs.

        Returns:
            Dict: Gradio Markdown update dictionary.
        """
        if not self.mailbox or not selected_choice or selected_choice == "Почтовый ящик пуст." or selected_choice not in thread_id_map:
            return self.gr.Markdown(value="Выберите цепочку для просмотра.", visible=True)

        thread_id = thread_id_map.get(selected_choice)
        if not thread_id:
            return self.gr.Markdown(value=f"Ошибка: Не найден ID для '{selected_choice}'.", visible=True)

        try:
            thread_string, _ = self.mailbox.get_thread_emails_as_string(thread_id)
            if not thread_string:
                content = f"**Цепочка '{selected_choice}' пуста.**"
            else:
                # Format as markdown code block for readability
                content = f"### Содержимое цепочки: {selected_choice}\n\n```text\n{thread_string}\n```"
            return self.gr.Markdown(value=content, visible=True)
        except Exception as e:
            print(f"Ошибка при получении содержимого треда {thread_id}: {e}")
            return self.gr.Markdown(value=f"**Ошибка при загрузке цепочки '{selected_choice}'.**", visible=True)


    def _update_calendar_display(self) -> Dict[str, Any]:
        """Updates the calendar state display."""
        if self.calendar:
            calendar_state = self.calendar.get_state_string()
            return self.gr.Textbox(value=calendar_state, visible=True)
        else:
            return self.gr.Textbox(visible=False)


    def upload_file(self, file, file_uploads_log, allowed_file_types=None):
        """
        Обработка загрузки файлов, по умолчанию разрешены типы .pdf, .docx и .txt
        """
        import gradio as gr

        if file is None:
            return gr.Textbox(value="Файл не загружен", visible=True), file_uploads_log

        if allowed_file_types is None:
            allowed_file_types = [".pdf", ".docx", ".txt"]

        file_ext = os.path.splitext(file.name)[1].lower()
        if file_ext not in allowed_file_types:
            return gr.Textbox("Тип файла не разрешен", visible=True), file_uploads_log

        original_name = os.path.basename(file.name)
        sanitized_name = re.sub(
            r"[^\w\-.]", "_", original_name
        )

        file_path = os.path.join(self.file_upload_folder, os.path.basename(sanitized_name))
        shutil.copy(file.name, file_path)

        return gr.Textbox(f"Файл загружен: {file_path}", visible=True), file_uploads_log + [file_path]

    def log_user_message(self, text_input, file_uploads_log):
        import gradio as gr

        return (
            text_input
            + (
                f"\nВам предоставлены следующие файлы, которые могут быть полезны: {file_uploads_log}"
                if len(file_uploads_log) > 0
                else ""
            ),
            "",
            gr.Button(interactive=False),
        )

    def launch(self, share: bool = True, **kwargs):
        import gradio as gr

        # CSS to make the sidebar wider (adjust percentage as needed)
        # custom_css = """
        # .gradio-container > .main > .wrap {
        #     grid-template-columns: 75% 1fr !important; /* Adjust first value (sidebar width) */
        # }
        # """

        with gr.Blocks(theme="ocean", fill_height=True) as demo: # Removed css=custom_css
            session_state = gr.State({})
            stored_messages = gr.State([]) # This might not be needed if chatbot handles history
            file_uploads_log = gr.State([])
            thread_id_map = gr.State({}) # State to store the mapping

            # Use gr.Row to control overall layout
            with gr.Row():
                # Sidebar content in a Column with scale
                with gr.Column(scale=1):
                    gr.Markdown(
                        f"# {self.name.replace('_', ' ').capitalize()}"
                        "\n> Этот веб-интерфейс позволяет вам взаимодействовать с агентом, который может использовать инструменты и выполнять шаги для решения задач."
                        + (f"\n\n**Описание агента:**\n{self.description}" if self.description else "")
                    )

                    with gr.Group():
                        gr.Markdown("**Ваш запрос**", container=True)
                        text_input = gr.Textbox(
                            lines=3,
                            label="Текстовое сообщение",
                            container=False,
                            placeholder="Введите ваш запрос здесь и нажмите Shift+Enter или кнопку Отправить",
                            scale=7
                        )

                        # Voice input
                        with gr.Column(scale=1):
                            audio_input = gr.Audio(
                                # source="microphone",
                                type="filepath",
                                label="Голосовой ввод",
                                visible=self.whisper_model is not None
                            )

                    with gr.Row():
                        submit_btn = gr.Button("Отправить", variant="primary", scale=1, interactive=False)
                        clear_btn = gr.Button("Очистить", scale=1)

                    if self.file_upload_folder is not None:
                        with gr.Group():
                            upload_file = gr.File(label="Загрузить файл")
                            upload_status = gr.Textbox(label="Статус загрузки", interactive=False, visible=False)
                            upload_file.change(
                                self.upload_file,
                                inputs=[upload_file, file_uploads_log],
                                outputs=[upload_status, file_uploads_log],
                                show_progress="hidden",
                            )

                    
                    gr.Markdown("\n> Состояние базы данных обновляется после каждого ответа агента. \nКаждое из окон пролистывается. ")
                    if self.calendar:
                        gr.Markdown("**Состояние Календаря**")

                        calendar_state_display = gr.Textbox(
                            label="Календарь",
                            value=self.calendar.get_state_string,
                            lines=8,
                            interactive=False,
                            max_lines=15
                        )
                    else:
                        calendar_state_display = gr.Textbox(visible=False) 

                    if self.mailbox:
                        gr.Markdown("**Состояние Почты**")
                        initial_radio_params, initial_choices, initial_map = self._update_mailbox_display()
                        thread_id_map.value = initial_map
                        mailbox_thread_selector = gr.Radio(
                            label=initial_radio_params.get("label", "Цепочки писем"),
                            choices=initial_choices, 
                            value=initial_radio_params.get("value"),
                            interactive=initial_radio_params.get("interactive", False),
                            visible=initial_radio_params.get("visible", True),
                            container=True
                        )
                        mailbox_thread_content_display = gr.Markdown(
                            value="Выберите цепочку для просмотра.",
                            visible=True,
                            elem_classes="email-content"
                        )
                    else:
                        mailbox_thread_selector = gr.Radio(visible=False)
                        mailbox_thread_content_display = gr.Markdown(visible=False)


                    gr.HTML('<br><br><h4><center>Сбер / Блок "Риски"</center></h4>')
                    with gr.Row():
                        gr.HTML("""<div style="display: flex; align-items: center; justify-content: center; gap: 8px; font-family: system-ui, -apple-system, sans-serif;">
                <img src="https://raw.githubusercontent.com/poteminr/gigasmol/refs/heads/main/assets/logo.png" style="width: 64px; height: 64px; object-fit: contain;" alt="logo">
                <a target="_blank" href="https://github.com/poteminr/gigasmol"><b>poteminr/gigasmol</b></a>
                </div>""")

                # Main chat interface in a Column with scale
                with gr.Column(scale=1):
                    chatbot = gr.Chatbot(
                        label="Агент",
                        type="messages",
                        height=700, # Set a height
                        show_label=False # Label is redundant with markdown title
                    )

            # --- Event Handlers ---

            # Define the function to run after agent interaction
            def update_displays_after_interaction():
                outputs = {}
                if self.mailbox:
                    radio_update_params, _, new_map = self._update_mailbox_display()
                    # Use gr.update() with the dictionary of parameters
                    outputs[mailbox_thread_selector] = gr.update(**radio_update_params)
                    outputs[thread_id_map] = new_map
                    # Clear content display when threads refresh
                    outputs[mailbox_thread_content_display] = gr.Markdown(value="Выберите цепочку для просмотра.")
                else: # Provide dummy updates if no mailbox
                    outputs[mailbox_thread_selector] = gr.Radio(visible=False)
                    outputs[thread_id_map] = {}
                    outputs[mailbox_thread_content_display] = gr.Markdown(visible=False)

                if self.calendar:
                     outputs[calendar_state_display] = self._update_calendar_display()
                else:
                    outputs[calendar_state_display] = gr.Textbox(visible=False) # Provide dummy update

                # Return values in the order expected by the .then() call
                # The order must match the outputs list defined below in handle_submit_and_update
                # chatbot, mailbox_thread_selector, thread_id_map, mailbox_thread_content_display, calendar_state_display
                return (
                    # Chatbot is updated within handle_submit_and_update
                    outputs.get(mailbox_thread_selector), # Radio update
                    outputs.get(thread_id_map),           # Map update
                    outputs.get(mailbox_thread_content_display), # Content update
                    outputs.get(calendar_state_display)  # Calendar update
                )

            # Define the function to re-enable inputs
            # def reset_inputs():
            #     return (
            #         gr.Textbox(
            #             interactive=True, placeholder="Введите ваш запрос здесь и нажмите Shift+Enter или кнопку Отправить"
            #         ),
            #         gr.Button(interactive=True),
            #     )

            def handle_audio(audio):
                if audio is None:
                    return gr.Textbox(), gr.Button(interactive=False)
                transcription = self.transcribe_audio(audio)
                # Enable submit button if transcription is not empty
                return (
                    gr.Textbox(value=transcription),
                    gr.Button(interactive=bool(transcription.strip()))
                )

            def handle_text_input(text):
                # Enable submit button if text input is not empty
                return gr.Button(interactive=bool(text.strip()))

            # Connect audio input to text input
            # audio_input.change(
            #     handle_audio,
            #     inputs=[audio_input],
            #     outputs=[text_input],
            #     show_progress=True
            # )

            # Add clear button handler
            clear_btn.click(
                lambda: (gr.Textbox(value=""), gr.Audio(value=None)),
                outputs=[text_input, audio_input]
            )

            # --- Submit Logic (Button and Enter key) ---
            submit_inputs = [text_input, chatbot, session_state, file_uploads_log]
            submit_outputs = [
                chatbot,
                mailbox_thread_selector,
                thread_id_map,
                mailbox_thread_content_display,
                calendar_state_display,
                text_input,
                submit_btn,
                audio_input  # Add audio input to reset after submission
            ]

            # Update the reset_inputs function
            def reset_inputs():
                return (
                    gr.Textbox(interactive=True,
                               placeholder="Введите ваш запрос здесь и нажмите Shift+Enter или кнопку Отправить"),
                    gr.Button(interactive=True),
                    gr.Audio(value=None)  # Clear audio input
                )

            def handle_submit_and_update(prompt, history, state, current_file_log):
                # 1. Log user message (prepare prompt) and disable inputs
                processed_prompt, _, _ = self.log_user_message(prompt, current_file_log)
                # Determine initial state for outputs before agent interaction
                current_radio = gr.Radio()  # Default update (no change)
                current_map = state.get("thread_id_map", {})
                current_content = gr.Markdown()  # Default update
                current_calendar = gr.Textbox()  # Default update
                if self.mailbox:
                    current_radio_params, _, _ = self._update_mailbox_display()  # Get current params for update
                    current_radio = gr.update(**current_radio_params)  # Ensure it keeps its state
                if self.calendar:
                    current_calendar = self._update_calendar_display()

                # Append user message as a ChatMessage object
                user_message = gr.ChatMessage(role="user", content=prompt)
                updated_initial_history = history + [user_message]

                yield (
                    updated_initial_history,  # Show user message immediately using ChatMessage
                    current_radio,  # Keep radio state
                    current_map,  # Keep map
                    current_content,  # Keep content
                    current_calendar,  # Keep calendar state
                    gr.Textbox(value="", interactive=False),  # Clear and disable input
                    gr.Button(interactive=False),  # Disable button
                    gr.Audio(value=None)  # Reset audio input
                )

                # 2. Interact with the agent (updates history in place)
                agent_gen = self.interact_with_agent(processed_prompt, updated_initial_history, state)
                final_history = None
                for updated_history in agent_gen:
                    final_history = updated_history  # Store the last yielded history

                # Ensure final_history has the latest state
                if final_history is None:
                    final_history = updated_initial_history  # Use history with user message if generator was empty

                # 3. Update state displays (Mailbox, Calendar)
                radio_update, new_map, content_update, calendar_update = update_displays_after_interaction()

                # 4. Reset input controls
                text_input_update, submit_btn_update, audio_input_update = reset_inputs()

                # 5. Yield final state - ensure the order matches the submit_outputs list
                yield (
                    final_history,
                    radio_update,
                    new_map,
                    content_update,
                    calendar_update,
                    text_input_update,
                    submit_btn_update,
                    audio_input_update
                )

            submit_btn.click(
                handle_submit_and_update,
                inputs=submit_inputs,
                outputs=submit_outputs,
                #api_name="agent_chat" # Optional API name
            )
            text_input.submit(
                handle_submit_and_update,
                inputs=submit_inputs,
                outputs=submit_outputs,
                #api_name=False # Disable API for enter key?
            )

            text_input.change(
                handle_text_input,
                inputs=[text_input],
                outputs=[submit_btn]
            )

            audio_input.change(
                handle_audio,
                inputs=[audio_input],
                outputs=[text_input, submit_btn]
            )


            # --- Mailbox Thread Selection Logic ---
            if self.mailbox:
                mailbox_thread_selector.change(
                    self._display_thread_content,
                    inputs=[mailbox_thread_selector, thread_id_map],
                    outputs=[mailbox_thread_content_display],
                    show_progress="hidden"
                )


        demo.launch(debug=True, share=share, **kwargs)


__all__ = ["stream_to_gradio", "GradioUI"]

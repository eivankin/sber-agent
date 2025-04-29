import json
from gigasmol import GigaChatSmolModel
from smolagents import CodeAgent, ToolCallingAgent, Tool, UserInputTool


from tools.mail_tools import MailToolset
from tools.calendar_tools import CalendarToolset
from tools.basic_tools import CurrencyConversionTool, WeatherTool, TimeTool
from tools.utils import GigaChatFinalAnswerTool
from ui.agent_ui import GradioUI
from examples.mailbox_example import MAILBOX_EXAMPLE
from examples.calendar_example import CALENDAR_EXAMPLE

from prompts import GIGACHAT_AGENT_SYSTEM_PROMPT

credentials = json.load(open('credentials.json', 'r'))

model = GigaChatSmolModel(
    auth_data=credentials['gigachat_authorization_key'],
    client_id=None,
    model_name="GigaChat-2-Max",
    temperature=0.1,
    top_p=0.9,
    repetition_penalty=1.1,
    max_tokens=64000,
)

calendar = CALENDAR_EXAMPLE
mailbox = MAILBOX_EXAMPLE

agent = CodeAgent(
    tools=[
        CurrencyConversionTool(credentials['currency_api_key']),
        WeatherTool(credentials['weather_api_key']),
        TimeTool()] +
        MailToolset(mailbox, model).tools +
        CalendarToolset(calendar).get_tools(),
    additional_authorized_imports=["datetime"],
    model=model
)

agent.tools['final_answer'] = GigaChatFinalAnswerTool()
agent.system_prompt = GIGACHAT_AGENT_SYSTEM_PROMPT

GradioUI(agent, mailbox, calendar, hide_steps=True).launch()
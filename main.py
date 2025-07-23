# Copyright 2025 Elshan Isayev Elman Oglu
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

import json
import disnake
from disnake.ext import commands

intents = disnake.Intents(
    guilds=True,            # чтобы бот знал о серверах
    voice_states=True,      # для работы в голосовых каналах
    messages=False,         # текстовые сообщения не нужны
    message_content=False   # не требуется, если не парсишь текст
)

bot = commands.InteractionBot(intents=intents, reload=False)

@bot.event
async def on_ready():
    print("Бот готов!")

bot.load_extensions('cogs')
print("Загруженные Cogs:", bot.cogs.keys())

with open("secrets.json", "r", encoding="utf-8") as file:
    t = json.load(file)
    TOKEN = t["TOKEN"]

bot.run(TOKEN)

import json
import subprocess
import sys
import time

import disnake
from disnake.ext import commands

# ---------- Определяем режим ----------
is_second = len(sys.argv) > 1 and sys.argv[1] == "sfx2"

# ---------- Загрузка токенов ----------
with open("secrets.json", "r", encoding="utf-8") as file:
    t = json.load(file)
    TOKEN_SFX = t["SFX_TOKEN"]
    TOKEN_SFX2 = t["SFX_TOKEN2"]

TOKEN = TOKEN_SFX2 if is_second else TOKEN_SFX

intents = disnake.Intents(
    guilds=True,
    voice_states=True,
    messages=False,
    message_content=False
)

bot = commands.InteractionBot(intents=intents, reload=False)

@bot.event
async def on_ready():
    print(f"✅ Бот готов! [{bot.user} | {'SFX2' if is_second else 'SFX'}]")

bot.guild_players = {}

# ---------- Запуск ----------
if not is_second: subprocess.Popen([sys.executable, __file__, "sfx2"])
print("🚀 Второй бот (SFX) запущен как отдельный процесс")

bot.load_extensions('cogs')
print("Загруженные Cogs:", list(bot.cogs.keys()))

bot.run(TOKEN)
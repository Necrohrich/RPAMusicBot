import json
import subprocess
import sys

import disnake
from disnake.ext import commands

# ---------- Определяем режим ----------
is_second = len(sys.argv) > 1 and sys.argv[1] == "music2"

# ---------- Загрузка токенов ----------
with open("secrets.json", "r", encoding="utf-8") as file:
    t = json.load(file)
    TOKEN_MUSIC = t["MUSIC_TOKEN"]
    TOKEN_MUSIC2 = t["MUSIC_TOKEN2"]

TOKEN = TOKEN_MUSIC2 if is_second else TOKEN_MUSIC

intents = disnake.Intents(
    guilds=True,
    voice_states=True,
    messages=False,
    message_content=False
)

bot = commands.InteractionBot(intents=intents, reload=False)

@bot.event
async def on_ready():
    print(f"✅ Бот готов! [{bot.user} | {'Music2' if is_second else 'Music'}]")

bot.guild_players = {}

# ---------- Запуск ----------
if not is_second: subprocess.Popen([sys.executable, __file__, "music2"])
print("🚀 Второй бот (SFX) запущен как отдельный процесс")

bot.load_extensions('cogs')
print("Загруженные Cogs:", list(bot.cogs.keys()))

bot.run(TOKEN)
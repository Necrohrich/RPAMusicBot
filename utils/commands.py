import os

import disnake

from utils.audio import AudioSourceManager
from utils.functions import get_user_folder, ensure_voice

async def play_command(guild_players, inter: disnake.ApplicationCommandInteraction, track_type:str, filename: str):
    await inter.response.defer()
    guild_id = inter.guild_id

    if not inter.author.voice or not inter.author.voice.channel:
        await inter.edit_original_message("Вы должны быть в голосовом канале!")
        return

    voice_channel = inter.author.voice.channel

    player = guild_players.get(guild_id)
    if not player:
        player = AudioSourceManager(guild_id)
        guild_players[guild_id] = player

    await ensure_voice(player, voice_channel)

    user_folder = get_user_folder(track_type, inter.author.id)
    path = os.path.join(user_folder, filename)
    if not os.path.exists(path):
        await inter.edit_original_message("Трек не найден.")
        return

    await player.play(path, track_type=track_type)
    await inter.edit_original_message(f"🎵 Проигрывается: `{filename}` из категории `{track_type}`")

import os

import disnake

from utils.audio import AudioSourceManager
from utils.functions import get_user_folder, ensure_voice

async def play_command(guild_players, inter: disnake.ApplicationCommandInteraction | disnake.MessageInteraction, track_type:str, filename: str):
    await inter.response.defer(ephemeral=True)
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

async def pause_command(player, inter: disnake.ApplicationCommandInteraction | disnake.MessageInteraction):
    if not player or not player.voice or not player.voice.is_playing():
        await inter.response.send_message("Нечего ставить на паузу.", ephemeral=True)
        return
    player.pause()
    await inter.response.send_message("⏸ Пауза", ephemeral=True)

async def resume_command(player, inter: disnake.ApplicationCommandInteraction | disnake.MessageInteraction):
    if not player or not player.voice or not player.voice.is_paused():
        await inter.response.send_message("Нечего продолжать.", ephemeral=True)
        return
    player.resume()
    await inter.response.send_message("▶ Продолжено", ephemeral=True)

async def stop_command(player, inter: disnake.ApplicationCommandInteraction | disnake.MessageInteraction):
    if not player or not player.voice or not player.voice.is_playing():
        await inter.response.send_message("Нечего останавливать.", ephemeral=True)
        return
    player.stop()
    await inter.response.send_message("⏹ Остановлено", ephemeral=True)

async def loop_command(player, inter: disnake.ApplicationCommandInteraction | disnake.MessageInteraction, enable):
    if not player or not player.current_type:
        await inter.response.send_message("Плеер не инициализирован или нет текущего трека.", ephemeral=True)
        return
    player.set_loop(player.current_type, enable)
    status = "включён" if enable else "выключен"
    await inter.response.send_message(f"Повтор трека {status}.", ephemeral=True)
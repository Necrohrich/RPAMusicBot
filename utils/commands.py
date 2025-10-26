import os
from typing import Optional

import disnake

from utils.audio import AudioSourceManager
from utils.functions import get_user_folder, ensure_voice

async def play_command(guild_players, inter: disnake.ApplicationCommandInteraction | disnake.MessageInteraction,
                       track_type: str, filename: str,
                       voice_channel: Optional[disnake.VoiceChannel] = None,
                       guild_id: Optional[int] = None):
    # Попытка дефера — может упасть если интеракция уже отвечена, поэтому оборачиваем
    try:
        await inter.response.defer(ephemeral=True)
    except Exception:
        pass

    # Выбираем voice_channel: приоритет — переданный аргумент, затем inter.author.voice
    if voice_channel is None:
        author_voice = getattr(inter.author, "voice", None)
        if author_voice and getattr(author_voice, "channel", None):
            voice_channel = author_voice.channel

    if not voice_channel:
        # безопасная отправка сообщения об ошибке (edit_original может бросить 404)
        try:
            await inter.edit_original_message("Вы должны быть в голосовом канале!")
        except Exception:
            try:
                await inter.followup.send("Вы должны быть в голосовом канале!", ephemeral=True)
            except Exception:
                try:
                    await inter.response.send_message("Вы должны быть в голосовом канале!", ephemeral=True)
                except Exception:
                    pass
        return

    # разрешаем явно переопределять guild_id при вызове из DM
    guild_id_resolved = guild_id or inter.guild_id or getattr(voice_channel.guild, "id", None)

    player = guild_players.get(guild_id_resolved)
    if isinstance(player, disnake.VoiceClient):
        # упаковка в менеджер
        player = AudioSourceManager(guild_id_resolved, voice=player)
        guild_players[guild_id_resolved] = player

    if not player:
        player = AudioSourceManager(guild_id_resolved)
        guild_players[guild_id_resolved] = player

    await ensure_voice(player, voice_channel)

    user_folder = get_user_folder(track_type, getattr(inter.author, "id", None))
    path = os.path.join(user_folder, filename)
    if not os.path.exists(path):
        try:
            await inter.edit_original_message("Трек не найден.")
        except Exception:
            try:
                await inter.followup.send("Трек не найден.", ephemeral=True)
            except Exception:
                try:
                    await inter.response.send_message("Трек не найден.", ephemeral=True)
                except Exception:
                    pass
        return

    await player.play(path, track_type=track_type)

    # безопасно отправляем финальное сообщение
    try:
        await inter.edit_original_message(f"🎵 Проигрывается: `{filename}` из категории `{track_type}`")
    except Exception:
        try:
            await inter.followup.send(f"🎵 Проигрывается: `{filename}` из категории `{track_type}`", ephemeral=True)
        except Exception:
            try:
                await inter.response.send_message(f"🎵 Проигрывается: `{filename}` из категории `{track_type}`", ephemeral=True)
            except Exception:
                pass


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

def ensure_audio_manager(guild_players: dict, guild_id: int, voice_client: Optional[disnake.VoiceClient] = None):
    """
    Возвращает AudioSourceManager для guild_id. Если в guild_players лежал VoiceClient -
    оборачивает его в AudioSourceManager. Если ничего нет — создаёт новый менеджер.
    Также при наличии voice_client устанавливает player.voice.
    """
    player = guild_players.get(guild_id)
    # если вдруг там лежит сырый VoiceClient — упакуем в AudioSourceManager
    if isinstance(player, disnake.VoiceClient):
        player = AudioSourceManager(guild_id, voice=player)
        guild_players[guild_id] = player

    if player is None:
        player = AudioSourceManager(guild_id, voice=voice_client)
        guild_players[guild_id] = player
    else:
        # если передали voice_client, а у менеджера нет voice — установить
        if voice_client is not None and getattr(player, "voice", None) is None:
            player.voice = voice_client

    return player
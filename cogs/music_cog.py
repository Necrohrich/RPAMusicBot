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

import asyncio
import disnake
from disnake import OptionChoice
from disnake.ext import commands
import os

from utils.audio import AudioSourceManager
from utils.autocomplete_helpers import filename_autocomplete
from utils.commands import play_command
from utils.functions import get_user_folder, get_files_in_folder, ensure_voice
from utils.mix import AudioMixer

class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.guild_players = {}  # guild_id: AudioSourceManager

    @commands.slash_command(name="play", description="Проиграть трек из вашей коллекции")
    async def play(self, inter: disnake.ApplicationCommandInteraction,
                   track_type: str = commands.Param(choices=["music", "ambient", "mixed"], description="Категория трека"),
                   filename: str = commands.Param(description="Имя файла трека")):
        await play_command(self.guild_players, inter, track_type, filename)

    @commands.slash_command(description="Показать текущий проигрываемый трек")
    async def now_playing(self, inter: disnake.ApplicationCommandInteraction):
        player = self.guild_players.get(inter.guild_id)
        if not player or not player.current_track or not player.voice or not player.voice.is_playing():
            await inter.response.send_message("Сейчас ничего не воспроизводится.", ephemeral=True)
            return

        filename = os.path.basename(player.current_track)
        await inter.response.send_message(f"🎶 Сейчас играет: `{filename}` (категория: `{player.current_type}`)")

    @commands.slash_command(description="Поставить трек на паузу")
    async def pause(self, inter: disnake.ApplicationCommandInteraction):
        player = self.guild_players.get(inter.guild_id)
        if not player or not player.voice or not player.voice.is_playing():
            await inter.response.send_message("Нечего ставить на паузу.", ephemeral=True)
            return
        player.pause()
        await inter.response.send_message("⏸ Пауза")

    @commands.slash_command(description="Продолжить воспроизведение")
    async def resume(self, inter: disnake.ApplicationCommandInteraction):
        player = self.guild_players.get(inter.guild_id)
        if not player or not player.voice or not player.voice.is_paused():
            await inter.response.send_message("Нечего продолжать.", ephemeral=True)
            return
        player.resume()
        await inter.response.send_message("▶ Продолжено")

    @commands.slash_command(description="Остановить воспроизведение")
    async def stop(self, inter: disnake.ApplicationCommandInteraction):
        player = self.guild_players.get(inter.guild_id)
        if not player or not player.voice or not player.voice.is_playing():
            await inter.response.send_message("Нечего останавливать.", ephemeral=True)
            return
        player.stop()
        await inter.response.send_message("⏹ Остановлено")

    @commands.slash_command(description="Покинуть голосовой канал")
    async def leave(self, inter: disnake.ApplicationCommandInteraction):
        player = self.guild_players.get(inter.guild_id)
        if player and player.voice:
            await player.cancel_loop_task()
            await player.voice.disconnect()
            player.voice = None
            await inter.response.send_message("👋 Бот вышел из голосового канала.")
        else:
            await inter.response.send_message("Бот не в голосовом канале.", ephemeral=True)

    @commands.slash_command(description="Установить громкость воспроизведения")
    async def set_volume(self, inter: disnake.ApplicationCommandInteraction,
                         volume: float = commands.Param(ge=0.0, le=2.0, description="Громкость от 0.0 до 2.0")):
        player = self.guild_players.get(inter.guild_id)
        if not player or not player.current_type:
            await inter.response.send_message("Плеер не инициализирован или нет текущего трека.", ephemeral=True)
            return

        player.set_volume(player.current_type, volume)
        await inter.response.send_message(f"Громкость для {player.current_type} установлена в {volume}")

    @commands.slash_command(description="Перемотать трек на указанную позицию (в секундах)")
    async def seek(self, inter: disnake.ApplicationCommandInteraction,
                   position: float = commands.Param(ge=0.0, description="Позиция в секундах для перемотки")):
        player = self.guild_players.get(inter.guild_id)
        if not player or not player.current_track:
            await inter.response.send_message("Нет проигрываемого трека.", ephemeral=True)
            return
        try:
            await player.seek(position)
            await inter.response.send_message(f"Перемотано на {position} секунд.")
        except Exception as e:
            await inter.response.send_message(f"Ошибка при перемотке: {e}", ephemeral=True)

    @commands.slash_command(description="Включить или выключить повтор текущего трека")
    async def loop(self, inter: disnake.ApplicationCommandInteraction,
                   enable: bool = commands.Param(description="Включить (true) или выключить (false) повтор")):
        player = self.guild_players.get(inter.guild_id)
        if not player or not player.current_type:
            await inter.response.send_message("Плеер не инициализирован или нет текущего трека.", ephemeral=True)
            return
        player.set_loop(player.current_type, enable)
        status = "включён" if enable else "выключен"
        await inter.response.send_message(f"Повтор трека {status}.")

    @commands.slash_command(description="Создать и проиграть микс из музыки и эмбиента")
    async def mix(self, inter: disnake.ApplicationCommandInteraction,
                  music_file: str = commands.Param(description="Файл музыки"),
                  ambient_file: str = commands.Param(description="Файл эмбиента"),
                  mix_name: str = commands.Param(description="Имя для микса"),
                  music_volume: float = commands.Param(default=1.0, ge=0.0, le=2.0, description="Громкость музыки"),
                  ambient_volume: float = commands.Param(default=0.5, ge=0.0, le=2.0, description="Громкость эмбиента")):
        await inter.response.defer()
        guild_id = inter.guild_id

        music_folder = get_user_folder("music", inter.author.id)
        ambient_folder = get_user_folder("ambient", inter.author.id)

        music_path = os.path.join(music_folder, music_file)
        ambient_path = os.path.join(ambient_folder, ambient_file)

        if not os.path.exists(music_path) or not os.path.exists(ambient_path):
            await inter.edit_original_message("Один или оба файла не найдены.")
            return

        output_folder = os.path.join("music", "mixed", str(inter.author.id))
        os.makedirs(output_folder, exist_ok=True)
        output_filename = f"{mix_name}.mp3"
        output_path = os.path.join(output_folder, output_filename)

        try:
            await asyncio.to_thread(AudioMixer.mix_tracks, music_path, ambient_path, music_volume, ambient_volume,
                                    output_path)
        except Exception as e:
            await inter.edit_original_message(f"Ошибка микширования: {e}")
            return

        player = self.guild_players.get(guild_id)
        if not player:
            player = AudioSourceManager(guild_id)
            self.guild_players[guild_id] = player

        if not inter.author.voice or not inter.author.voice.channel:
            await inter.edit_original_message("Вы должны быть в голосовом канале!")
            return

        voice_channel = inter.author.voice.channel
        await ensure_voice(player, voice_channel)

        await inter.edit_original_message(f"Проигрывается микс `{output_filename}`")

        await player.play(output_path, track_type="mixed")

    @play.autocomplete("filename")
    async def music_autocomplete(self, inter, user_input: str):
        return await filename_autocomplete(inter, user_input)

    @mix.autocomplete("music_file")
    async def mix_music_autocomplete(self, inter, user_input: str):
        user_folder = get_user_folder("music", inter.author.id)
        files = get_files_in_folder(user_folder, user_input)
        return [OptionChoice(name=f, value=f) for f in files[:25]]

    @mix.autocomplete("ambient_file")
    async def mix_ambient_autocomplete(self, inter, user_input: str):
        user_folder = get_user_folder("ambient", inter.author.id)
        files = get_files_in_folder(user_folder, user_input)
        return [OptionChoice(name=f, value=f) for f in files[:25]]


def setup(bot: commands.Bot):
    bot.add_cog(MusicCog(bot))
    print("MusicCog загружен")


def teardown(bot: commands.Bot):
    bot.remove_cog("MusicCog")
    print("MusicCog удален")

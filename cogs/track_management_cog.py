import asyncio
import shutil
import aiofiles
import disnake
import yt_dlp
from disnake.ext import commands
import os

from utils.autocomplete_helpers import filename_autocomplete
from utils.functions import get_user_folder, to_seconds
from utils.logger import Logger
from utils.mix import AudioMixer

DISCORD_FILE_LIMIT_MB = 8
DISCORD_FILE_LIMIT_BYTES = DISCORD_FILE_LIMIT_MB * 1024 * 1024

class TrackManagementCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(name="upload", description="Загрузить трек в категорию music или ambient")
    async def upload_file(self, inter: disnake.ApplicationCommandInteraction,
                          file: disnake.Attachment,
                          track_type: str = commands.Param(choices=["music", "ambient"], description="Категория трека"),
                          new_name: str = commands.Param(default=None,
                                                         description="Новое имя файла без расширения (необязательно)")):
        await inter.response.defer()
        user_folder = get_user_folder(track_type, inter.author.id)

        # Используем новое имя, если оно задано, иначе исходное имя
        filename = f"{new_name}.mp3" if new_name else file.filename

        path = os.path.join(user_folder, filename)
        async with aiofiles.open(path, "wb") as f:
            content = await file.read()
            await f.write(content)

        await inter.edit_original_message(f"Трек **{filename}** загружен в категорию `{track_type}`.")

    @commands.slash_command(name="upload_url", description="Скачать трек по ссылке (YouTube и др.)")
    async def upload_url(self, inter: disnake.ApplicationCommandInteraction,
                         url: str = commands.Param(description="Ссылка на видео или аудио"),
                         track_name: str = commands.Param(description="Имя для сохранения файла"),
                         track_type: str = commands.Param(choices=["music", "ambient"], description="Категория трека"),
                         start: str = commands.Param(default="00:00:00", description="Начало (например 00:01:30)",
                                                     name="начало"),
                         end: str = commands.Param(default="", description="Конец (например 00:03:00)", name="конец")):
        await inter.response.defer()
        user_folder = get_user_folder(track_type, inter.author.id)
        final_path = os.path.join(user_folder, f"{track_name}.mp3")

        start_sec = to_seconds(start) if start else 0
        end_sec = to_seconds(end) if end.strip() else None
        duration = end_sec - start_sec if end_sec and end_sec > start_sec else None

        # Получаем прямой URL потока
        try:
            ydl_opts = {"quiet": True, "no_warnings": True, "format": "bestaudio/best"}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(lambda: ydl.extract_info(url, download=False))
                stream_url = info["url"]
        except Exception as e:
            return await inter.followup.send(f"❌ Ошибка получения ссылки на поток: {e}")

        # Первое и единственное сообщение, которое будем редактировать
        progress_message = await inter.followup.send("📥 Подготовка загрузки…", ephemeral=False)

        # Собираем команду ffmpeg
        ffmpeg_command = [
            "ffmpeg", "-y", "-hide_banner",
            # seek до нужного старта до входа потока
            *(["-ss", str(start_sec)] if start_sec > 0 else []),
            "-i", stream_url,
            # duration, если есть
            *(["-t", str(duration)] if duration else []),
            # ограничение размера до 8 МБ
            "-fs", str(DISCORD_FILE_LIMIT_BYTES),
            # выходные параметры
            "-vn", "-acodec", "libmp3lame", "-ab", "64k",
            "-progress", "pipe:1", "-nostats",
            final_path
        ]

        # Запускаем процесс
        process = await asyncio.create_subprocess_exec(
            *ffmpeg_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )

        async def update_progress():
            last_pct = None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                text = line.decode().strip()
                if text.startswith("out_time_ms="):
                    ms = int(text.split("=", 1)[1])
                    sec = ms // 1_000_000
                    if duration:
                        pct = min(int(sec / duration * 100), 100)
                        if pct != last_pct:
                            msg = f"⏳ Прогресс: {pct}%"
                            Logger.log(msg)
                            try:
                                await progress_message.edit(content=msg)
                            except disnake.HTTPException as error:
                                Logger.log_exception(error)
                            last_pct = pct
                    else:
                        # без конечной точки, выводим просто прошедшее время
                        h = sec // 3600
                        m = (sec % 3600) // 60
                        s = sec % 60
                        time_str = f"{h:02d}:{m:02d}:{s:02d}"
                        msg = f"⏳ Пройдено: {time_str}"
                        try:
                            await progress_message.edit(content=msg)
                        except disnake.HTTPException as error:
                            Logger.log_exception(error)

        updater = asyncio.create_task(update_progress())
        returncode = await process.wait()
        updater.cancel()

        if returncode != 0:
            new_content = "❌ Ошибка при обработке ffmpeg."
        else:
            new_content = f"✅ Трек **{track_name}** загружен в `{track_type}`."

        await progress_message.edit(content=new_content)
        return None

    @commands.slash_command(name="delete_track", description="Удалить трек из вашей коллекции")
    async def delete_track(self, inter: disnake.ApplicationCommandInteraction,
                           track_type: str = commands.Param(choices=["music", "ambient", "mixed"],
                                                            description="Категория трека"),
                           filename: str = commands.Param(description="Имя файла трека для удаления")):
        user_folder = get_user_folder(track_type, inter.author.id)
        path = os.path.join(user_folder, filename)

        if not os.path.exists(path):
            await inter.response.send_message("Трек не найден.", ephemeral=True)
            return

        try:
            os.remove(path)
            await inter.response.send_message(f"Трек **{filename}** успешно удалён из категории `{track_type}`.")
        except Exception as e:
            await inter.response.send_message(f"Ошибка при удалении файла: {e}", ephemeral=True)

    @commands.slash_command(name="delete_all_tracks", description="Удалить все треки в выбранной категории")
    async def delete_all_tracks(self, inter: disnake.ApplicationCommandInteraction,
                                track_type: str = commands.Param(choices=["music", "ambient", "mixed"],
                                                                 description="Категория треков для удаления")):
        user_folder = get_user_folder(track_type, inter.author.id)

        if not os.path.exists(user_folder):
            await inter.response.send_message("Папка не найдена.", ephemeral=True)
            return

        deleted_files = 0
        try:
            for filename in os.listdir(user_folder):
                file_path = os.path.join(user_folder, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    deleted_files += 1

            await inter.response.send_message(
                f"Удалено {deleted_files} треков из категории `{track_type}`.",
                ephemeral=True
            )
        except Exception as e:
            await inter.response.send_message(f"Ошибка при удалении файлов: {e}", ephemeral=True)

    @commands.slash_command(name="move_track", description="Переместить трек из одной категории в другую")
    async def move_track(
            self,
            inter: disnake.ApplicationCommandInteraction,
            source_type: str = commands.Param(
                choices=["music", "ambient", "mixed"],
                description="Категория, из которой переносим"
            ),
            filename: str = commands.Param(
                description="Имя трека, который нужно переместить (включая .mp3)"
            ),
            target_type: str = commands.Param(
                choices=["music", "ambient", "mixed"],
                description="Категория, в которую переносим"
            )
    ):
        """
        Перемещает трек пользователя между категориями: music, ambient, mixed.
        """

        await inter.response.defer(ephemeral=True)

        if source_type == target_type:
            await inter.edit_original_message("Исходная и целевая категории совпадают. Выберите разные.")
            return

        try:
            user_id = inter.author.id
            source_folder = get_user_folder(source_type, user_id)
            target_folder = get_user_folder(target_type, user_id)

            src_path = os.path.join(source_folder, filename)
            dst_path = os.path.join(target_folder, filename)

            if not os.path.isfile(src_path):
                await inter.edit_original_message(f"Файл **{filename}** не найден в категории `{source_type}`.")
                return

            if os.path.exists(dst_path):
                await inter.edit_original_message(
                    f"В категории `{target_type}` уже есть файл с именем **{filename}**."
                )
                return

            os.makedirs(target_folder, exist_ok=True)
            shutil.move(src_path, dst_path)

            Logger.log(f"[{inter.author.id}] переместил трек {filename} из {source_type} → {target_type}")

            await inter.edit_original_message(
                f"✅ Трек **{filename}** успешно перемещён из `{source_type}` в `{target_type}`."
            )

        except Exception as e:
            Logger.log_exception(e)
            await inter.edit_original_message(f"❌ Ошибка при перемещении: {e}")

    @commands.slash_command(name="track_duration", description="Узнать длительность трека из вашей коллекции")
    async def track_duration(
            self,
            inter: disnake.ApplicationCommandInteraction,
            track_type: str = commands.Param(
                choices=["music", "ambient", "mixed"],
                description="Категория трека"),
            filename: str = commands.Param(description="Имя файла трека (включая .mp3)")
    ):
        await inter.response.defer(ephemeral=True)

        # Папка пользователя
        user_folder = get_user_folder(track_type, inter.author.id)
        file_path = os.path.join(user_folder, filename)
        if not os.path.isfile(file_path):
            await inter.edit_original_message("❌ Трек не найден в вашей коллекции.")
            return

        # Получаем длительность
        duration_sec = AudioMixer.get_track_duration(file_path)
        if duration_sec <= 0:
            await inter.edit_original_message("❌ Не удалось определить длительность.")
            return

        hrs = int(duration_sec // 3600)
        mins = int((duration_sec % 3600) // 60)
        secs = int(duration_sec % 60)
        duration_str = f"{hrs:02d}:{mins:02d}:{secs:02d}"

        await inter.edit_original_message(
            f"⏱ Длительность `{filename}` из `{track_type}`: **{duration_str}**"
        )

    @track_duration.autocomplete("filename")
    @move_track.autocomplete("filename")
    @delete_track.autocomplete("filename")
    async def track_manager_autocomplete(self, inter, user_input: str):
        return await filename_autocomplete(inter, user_input)

def setup(bot: commands.Bot):
    bot.add_cog(TrackManagementCog(bot))
    print("TrackManagementCog загружен")

def teardown(bot: commands.Bot):
    bot.remove_cog("TrackManagementCog")
    print("TrackManagementCog удален")
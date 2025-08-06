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
import os
import subprocess
from functools import lru_cache
from typing import Optional

import disnake
from disnake import FFmpegPCMAudio
from utils.logger import Logger


class AudioSourceManager:
    def __init__(self, guild_id: int, voice: Optional[disnake.VoiceClient] = None):
        self.guild_id = guild_id
        self.voice = voice
        self.current_track = None
        self.current_type = None  # "music", "ambient", "mixed"
        self.loop_flags = {"music": False, "ambient": False, "mixed": False}
        self.volumes = {"music": 1.0, "ambient": 1.0, "mixed": 1.0}
        self._play_lock = asyncio.Lock()
        self._play_task: Optional[asyncio.Task] = None

    def set_volume(self, track_type: str, volume: float):
        self.volumes[track_type] = volume
        Logger.log(f"[{self.guild_id}] Громкость '{track_type}' установлена на {volume}")

        if self.current_type == track_type and self.voice and self.voice.is_playing():
            Logger.log(f"[{self.guild_id}] Применение новой громкости для текущего трека")
            self.voice.stop()
            asyncio.create_task(self.play(self.current_track, self.current_type))

    def set_loop(self, track_type: str, enable: bool):
        self.loop_flags[track_type] = enable
        Logger.log(f"[{self.guild_id}] Loop для '{track_type}' установлен в {enable}")

        if not enable and self._play_task and not self._play_task.done():
            self._play_task.cancel()

    async def play(self, path: str, track_type: str):
        if not self.voice or not self.voice.is_connected():
            Logger.error(f"[{self.guild_id}] Голосовой клиент не подключён, не могу воспроизвести")
            return

        self.current_track = path
        self.current_type = track_type

        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            try:
                await self._play_task
            except asyncio.CancelledError:
                pass

        Logger.log(f"[{self.guild_id}] Начинаю воспроизведение трека: {os.path.basename(path)} (тип {track_type})")

        self._play_task = asyncio.create_task(self._play_loop_or_single(path))

    async def _play_loop_or_single(self, path: str, seek_seconds: float = 0.0):
        try:
            while True:
                async with self._play_lock:
                    await self._play_source(path, seek_seconds=seek_seconds)
                    seek_seconds = 0.0

                Logger.log(f"[{self.guild_id}] Воспроизведение трека завершено")

                if not self.loop_flags.get(self.current_type, False):
                    if self.voice and self.voice.is_connected():
                        await self.voice.disconnect()
                        Logger.log(f"[{self.guild_id}] Бот покинул голосовой канал после завершения трека")
                    break

                Logger.log(f"[{self.guild_id}] Повтор трека: {os.path.basename(path)}")

        except asyncio.CancelledError:
            Logger.log(f"[{self.guild_id}] Воспроизведение отменено")
            raise
        except Exception as e:
            Logger.error(f"[{self.guild_id}] Ошибка в воспроизведении: {e}")

    async def _play_source(self, path: str, seek_seconds: float = 0.0):
        if not self.voice or not self.voice.is_connected():
            Logger.error(f"[{self.guild_id}] Голосовой клиент не подключён (_play_source)")
            return

        duration = self.get_track_duration(path)
        if seek_seconds >= duration:
            Logger.log(f"[{self.guild_id}] Seek {seek_seconds}s > длительность {duration}s, сброс на 0")
            seek_seconds = 0.0

        audio = self._create_audio_source(path, seek_seconds)
        if audio is None:
            return

        if self.voice.is_playing():
            self.voice.stop()

        play_done = asyncio.Event()

        def after_play(error):
            if error:
                Logger.error(f"[{self.guild_id}] Ошибка при воспроизведении: {error}")
            play_done.set()

        self.voice.play(audio, after=after_play)
        Logger.log(f"[{self.guild_id}] Воспроизведение источника с громкостью {self.volumes.get(self.current_type, 1.0)}")
        await play_done.wait()

    def _create_audio_source(self, path: str, seek_seconds: float = 0.0) -> Optional[FFmpegPCMAudio]:
        if not os.path.isfile(path):
            Logger.error(f"[{self.guild_id}] Файл не найден: {path}")
            return None

        vol = self.volumes.get(self.current_type, 1.0)
        before_options = f"-ss {seek_seconds}"
        options = f"-filter:a volume={vol}"

        try:
            return FFmpegPCMAudio(
                executable="ffmpeg",
                source=path,
                before_options=before_options,
                options=options
            )
        except Exception as e:
            Logger.error(f"[{self.guild_id}] Ошибка при создании аудиоисточника: {e}")
            return None

    async def seek(self, position: float):
        if not self.current_track or not os.path.isfile(self.current_track):
            Logger.error(f"[{self.guild_id}] Файл не найден для seek: {self.current_track}")
            return

        duration = await self._get_audio_duration(self.current_track)
        if duration and position >= duration:
            Logger.log(f"[{self.guild_id}] Seek больше длительности ({position} > {duration}). Сброс на 0")
            position = 0.0

        Logger.log(f"[{self.guild_id}] Перемотка на {position} секунд в треке: {os.path.basename(self.current_track)}")

        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            try:
                await self._play_task
            except asyncio.CancelledError:
                pass

        self._play_task = asyncio.create_task(self._play_loop_or_single(self.current_track, seek_seconds=position))

    async def _get_audio_duration(self, path: str) -> Optional[float]:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return float(result.stdout.strip())
        except Exception as e:
            Logger.error(f"[{self.guild_id}] Не удалось получить длительность трека: {e}")
            return None

    def stop(self):
        if self.voice and self.voice.is_playing():
            self.voice.stop()
            Logger.log(f"[{self.guild_id}] Воспроизведение остановлено")

        if self._play_task and not self._play_task.done():
            self._play_task.cancel()

        self.current_track = None
        self.current_type = None

    def pause(self):
        if self.voice and self.voice.is_playing():
            self.voice.pause()
            Logger.log(f"[{self.guild_id}] Воспроизведение приостановлено")

    def resume(self):
        if self.voice and self.voice.is_paused():
            self.voice.resume()
            Logger.log(f"[{self.guild_id}] Воспроизведение возобновлено")

    async def cancel_loop_task(self):
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            try:
                await self._play_task
            except asyncio.CancelledError:
                Logger.log(f"[{self.guild_id}] Луп трека отменён корректно")
            self._play_task = None

    @staticmethod
    @lru_cache(maxsize=64)
    def get_track_duration(path: str) -> float:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=5
            )
            return float(result.stdout.strip())
        except Exception as e:
            Logger.error(f"[Audio] Ошибка получения длительности трека: {e}")
            return 0.0

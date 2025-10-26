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

        # Новые поля для отслеживания позиции старта воспроизведения
        self._start_time: Optional[float] = None  # loop.time() когда началось текущее воспроизведение
        self._start_seek_offset: float = 0.0  # сколько секунд пропущено при старте (ss)

        # --- fade-параметры (по умолчанию для music: включён выход, вход выключен) ---
        # Эти настройки хранятся в объекте плеера — действуют пока объект жив (плеер подключён).
        self.fade_config = {
            "music": {"in_enabled": False, "out_enabled": True, "in_dur": 3.0, "out_dur": 5.0},
            "ambient": {"in_enabled": False, "out_enabled": False, "in_dur": 2.0, "out_dur": 2.0},
            "mixed": {"in_enabled": False, "out_enabled": True, "in_dur": 3.0, "out_dur": 5.0},
        }

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

    async def play(self, path: str, track_type: str, fade_in: float = 0.0, fade_out: float = 0.0):
        if not self.voice or not self.voice.is_connected():
            Logger.error(f"[{self.guild_id}] Голосовой клиент не подключён, не могу воспроизвести")
            return

        # Подставляем глобальные для этого плеера настройки, если конкретные параметры не указаны
        cfg = self.fade_config.get(track_type, {})
        if (not fade_in or float(fade_in) == 0.0) and cfg.get("in_enabled"):
            fade_in = float(cfg.get("in_dur", 0.0))
        if (not fade_out or float(fade_out) == 0.0) and cfg.get("out_enabled"):
            fade_out = float(cfg.get("out_dur", 0.0))

        self.current_track = path
        self.current_type = track_type

        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            try:
                await self._play_task
            except asyncio.CancelledError:
                pass

        Logger.log(f"[{self.guild_id}] Начинаю воспроизведение трека: {os.path.basename(path)} (тип {track_type})")

        self._play_task = asyncio.create_task(self._play_loop_or_single(path, fade_in=fade_in, fade_out=fade_out))

    async def _play_loop_or_single(self, path: str, seek_seconds: float = 0.0, fade_in: float = 0.0,
                                   fade_out: float = 0.0):
        try:
            while True:
                async with self._play_lock:
                    await self._play_source(path, seek_seconds=seek_seconds, fade_in=fade_in, fade_out=fade_out)
                    # после первого запуска сбрасываем seek/fade_in (повторы должны начинаться с начала без повторного fade_in)
                    seek_seconds = 0.0
                    fade_in = 0.0

                Logger.log(f"[{self.guild_id}] Воспроизведение трека завершено")

                if not self.loop_flags.get(self.current_type, False):
                    Logger.log(f"[{self.guild_id}] Трек завершён, ожидание следующей команды.")
                    break
                    # if self.voice and self.voice.is_connected():
                    #     await self.voice.disconnect()
                    #     Logger.log(f"[{self.guild_id}] Бот покинул голосовой канал после завершения трека")
                    # break

                Logger.log(f"[{self.guild_id}] Повтор трека: {os.path.basename(path)}")

        except asyncio.CancelledError:
            Logger.log(f"[{self.guild_id}] Воспроизведение отменено")
            raise
        except Exception as e:
            Logger.error(f"[{self.guild_id}] Ошибка в воспроизведении: {e}")

    async def _play_source(self, path: str, seek_seconds: float = 0.0, fade_in: float = 0.0, fade_out: float = 0.0):
        if not self.voice or not self.voice.is_connected():
            Logger.error(f"[{self.guild_id}] Голосовой клиент не подключён (_play_source)")
            return

        duration = self.get_track_duration(path)
        if seek_seconds >= duration:
            Logger.log(f"[{self.guild_id}] Seek {seek_seconds}s > длительность {duration}s, сброс на 0")
            seek_seconds = 0.0

        audio = self._create_audio_source(path, seek_seconds, fade_in, fade_out)
        if audio is None:
            return

        # Обновляем стартовую метку для возможности расчёта текущей позиции
        loop = asyncio.get_running_loop()
        self._start_time = loop.time()
        self._start_seek_offset = seek_seconds

        if self.voice.is_playing():
            self.voice.stop()

        play_done = asyncio.Event()

        def after_play(error):
            if error:
                Logger.error(f"[{self.guild_id}] Ошибка при воспроизведении: {error}")
            play_done.set()

        self.voice.play(audio, after=after_play)
        Logger.log(
            f"[{self.guild_id}] Воспроизведение источника с громкостью {self.volumes.get(self.current_type, 1.0)} "
            f"fade-in: {fade_in}s, fade-out: {fade_out}s")
        await play_done.wait()

    def _create_audio_source(self, path: str, seek_seconds: float = 0.0, fade_in: float = 0.0, fade_out: float = 0.0) -> Optional[FFmpegPCMAudio]:
        if not os.path.isfile(path):
            Logger.error(f"[{self.guild_id}] Файл не найден: {path}")
            return None

        vol = self.volumes.get(self.current_type, 1.0)
        # -ss перед input — seek по входу
        before_options = f"-ss {seek_seconds}"

        # строим фильтр: volume + afade(вход/выход)
        filters = []
        filters.append(f"volume={vol}")

        # fade in — всегда начинается с относительной позиции 0 (после -ss)
        if fade_in and fade_in > 0.0:
            filters.append(f"afade=t=in:st=0:d={float(fade_in)}")
            Logger.log(f"[{self.guild_id}] fade-in будет применён: {fade_in}s")

        # fade out — нужно вычислить старт относительно оставшейся части входа (после -ss)
        if fade_out and fade_out > 0.0:
            total = self.get_track_duration(path)
            remaining = max(total - seek_seconds, 0.0)
            # старт фейда относительно начала текущего возпроизведения (после -ss)
            start = max(remaining - float(fade_out), 0.0)
            filters.append(f"afade=t=out:st={start}:d={float(fade_out)}")
            Logger.log(
                f"[{self.guild_id}] fade-out будет применён: {fade_out}s (старт относительно текущей позиции: {start}s)")

        filter_str = ",".join(filters)
        # options: фильтр + вывод в PCM (FFmpegPCMAudio сам обработает)
        options = f"-filter:a \"{filter_str}\""

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

        # Передаём текущий fade_out из настроек плеера
        fade_out_duration = self.fade_config.get(self.current_type, {}).get("out_dur", 0.0) \
            if self.fade_config.get(self.current_type, {}).get("out_enabled", False) else 0.0

        self._play_task = asyncio.create_task(self._play_loop_or_single(self.current_track, seek_seconds=position,
                                                                        fade_out=fade_out_duration))

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

    # --- новые утилиты для фейдов/позиции ---

    def get_current_position(self) -> Optional[float]:
        """Возвращает текущее положение (в секундах) в текущем треке или None."""
        if not self._start_time:
            return None
        loop = asyncio.get_running_loop()
        pos = loop.time() - self._start_time + self._start_seek_offset
        return pos

    async def fade_out(self, fade_duration: float):
        """Плавное приглушение от текущей позиции: создаёт новый источник с afade out,
           который начнётся сразу и продлится fade_duration."""
        if not self.current_track or not os.path.isfile(self.current_track):
            Logger.log(f"[{self.guild_id}] Нет трека для fade_out")
            return

        if not self.voice or not self.voice.is_playing():
            Logger.log(f"[{self.guild_id}] Нечего приглушать")
            return

        pos = self.get_current_position() or 0.0
        total = self.get_track_duration(self.current_track)
        remaining = max(total - pos, 0.0)
        if remaining <= 0:
            Logger.log(f"[{self.guild_id}] Трек уже в конце")
            return

        # Если запрашивают фейд длиннее оставшегося времени — уменьшаем
        fade_d = min(float(fade_duration), remaining)

        # Перезапускаем воспроизведение с -ss=pos и afade out длиной fade_d (старт в 0)
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            try:
                await self._play_task
            except asyncio.CancelledError:
                pass

        # этот цикл воспроизведения не устанавливает loop/repeat (использует существующие флаги)
        self._play_task = asyncio.create_task(
            self._play_loop_or_single(self.current_track, seek_seconds=pos, fade_in=0.0, fade_out=fade_d))

    async def restart_with_fade_in(self, fade_duration: float):
        """Перезапустит текущий трек с начала и применит fade-in."""
        if not self.current_track or not os.path.isfile(self.current_track):
            Logger.log(f"[{self.guild_id}] Нет трека для restart_with_fade_in")
            return

        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            try:
                await self._play_task
            except asyncio.CancelledError:
                pass

        # старт с нуля и fade-in
        self._play_task = asyncio.create_task(
            self._play_loop_or_single(self.current_track, seek_seconds=0.0, fade_in=float(fade_duration), fade_out=0.0))

    # ---------- управление настройками fade для плеера ----------

    def set_fade_enabled(self, track_type: str, fade_in_enabled: Optional[bool] = None,
                         fade_out_enabled: Optional[bool] = None):
        cfg = self.fade_config.setdefault(track_type,
                                          {"in_enabled": False, "out_enabled": False, "in_dur": 3.0, "out_dur": 5.0})
        if fade_in_enabled is not None:
            cfg["in_enabled"] = bool(fade_in_enabled)
            Logger.log(f"[{self.guild_id}] fade-in для '{track_type}' установлен: {cfg['in_enabled']}")
        if fade_out_enabled is not None:
            cfg["out_enabled"] = bool(fade_out_enabled)
            Logger.log(f"[{self.guild_id}] fade-out для '{track_type}' установлен: {cfg['out_enabled']}")

    def set_fade_duration(self, track_type: str, in_dur: Optional[float] = None, out_dur: Optional[float] = None):
        cfg = self.fade_config.setdefault(track_type,
                                          {"in_enabled": False, "out_enabled": False, "in_dur": 3.0, "out_dur": 5.0})
        if in_dur is not None:
            cfg["in_dur"] = float(in_dur)
            Logger.log(f"[{self.guild_id}] Длительность fade-in для '{track_type}' установлена: {cfg['in_dur']}")
        if out_dur is not None:
            cfg["out_dur"] = float(out_dur)
            Logger.log(f"[{self.guild_id}] Длительность fade-out для '{track_type}' установлена: {cfg['out_dur']}")

    def get_fade_settings(self, track_type: str):
        return self.fade_config.get(track_type, {})

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

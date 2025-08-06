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

import subprocess
import logging

class AudioMixer:
    @staticmethod
    def get_track_duration(file_path: str) -> float:
        """
        Получить длительность аудиофайла в секундах с помощью ffprobe.
        Если не удалось — возвращает 0.0.
        """
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    file_path
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            return float(result.stdout.strip())
        except Exception as e:
            logging.warning(f"Не удалось получить длительность трека {file_path}: {e}")
            return 0.0

    @staticmethod
    def mix_tracks(
            music_path: str,
            ambient_path: str,
            music_vol: float,
            ambient_vol: float,
            output_path: str,
    ):
        """
        Микширует два трека (музыка и эмбиент), повторяя короткий, чтобы совпадал с длинным.
        Использует aloop для быстрого повтора и избегает вызова ffprobe.
        """

        dur_music = AudioMixer.get_track_duration(music_path)
        dur_ambient = AudioMixer.get_track_duration(ambient_path)

        if dur_music >= dur_ambient:
            long_path = music_path
            short_path = ambient_path
            long_vol = music_vol
            short_vol = ambient_vol
        else:
            long_path = ambient_path
            short_path = music_path
            long_vol = ambient_vol
            short_vol = music_vol

        command = [
            "ffmpeg", "-y",
            "-i", long_path,
            "-i", short_path,
            "-filter_complex",
            (
                f"[0:a]volume={long_vol}[a0];"
                f"[1:a]volume={short_vol},aloop=loop=-1:size=2e+07[a1];"
                f"[a0][a1]amix=inputs=2:duration=first"
            ),
            "-c:a", "libmp3lame",
            "-threads", "2",
            output_path,
        ]

        logging.info(f"FFmpeg микширование: {' '.join(command)}")

        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if result.returncode != 0:
            error_message = result.stderr.decode(errors="ignore")
            logging.error(f"Ошибка микширования: {error_message}")
            raise RuntimeError(f"Ошибка микширования аудио: {error_message}")

        logging.info(f"Микширование завершено. Файл сохранён: {output_path}")
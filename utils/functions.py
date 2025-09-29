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

import os
from typing import Optional

import disnake

from utils.audio import AudioSourceManager


async def ensure_voice(player: AudioSourceManager, voice_channel: disnake.VoiceChannel):
    if player.voice is None or not player.voice.is_connected():
        player.voice = await voice_channel.connect()
    elif player.voice.channel.id != voice_channel.id:
        await player.voice.move_to(voice_channel)

def get_user_folder(track_type: str, user_id: int) -> str:
    folder = f"music/{track_type}/{user_id}"
    os.makedirs(folder, exist_ok=True)
    return folder


def get_files_in_folder(folder: str, user_input: str) -> list[str]:
    if not os.path.exists(folder):
        return []
    return [f for f in os.listdir(folder) if f.lower().endswith(".mp3") and user_input.lower() in f.lower()]

def to_seconds(time_str: str) -> Optional[int]:
    parts = [int(p) for p in time_str.strip().split(":")]
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m = 0, parts[0]
        s = parts[1]
    elif len(parts) == 1:
        h, m, s = 0, 0, parts[0]
    else:
        return None
    return h * 3600 + m * 60 + s
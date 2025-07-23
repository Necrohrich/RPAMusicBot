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

from disnake import OptionChoice
from utils.functions import get_user_folder, get_files_in_folder

async def filename_autocomplete(inter, user_input: str):
    track_type = inter.options.get("track_type") or inter.options.get("source_type")
    if not track_type:
        return []
    user_folder = get_user_folder(track_type, inter.author.id)
    files = get_files_in_folder(user_folder, user_input)
    return [OptionChoice(name=f, value=f) for f in files[:25]]

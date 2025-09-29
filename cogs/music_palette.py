import os
import json
import asyncio
import urllib.parse
import disnake
from disnake import ApplicationCommandInteraction, OptionChoice
from disnake.ext import commands
from disnake.ui import Button, View

from utils.autocomplete_helpers import filename_autocomplete
from utils.commands import play_command, stop_command, pause_command, resume_command, loop_command

PALETTES_PATH = "data/palettes.json"

def _safe_name(s: str) -> str:
    return urllib.parse.quote_plus(s)

def _unsafename(s: str) -> str:
    return urllib.parse.unquote_plus(s)

class PaletteStorage:
    """Асинхронно читаем/пишем JSON палет."""
    def __init__(self, path: str = PALETTES_PATH):
        self.path = path
        self._lock = asyncio.Lock()
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        if not os.path.exists(self.path):
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)

    async def load(self) -> dict:
        async with self._lock:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)

    async def save(self, data: dict):
        async with self._lock:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)


class PaletteView(View):
    def __init__(self, cog, owner_id: int, palette_name: str, ephemeral: bool):
        super().__init__(timeout=None)
        self.cog = cog
        self.owner_id = owner_id
        self.palette_name = palette_name
        self.ephemeral = ephemeral
        self.message: disnake.Message | None = None

        # 20 трек-кнопок
        for i in range(1, 21):
            safe_pid = _safe_name(palette_name)
            custom_id = f"palette:{owner_id}:{safe_pid}:slot:{i}"
            btn = Button(style=disnake.ButtonStyle.primary, label=str(i), custom_id=custom_id)
            # безопасная фабрика для callback (избегаем проблем с замыканиями)
            def make_slot_cb(slot):
                async def _cb(inter: disnake.MessageInteraction):
                    await cog.on_track_pressed(inter, owner_id, palette_name, slot, self)
                return _cb
            btn.callback = make_slot_cb(i)
            self.add_item(btn)

        # 5 контрольных кнопок: stop, pause, loop, resume, close
        ctrl_buttons = [
            ("⏹ Stop", "stop"),
            ("⏸ Pause", "pause"),
            ("🔁 Loop", "loop"),
            ("▶️ Resume", "resume"),
            ("❌ Close", "close"),
        ]
        for item in ctrl_buttons:
            label = item[0] if len(item) > 0 else "?"
            action = item[1] if len(item) > 1 else ""
            custom_id = f"palette_ctrl:{owner_id}:{_safe_name(palette_name)}:{action}"
            style = disnake.ButtonStyle.secondary
            btn = Button(style=style, label=label, custom_id=custom_id)
            def make_ctrl_cb(act):
                async def _cb(inter: disnake.MessageInteraction):
                    await cog.on_control_pressed(inter, owner_id, palette_name, act, self)
                return _cb
            btn.callback = make_ctrl_cb(action)
            self.add_item(btn)

    def disable_all(self):
        for it in self.children:
            it.disabled = True

class MusicPaletteCog(commands.Cog):
    def __init__(self, bot: commands.Bot, palettes_path: str = PALETTES_PATH):
        self.bot = bot
        # гарантируем, что bot имеет поле guild_players
        if not hasattr(self.bot, "guild_players"):
            self.bot.guild_players = {}
        self.guild_players = self.bot.guild_players
        self.storage = PaletteStorage(palettes_path)

    # ---------- HELPERS ----------
    async def _get_user_palettes(self, user_id: int) -> dict:
        data = await self.storage.load()
        return data.get(str(user_id), {})

    async def _save_user_palettes(self, user_id: int, palettes: dict):
        data = await self.storage.load()
        data[str(user_id)] = palettes
        await self.storage.save(data)

    # ---------- CORE ACTIONS ----------
    async def play_from_palette(self, inter: ApplicationCommandInteraction | disnake.MessageInteraction,
                                track_type: str, filename: str):
        """
        Вызываем существующую команду play_command и возвращаем (success, msg),
        чтобы вызывающий код мог корректно распаковать результат.
        """
        try:
            # play_command сам обрабатывает interaction (defer/edit) в utils.commands
            await play_command(self.guild_players, inter, track_type, filename)
            msg = f"Проигрывается: `{filename}` ({track_type})"
            return True, msg
        except Exception as e:
            return False, f"Ошибка при попытке воспроизвести: {e}"

    # ---------- CALLBACKS для View ----------
    async def on_track_pressed(self, inter: disnake.MessageInteraction, owner_id: int, palette_name: str,
                               slot: int, view: PaletteView):
        # проверка прав — палета принадлежит владельцу owner_id
        if inter.author.id != owner_id:
            await inter.response.send_message("Это не ваша палета.", ephemeral=True)
            return

        # загрузим палету
        user_palettes = await self._get_user_palettes(owner_id)
        pal = user_palettes.get(palette_name)
        if not pal:
            await inter.response.send_message("Палета не найдена.", ephemeral=True)
            return

        slot_key = str(slot)
        entry = pal.get(slot_key)
        if not entry:
            await inter.response.send_message("Этот слот пуст.", ephemeral=True)
            return

        shortname = entry.get("shortname", "track")
        track_type = entry.get("track_type")
        filename = entry.get("filename")
        if not track_type or not filename:
            await inter.response.send_message("Некорректная запись в палете.", ephemeral=True)
            return

        # НЕ делаем defer здесь — play_command сам отвечает/деферит
        success, msg = await self.play_from_palette(inter, track_type, filename)

        # обновляем embed в сообщении
        # формируем список слотов (только заполненные)
        lines = []
        for i in range(1, 21):
            entry_i = pal.get(str(i))
            if entry_i:
                lines.append(f"{i}: {entry_i.get('shortname')}")

        embed = disnake.Embed(title=f"Palette — {palette_name}")
        embed.add_field(name="Status", value=f"Слот {slot}: {shortname}\n{msg}", inline=False)

        if lines:
            embed.add_field(name="Назначенные слоты", value="\n".join(lines[:10]), inline=False)
            if len(lines) > 10:
                embed.add_field(name="...", value="\n".join(lines[10:20]), inline=False)

        embed.set_footer(text=f"Запрос от {inter.author.display_name}")

        msg_obj = getattr(view, "message", None) or inter.message
        try:
            if msg_obj:
                await msg_obj.edit(embed=embed, view=view)
            else:
                await inter.followup.send(msg, ephemeral=view.ephemeral)
        except Exception as exc:
            print("Failed to edit palette message:", exc)
            try:
                await inter.followup.send(msg, ephemeral=view.ephemeral)
            except Exception as exc2:
                print("Also failed to send followup:", exc2)

    async def on_control_pressed(self, inter: disnake.MessageInteraction, owner_id: int, palette_name: str,
                                 action: str, view: PaletteView):
        if inter.author.id != owner_id:
            await inter.response.send_message("Это не ваша палета.", ephemeral=True)
            return

        # получаем player
        guild_id = inter.guild_id
        player = self.guild_players.get(guild_id)
        # УБРАЛИ defer здесь — команды сами отправляют ответ
        msg = ""
        try:
            if action == "stop":
                if player:
                    await stop_command(player, inter)
                    msg = "Остановлено."
                else:
                    await inter.response.send_message("Плеер не запущен.", ephemeral=True)
                    return
            elif action == "pause":
                if player:
                    await pause_command(player, inter)
                    msg = "Пауза."
                else:
                    await inter.response.send_message("Плеер не запущен.", ephemeral=True)
                    return
            elif action == "resume":
                if player:
                    await resume_command(player, inter)
                    msg = "Возобновлено."
                else:
                    await inter.response.send_message("Плеер не запущен.", ephemeral=True)
                    return
            elif action == "loop":
                if player:
                    # Определяем текущее состояние loop — сначала храним/читаем _palette_loop_enabled,
                    # если нет — пробуем player.loop, иначе считаем False.
                    current_state = getattr(player, "_palette_loop_enabled", None)
                    if current_state is None:
                        current_state = getattr(player, "loop", False)
                    new_state = not bool(current_state)

                    # Вызываем loop_command с явно заданным enable
                    await loop_command(player, inter, new_state)
                    # Сохраняем состояние для будущих переключений
                    setattr(player, "_palette_loop_enabled", bool(new_state))
                    msg = f"Режим loop {'включён' if new_state else 'выключен'}."
                else:
                    await inter.response.send_message("Плеер не запущен.", ephemeral=True)
                    return
            elif action == "close":
                view.disable_all()
                msg_obj = getattr(view, "message", None) or inter.message

                try:
                    if not getattr(inter, "responded", False):
                        await inter.response.send_message("Палета закрыта.", ephemeral=True)

                    if msg_obj:
                        try:
                            await msg_obj.delete()
                        except Exception:
                            try:
                                orig = await inter.original_message()
                                if orig:
                                    await orig.delete()
                            except Exception:
                                try:
                                    await msg_obj.edit(embed=disnake.Embed(title="Палета закрыта"), view=None)
                                except Exception:
                                    pass
                except Exception as e:
                    try:
                        if getattr(inter, "responded", False):
                            await inter.followup.send("Палета закрыта.", ephemeral=True)
                        else:
                            await inter.response.send_message("Палета закрыта.", ephemeral=True)
                    except Exception:
                        print("Close: unexpected error:", e)
                return
        except Exception as e:
                        try:
                            await inter.followup.send(f"Ошибка при выполнении действия: {e}", ephemeral=True)
                        except Exception:
                            # если даже followup не сработал — пробуем response (в крайнем случае)
                            try:
                                await inter.response.send_message(f"Ошибка при выполнении действия: {e}", ephemeral=True)
                            except Exception:
                                pass
                        return

        # обновим embed сообщения
        # формируем embed со списком слотов
        user_palettes = await self._get_user_palettes(owner_id)
        pal = user_palettes.get(palette_name, {})

        lines = []
        for i in range(1, 21):
            entry_i = pal.get(str(i))
            if entry_i:
                lines.append(f"{i}: {entry_i.get('shortname')}")

        embed = disnake.Embed(title=f"Palette — {palette_name}")
        embed.add_field(name="Status", value=msg, inline=False)

        if lines:
            embed.add_field(name="Назначенные слоты", value="\n".join(lines[:10]), inline=False)
            if len(lines) > 10:
                embed.add_field(name="...", value="\n".join(lines[10:20]), inline=False)

        msg_obj = getattr(view, "message", None) or inter.message
        try:
            if msg_obj:
                await msg_obj.edit(embed=embed, view=view)
            else:
                await inter.followup.send(msg, ephemeral=True)
        except Exception as exc:
            print("Failed to edit palette message:", exc)
            try:
                await inter.followup.send(msg, ephemeral=True)
            except Exception as exc2:
                print("Also failed to send followup:", exc2)

    # ---------- SLASH COMMANDS ----------
    @commands.slash_command(name="palette", description="Управление музыкальными палетами")
    async def palette(self, inter: ApplicationCommandInteraction):
        pass

    @palette.sub_command(name="create", description="Создать новую палету (20 слотов)")
    async def palette_create(self, inter: ApplicationCommandInteraction,
                             name: str = commands.Param(description="Имя палеты")):
        await inter.response.defer(ephemeral=True)
        uid = inter.author.id
        palettes = await self._get_user_palettes(uid)
        if name in palettes:
            await inter.followup.send("Палета с таким именем уже существует.", ephemeral=True)
            return
        palettes[name] = {str(i): None for i in range(1, 21)}
        await self._save_user_palettes(uid, palettes)
        await inter.followup.send(f"Палета `{name}` создана.", ephemeral=True)

    @palette.sub_command(name="add", description="Добавить трек в палету")
    async def palette_add(
        self,
        inter: ApplicationCommandInteraction,
        palette_name: str = commands.Param(description="Имя палеты"),
        slot: int = commands.Param(ge=1, le=20, description="Слот (1-20)"),
        shortname: str = commands.Param(description="Короткое имя для кнопки"),
        track_type: str = commands.Param(choices=["music", "ambient", "mixed"], description="Категория трека (music/ambient/...)"),
        filename: str = commands.Param(description="Имя файла (например track.mp3)")
    ):
        await inter.response.defer(ephemeral=True)
        uid = inter.author.id
        palettes = await self._get_user_palettes(uid)
        pal = palettes.get(palette_name)
        if pal is None:
            await inter.followup.send("Палета не найдена.", ephemeral=True)
            return
        pal[str(slot)] = {
            "shortname": shortname,
            "track_type": track_type,
            "filename": filename
        }
        await self._save_user_palettes(uid, palettes)
        await inter.followup.send(f"Трек добавлен в слот {slot} палеты `{palette_name}`.", ephemeral=True)

    @palette_add.autocomplete("filename")
    async def music_autocomplete(self, inter, user_input: str):
        return await filename_autocomplete(inter, user_input)

    @palette.sub_command(name="show", description="Показать палету (всплывающее/не всплывающее окно)")
    async def palette_show(
            self,
            inter: ApplicationCommandInteraction,
            palette_name: str = commands.Param(description="Имя палеты"),
            ephemeral: bool = commands.Param(default=True, description="Показать как ephemeral?")
    ):
        uid = inter.author.id
        palettes = await self._get_user_palettes(uid)
        pal = palettes.get(palette_name)
        if pal is None:
            await inter.response.send_message("Палета не найдена.", ephemeral=True)
            return

        # создаём View
        view = PaletteView(self, uid, palette_name, ephemeral)
        embed = disnake.Embed(
            title=f"Palette — {palette_name}",
            description="Нажмите кнопку слота, чтобы воспроизвести. Контролы: Stop/Pause/Loop/Resume/Close"
        )

        fields = []
        for i in range(1, 21):
            entry = pal.get(str(i))
            if entry:
                fields.append(f"{i}: {entry.get('shortname')}")

        if fields:
            embed.add_field(name="Назначенные слоты", value="\n".join(fields[:10]), inline=False)
            if len(fields) > 10:
                embed.add_field(name="...", value="\n".join(fields[10:20]), inline=False)

        # отправляем сообщение с view
        await inter.response.send_message(embed=embed, view=view, ephemeral=ephemeral)

        # сохраняем объект сообщения внутри view
        try:
            view.message = await inter.original_message()
        except Exception as exc:
            print("Failed to fetch original message:", exc)
            view.message = None

    @palette.sub_command(name="delete", description="Удалить палету")
    async def palette_delete(self, inter: ApplicationCommandInteraction,
                             palette_name: str = commands.Param(description="Имя палеты")):
        await inter.response.defer(ephemeral=True)
        uid = inter.author.id
        palettes = await self._get_user_palettes(uid)
        if palette_name not in palettes:
            await inter.followup.send("Палета не найдена.", ephemeral=True)
            return
        del palettes[palette_name]
        await self._save_user_palettes(uid, palettes)
        await inter.followup.send(f"Палета `{palette_name}` удалена.", ephemeral=True)

    @palette_add.autocomplete("palette_name")
    @palette_show.autocomplete("palette_name")
    @palette_delete.autocomplete("palette_name")
    async def palette_name_autocomplete(self, inter: ApplicationCommandInteraction, user_input: str):
        uid = inter.author.id
        palettes = await self._get_user_palettes(uid)
        res = []
        for name in palettes.keys():
            if user_input.lower() in name.lower():
                res.append(OptionChoice(name, name))
            if len(res) >= 25:
                break
        return res

    @palette.sub_command(name="list", description="Показать список ваших палет")
    async def palette_list(self, inter: ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        uid = inter.author.id
        palettes = await self._get_user_palettes(uid)
        if not palettes:
            await inter.followup.send("У вас нет палет.", ephemeral=True)
            return
        names = "\n".join(f"- {n}" for n in palettes.keys())
        await inter.followup.send(f"Ваши палеты:\n{names}", ephemeral=True)

def setup(bot: commands.Bot):
    # гарантируем наличие shared guild_players
    if not hasattr(bot, "guild_players"):
        bot.guild_players = {}
    bot.add_cog(MusicPaletteCog(bot))
    print("MusicPaletteCog загружен")


def teardown(bot: commands.Bot):
    bot.remove_cog("MusicPaletteCog")
    print("MusicPaletteCog удален")

import os
import json
import asyncio
import urllib.parse
import disnake
from disnake import ApplicationCommandInteraction, OptionChoice
from disnake.ext import commands
from disnake.ui import Button, View, Select

from utils.autocomplete_helpers import filename_autocomplete
from utils.commands import play_command, stop_command, pause_command, resume_command, loop_command

PALETTES_PATH = "data/palettes.json"


def _safe_name(s: str) -> str:
    return urllib.parse.quote_plus(s)


def _unsafename(s: str) -> str:
    return urllib.parse.unquote_plus(s)


class PaletteStorage:
    """Асинхронно читаем/пишем JSON палет и групп.

    Формат (после обновления):
    {
      "<user_id>": {
         "palettes": { "pal_name": {"1": {...}, ...}, ... },
         "groups": { "group_name": ["pal1", "pal2", ...], ... }
      },
      ...
    }
    """
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


class GroupView(View):
    """View, который показывает Select со списком палет из группы.

    При выборе палеты открывает PaletteView для выбранной палеты.
    """
    def __init__(self, cog, owner_id: int, group_name: str, palettes_list: list[str], ephemeral: bool):
        super().__init__(timeout=None)
        self.cog = cog
        self.owner_id = owner_id
        self.group_name = group_name
        self.palettes_list = palettes_list
        self.ephemeral = ephemeral

        options = [disnake.SelectOption(label=p, value=p) for p in palettes_list]
        # ограничение size — максимум 25 по условию, но Select поддерживает 1-25
        select = Select(placeholder="Выберите палету из группы...", options=options, min_values=1, max_values=1)

        async def callback(inter: disnake.MessageInteraction):
            if inter.author.id != owner_id:
                await inter.response.send_message("Это не ваша группа.", ephemeral=True)
                return
            selected = inter.values[0]
            uid = owner_id
            user_palettes = await self.cog._get_user_palettes(uid)
            if selected not in user_palettes:
                await inter.response.send_message("Палета не найдена. Возможно, она была удалена.", ephemeral=True)
                return

            pal_view = PaletteView(self.cog, uid, selected, self.ephemeral)
            pal = user_palettes[selected]

            embed = disnake.Embed(
                title=f"Palette — {selected}",
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

            try:
                await inter.response.send_message(embed=embed, view=pal_view, ephemeral=self.ephemeral)
                try:
                    pal_view.message = await inter.original_message()
                except Exception:
                    pal_view.message = None
            except Exception as e:
                try:
                    await inter.followup.send(embed=embed, view=pal_view, ephemeral=self.ephemeral)
                except Exception:
                    try:
                        await inter.channel.send(embed=embed, view=pal_view)
                    except Exception:
                        print("Failed to open palette from group:", e)

        select.callback = callback
        self.add_item(select)


class MusicPaletteCog(commands.Cog):
    def __init__(self, bot: commands.Bot, palettes_path: str = PALETTES_PATH):
        self.bot = bot
        # гарантируем, что bot имеет поле guild_players
        if not hasattr(self.bot, "guild_players"):
            self.bot.guild_players = {}
        self.guild_players = self.bot.guild_players
        self.storage = PaletteStorage(palettes_path)

    # ---------- STORAGE HELPERS ----------
    async def _get_user_data(self, user_id: int) -> dict:
        data = await self.storage.load()
        raw = data.get(str(user_id), {})
        # legacy: raw is palettes dict
        if isinstance(raw, dict) and ('palettes' not in raw and 'groups' not in raw):
            return {"palettes": raw, "groups": {}}
        if not isinstance(raw, dict):
            return {"palettes": {}, "groups": {}}
        return {"palettes": raw.get("palettes", {}), "groups": raw.get("groups", {})}

    async def _save_user_data(self, user_id: int, palettes: dict | None = None, groups: dict | None = None):
        data = await self.storage.load()
        existing = data.get(str(user_id), {})
        # handle legacy existing
        if isinstance(existing, dict) and ('palettes' not in existing and 'groups' not in existing):
            existing_palettes = existing
            existing_groups = {}
        else:
            existing_palettes = existing.get('palettes', {}) if isinstance(existing, dict) else {}
            existing_groups = existing.get('groups', {}) if isinstance(existing, dict) else {}

        if palettes is None:
            palettes = existing_palettes
        if groups is None:
            groups = existing_groups

        data[str(user_id)] = {'palettes': palettes, 'groups': groups}
        await self.storage.save(data)

    async def _get_user_palettes(self, user_id: int) -> dict:
        d = await self._get_user_data(user_id)
        return d.get('palettes', {})

    async def _save_user_palettes(self, user_id: int, palettes: dict):
        d = await self._get_user_data(user_id)
        groups = d.get('groups', {})
        await self._save_user_data(user_id, palettes=palettes, groups=groups)

    async def _get_user_groups(self, user_id: int) -> dict:
        d = await self._get_user_data(user_id)
        return d.get('groups', {})

    async def _save_user_groups(self, user_id: int, groups: dict):
        d = await self._get_user_data(user_id)
        palettes = d.get('palettes', {})
        await self._save_user_data(user_id, palettes=palettes, groups=groups)

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
                    current_state = getattr(player, "_palette_loop_enabled", None)
                    if current_state is None:
                        current_state = getattr(player, "loop", False)
                    new_state = not bool(current_state)

                    await loop_command(player, inter, new_state)
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

    # ---- palette basic commands (create/add/show/delete/list) ----
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
        track_type: str = commands.Param(choices=["music", "ambient", "mixed"], description="Категория трека (music/ambient/...)") ,
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
        # также удаляем палету из всех групп пользователя
        groups = await self._get_user_groups(uid)
        for gname, items in list(groups.items()):
            if palette_name in items:
                items.remove(palette_name)
                groups[gname] = items
        await self._save_user_palettes(uid, palettes)
        await self._save_user_groups(uid, groups)
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

    # ---------- GROUP COMMANDS ----------
    @palette.sub_command(name="group_create", description="Создать группу паллет")
    async def palette_group_create(self, inter: ApplicationCommandInteraction,
                                   name: str = commands.Param(description="Имя группы")):
        await inter.response.defer(ephemeral=True)
        uid = inter.author.id
        groups = await self._get_user_groups(uid)
        if name in groups:
            await inter.followup.send("Группа с таким именем уже существует.", ephemeral=True)
            return
        groups[name] = []
        await self._save_user_groups(uid, groups)
        await inter.followup.send(f"Группа паллет `{name}` создана.", ephemeral=True)

    @palette.sub_command(name="group_delete", description="Удалить группу паллет")
    async def palette_group_delete(self, inter: ApplicationCommandInteraction,
                                   name: str = commands.Param(description="Имя группы")):
        await inter.response.defer(ephemeral=True)
        uid = inter.author.id
        groups = await self._get_user_groups(uid)
        if name not in groups:
            await inter.followup.send("Группа не найдена.", ephemeral=True)
            return
        del groups[name]
        await self._save_user_groups(uid, groups)
        await inter.followup.send(f"Группа `{name}` удалена.", ephemeral=True)

    @palette.sub_command(name="group_add", description="Добавить паллету в группу")
    async def palette_group_add(self, inter: ApplicationCommandInteraction,
                                group_name: str = commands.Param(description="Имя группы"),
                                palette_name: str = commands.Param(description="Имя палеты")):
        await inter.response.defer(ephemeral=True)
        uid = inter.author.id
        groups = await self._get_user_groups(uid)
        if group_name not in groups:
            await inter.followup.send("Группа не найдена.", ephemeral=True)
            return
        palettes = await self._get_user_palettes(uid)
        if palette_name not in palettes:
            await inter.followup.send("Палета не найдена.", ephemeral=True)
            return
        items = groups.get(group_name, [])
        if palette_name in items:
            await inter.followup.send("Палета уже в группе.", ephemeral=True)
            return
        if len(items) >= 25:
            await inter.followup.send("В группу можно добавить не более 25 палет.", ephemeral=True)
            return
        items.append(palette_name)
        groups[group_name] = items
        await self._save_user_groups(uid, groups)
        await inter.followup.send(f"Палета `{palette_name}` добавлена в группу `{group_name}`.", ephemeral=True)

    @palette_group_add.autocomplete("group_name")
    @palette_group_delete.autocomplete("name")
    async def group_name_autocomplete(self, inter: ApplicationCommandInteraction, user_input: str):
        uid = inter.author.id
        groups = await self._get_user_groups(uid)
        res = []
        for name in groups.keys():
            if user_input.lower() in name.lower():
                res.append(OptionChoice(name, name))
            if len(res) >= 25:
                break
        return res

    @palette_group_add.autocomplete("palette_name")
    async def group_palette_autocomplete(self, inter: ApplicationCommandInteraction, user_input: str):
        # Reuse palette autocomplete logic
        return await self.palette_name_autocomplete(inter, user_input)

    @palette.sub_command(name="group_show", description="Показать группу паллет (SelectView)")
    async def palette_group_show(self, inter: ApplicationCommandInteraction,
                                 group_name: str = commands.Param(description="Имя группы"),
                                 ephemeral: bool = commands.Param(default=True, description="Показать как ephemeral?")):
        uid = inter.author.id
        groups = await self._get_user_groups(uid)
        if group_name not in groups:
            await inter.response.send_message("Группа не найдена.", ephemeral=True)
            return
        items = groups[group_name]
        if not items:
            await inter.response.send_message("Группа пуста.", ephemeral=True)
            return

        # создаём GroupView
        view = GroupView(self, uid, group_name, items, ephemeral)
        embed = disnake.Embed(title=f"Group — {group_name}", description="Выберите палету из списка ниже:")
        embed.add_field(name="Палеты в группе", value="\n".join(items[:25]), inline=False)

        await inter.response.send_message(embed=embed, view=view, ephemeral=ephemeral)

    @palette_group_show.autocomplete("group_name")
    async def group_show_autocomplete(self, inter: ApplicationCommandInteraction, user_input: str):
        return await self.group_name_autocomplete(inter, user_input)


def setup(bot: commands.Bot):
    if not hasattr(bot, "guild_players"):
        bot.guild_players = {}
    bot.add_cog(MusicPaletteCog(bot))
    print("MusicPaletteCog загружен")


def teardown(bot: commands.Bot):
    bot.remove_cog("MusicPaletteCog")
    print("MusicPaletteCog удален")

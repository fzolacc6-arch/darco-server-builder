"""
DARCO SERVER BUILDER
====================
A Discord bot that builds a whole server (roles, categories, channels and
permissions) from a JSON file uploaded through the /setup slash command.

Commands: /setup  /ping  /help  /status  /backup

Safety rules built into this bot:
  * Only server administrators can use /setup, /status and /backup.
  * Nothing is ever deleted (no channels, no roles).
  * Everything is validated first, then previewed, then you press Confirm.
  * Administrator permission is only granted if the JSON explicitly lists it.
  * The bot token is read from an environment variable, never from the code.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import time
from typing import Any, Optional

import discord
from discord import app_commands

try:  # optional: lets you keep the token in a local ".env" file
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

log = logging.getLogger("darco")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BOT_NAME = "DARCO SERVER BUILDER"
REASON = "DARCO SERVER BUILDER setup"
MAX_JSON_BYTES = 1_000_000
DELAY = 0.6  # polite pause (seconds) between Discord API writes
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
CHANNEL_TYPES = {"text", "voice", "stage", "forum", "announcement"}
EVERYONE_ALIASES = {"@everyone", "everyone"}


# --------------------------------------------------------------------------
# Permission helpers
# --------------------------------------------------------------------------
def _all_permission_names() -> set[str]:
    try:
        return set(discord.Permissions.VALID_FLAGS)
    except AttributeError:  # pragma: no cover
        return {name for name, _ in discord.Permissions.all()}


VALID_PERMS = _all_permission_names()


def norm_perm(name: str) -> str:
    """'Manage Messages' / 'manage-messages' -> 'manage_messages'."""
    return re.sub(r"[\s\-]+", "_", str(name).strip().lower())


def perm_names(perms: discord.Permissions) -> list[str]:
    return sorted(name for name, value in perms if value)


def parse_color(value: Any) -> discord.Colour:
    if not value:
        return discord.Colour.default()
    match = HEX_RE.match(str(value).strip())
    return discord.Colour(int(match.group(1), 16)) if match else discord.Colour.default()


def clip(text: str, limit: int = 1900) -> str:
    return text if len(text) <= limit else text[: limit - 20] + "\n... (cut off)"


def norm_channel_name(name: str, kind: str) -> str:
    name = name.strip()
    if kind in ("text", "announcement", "forum"):
        return re.sub(r"\s+", "-", name.lower())
    return name


def kind_of(channel: discord.abc.GuildChannel) -> Optional[str]:
    if isinstance(channel, discord.ForumChannel):
        return "forum"
    if isinstance(channel, discord.StageChannel):
        return "stage"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if isinstance(channel, discord.TextChannel):
        return "announcement" if channel.is_news() else "text"
    return None


def explain(exc: Exception) -> str:
    if isinstance(exc, discord.Forbidden):
        return ("Discord refused (403 Forbidden). The bot lacks a permission, or its role is "
                "not high enough in the role list.")
    if isinstance(exc, discord.HTTPException):
        return f"Discord error {exc.status} (code {exc.code}): {exc.text}"
    return f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
# JSON validation (no Discord connection needed)
# --------------------------------------------------------------------------
def _check_perm_list(value: Any, where: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        errors.append(f"{where}: must be a list of permission names, e.g. [\"send_messages\"].")
        return []
    clean = []
    for p in value:
        n = norm_perm(p)
        if n not in VALID_PERMS:
            errors.append(f"{where}: unknown permission '{p}'.")
        else:
            clean.append(n)
    return clean


def _check_access(spec: dict, where: str, known_roles: set[str], errors: list[str]) -> None:
    """Validate 'private', 'allowed_roles' and 'permissions' of a category/channel."""
    if "private" in spec and not isinstance(spec["private"], bool):
        errors.append(f"{where}: 'private' must be true or false.")
    allowed = spec.get("allowed_roles", [])
    if not isinstance(allowed, list) or not all(isinstance(r, str) for r in allowed):
        errors.append(f"{where}: 'allowed_roles' must be a list of role names.")
    else:
        for r in allowed:
            if r.strip().lower() not in known_roles:
                errors.append(f"{where}: allowed role '{r}' is not defined in 'roles' and does not exist in the server.")
    perms = spec.get("permissions", {})
    if not isinstance(perms, dict):
        errors.append(f"{where}: 'permissions' must be an object like {{\"Role\": {{\"allow\": [...], \"deny\": [...]}}}}.")
        return
    for role_name, rules in perms.items():
        w = f"{where} permissions['{role_name}']"
        if role_name.strip().lower() not in known_roles:
            errors.append(f"{w}: role is not defined in 'roles' and does not exist in the server.")
        if not isinstance(rules, dict):
            errors.append(f"{w}: must be an object with 'allow' / 'deny' / 'inherit' lists.")
            continue
        for k in rules:
            if k not in ("allow", "deny", "inherit") and not k.startswith("_"):
                errors.append(f"{w}: unknown key '{k}' (use allow, deny, inherit).")
        allow = set(_check_perm_list(rules.get("allow"), f"{w} allow", errors))
        deny = set(_check_perm_list(rules.get("deny"), f"{w} deny", errors))
        _check_perm_list(rules.get("inherit"), f"{w} inherit", errors)
        both = allow & deny
        if both:
            errors.append(f"{w}: {sorted(both)} appear in both allow and deny.")


def _check_channel(ch: Any, where: str, known_roles: set[str], errors: list[str]) -> None:
    if not isinstance(ch, dict):
        errors.append(f"{where}: must be an object.")
        return
    name = ch.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        errors.append(f"{where}: 'name' is required (1-100 characters).")
        return
    where = f"{where} '{name}'"
    kind = str(ch.get("type", "text")).lower()
    if kind not in CHANNEL_TYPES:
        errors.append(f"{where}: type '{kind}' is invalid. Use one of {sorted(CHANNEL_TYPES)}.")
    if "topic" in ch and (not isinstance(ch["topic"], str) or len(ch["topic"]) > 1024):
        errors.append(f"{where}: 'topic' must be text up to 1024 characters.")
    if "nsfw" in ch and not isinstance(ch["nsfw"], bool):
        errors.append(f"{where}: 'nsfw' must be true or false.")
    if "slowmode" in ch and (not isinstance(ch["slowmode"], int) or isinstance(ch["slowmode"], bool)
                             or not 0 <= ch["slowmode"] <= 21600):
        errors.append(f"{where}: 'slowmode' must be a whole number of seconds from 0 to 21600.")
    if "user_limit" in ch and (not isinstance(ch["user_limit"], int) or isinstance(ch["user_limit"], bool)
                               or not 0 <= ch["user_limit"] <= 99):
        errors.append(f"{where}: 'user_limit' must be a whole number from 0 to 99.")
    if "bitrate" in ch and (not isinstance(ch["bitrate"], int) or isinstance(ch["bitrate"], bool)
                            or not 8000 <= ch["bitrate"] <= 384000):
        errors.append(f"{where}: 'bitrate' must be a whole number from 8000 to 384000.")
    _check_access(ch, where, known_roles, errors)


def validate_config(cfg: Any, existing_role_names: set[str]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). If errors is non-empty nothing will be built."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(cfg, dict):
        return ["The JSON must be an object (it must start with { and end with })."], []

    known_top = {"version", "server_name", "description", "roles", "everyone", "categories", "channels"}
    for k in cfg:
        if k not in known_top and not k.startswith("_"):
            warnings.append(f"Unknown top-level key '{k}' will be ignored.")

    # ---- roles
    roles = cfg.get("roles", [])
    config_roles: set[str] = set()
    if not isinstance(roles, list):
        errors.append("'roles' must be a list.")
        roles = []
    for i, r in enumerate(roles):
        where = f"roles[{i}]"
        if not isinstance(r, dict):
            errors.append(f"{where}: must be an object.")
            continue
        name = r.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            errors.append(f"{where}: 'name' is required (1-100 characters).")
            continue
        where = f"roles[{i}] '{name}'"
        key = name.strip().lower()
        if key in EVERYONE_ALIASES:
            errors.append(f"{where}: do not create an '@everyone' role; use the top-level 'everyone' section.")
        if key in config_roles:
            errors.append(f"{where}: duplicate role name.")
        config_roles.add(key)
        color = r.get("color", r.get("colour"))
        if color not in (None, "") and not (isinstance(color, str) and HEX_RE.match(color.strip())):
            errors.append(f"{where}: color '{color}' is not a HEX color like \"#FF5733\".")
        perms = _check_perm_list(r.get("permissions"), f"{where} permissions", errors)
        if "administrator" in perms:
            warnings.append(f"Role '{name}' is given ADMINISTRATOR (you asked for it explicitly).")
        for flag in ("hoist", "mentionable"):
            if flag in r and not isinstance(r[flag], bool):
                errors.append(f"{where}: '{flag}' must be true or false.")
    known_roles = config_roles | existing_role_names | EVERYONE_ALIASES

    # ---- @everyone
    if "everyone" in cfg:
        ev = cfg["everyone"]
        if not isinstance(ev, dict):
            errors.append("'everyone' must be an object like {\"permissions\": [...]}.")
        else:
            p = _check_perm_list(ev.get("permissions"), "everyone permissions", errors)
            if "administrator" in p:
                warnings.append("@everyone is given ADMINISTRATOR - every member would be an admin!")

    # ---- categories
    cats = cfg.get("categories", [])
    if not isinstance(cats, list):
        errors.append("'categories' must be a list.")
        cats = []
    for i, cat in enumerate(cats):
        where = f"categories[{i}]"
        if not isinstance(cat, dict):
            errors.append(f"{where}: must be an object.")
            continue
        name = cat.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            errors.append(f"{where}: 'name' is required (1-100 characters).")
            continue
        where = f"categories[{i}] '{name}'"
        _check_access(cat, where, known_roles, errors)
        chans = cat.get("channels", [])
        if not isinstance(chans, list):
            errors.append(f"{where}: 'channels' must be a list.")
            continue
        if len(chans) > 50:
            errors.append(f"{where}: a category can hold at most 50 channels.")
        for j, ch in enumerate(chans):
            _check_channel(ch, f"{where} channels[{j}]", known_roles, errors)
    if len(cats) > 50:
        errors.append("A server can have at most 50 categories in this setup.")

    # ---- channels outside categories
    loose = cfg.get("channels", [])
    if not isinstance(loose, list):
        errors.append("'channels' must be a list.")
        loose = []
    for j, ch in enumerate(loose):
        _check_channel(ch, f"channels[{j}]", known_roles, errors)

    return errors, warnings


def preflight(guild: discord.Guild, cfg: dict, update_existing: bool) -> tuple[list[str], list[str]]:
    """Checks that need the live server: bot permissions, role hierarchy, limits."""
    errors: list[str] = []
    warnings: list[str] = []
    me = guild.me
    if me is None:
        return ["Could not find the bot inside this server."], []
    bp = me.guild_permissions
    roles = cfg.get("roles", [])
    needs_roles = bool(roles) or "everyone" in cfg
    needs_channels = bool(cfg.get("categories") or cfg.get("channels"))
    if needs_roles and not bp.manage_roles:
        errors.append("The bot is missing the 'Manage Roles' permission.")
    if needs_channels and not bp.manage_channels:
        errors.append("The bot is missing the 'Manage Channels' permission.")

    existing = {r.name.lower(): r for r in guild.roles if not r.is_default()}
    new_roles = [r for r in roles if r["name"].strip().lower() not in existing]
    if len(guild.roles) + len(new_roles) > 250:
        errors.append(f"Discord allows 250 roles; the server has {len(guild.roles)} and the file adds {len(new_roles)}.")

    top = me.top_role
    touched = [r for r in roles if r["name"].strip().lower() not in existing or update_existing]
    for r in touched:
        for p in r.get("permissions", []) or []:
            n = norm_perm(p)
            if not bp.administrator and not getattr(bp, n, False):
                errors.append(f"Role '{r['name']}': the bot does not have '{n}' itself, so it cannot give it to a role. "
                              "Give the bot that permission (or Administrator while setting up).")
    if "everyone" in cfg:
        for p in cfg["everyone"].get("permissions", []) or []:
            n = norm_perm(p)
            if not bp.administrator and not getattr(bp, n, False):
                errors.append(f"@everyone: the bot does not have '{n}' itself, so it cannot grant it.")
    if update_existing:
        for r in roles:
            ex = existing.get(r["name"].strip().lower())
            if ex and ex.position >= top.position:
                warnings.append(f"Role '{r['name']}' is at or above the bot's role and cannot be edited. "
                                "Move the bot's role higher.")
    if touched and top.position - 1 < len(touched):
        warnings.append("The bot's role is low in the role list, so the new roles cannot all be placed in the exact "
                        "order requested. Drag the bot's role to the top in Server Settings > Roles and run again.")

    n_new_channels = sum(len(c.get("channels", [])) + 1 for c in cfg.get("categories", [])) + len(cfg.get("channels", []))
    if len(guild.channels) + n_new_channels > 500:
        warnings.append("The server may exceed Discord's 500-channel limit if everything is new.")
    return errors, warnings


# --------------------------------------------------------------------------
# The builder (used for both the preview and the real build)
# --------------------------------------------------------------------------
class Builder:
    def __init__(self, guild: discord.Guild, cfg: dict, update_existing: bool = False,
                 dry_run: bool = True, progress=None):
        self.guild = guild
        self.cfg = cfg
        self.update = update_existing
        self.dry = dry_run
        self.progress = progress
        self.lines: list[str] = []
        self.errors: list[str] = []
        self.stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
        self.role_map: dict[str, discord.Role] = {}
        self.touched: set[str] = set()
        self.done = 0
        self.total = (len(cfg.get("roles", [])) + (1 if "everyone" in cfg else 0)
                      + sum(1 + len(c.get("channels", [])) for c in cfg.get("categories", []))
                      + len(cfg.get("channels", []))) or 1

    # ---- bookkeeping
    def ok(self, kind: str, text: str) -> None:
        self.stats[kind] += 1
        word = {"created": "WOULD CREATE" if self.dry else "CREATED",
                "updated": "WOULD UPDATE" if self.dry else "UPDATED",
                "skipped": "SKIPPED (already exists)"}[kind]
        self.lines.append(f"[{word}] {text}")

    def fail(self, text: str, exc: Optional[Exception] = None) -> None:
        msg = f"{text}: {explain(exc)}" if exc else text
        self.stats["failed"] += 1
        self.errors.append(msg)
        self.lines.append(f"[FAILED] {msg}")

    async def tick(self, label: str) -> None:
        self.done += 1
        if self.progress and not self.dry and (self.done % 3 == 0 or self.done == self.total):
            await self.progress(self.done, self.total, label)

    async def pause(self) -> None:
        if not self.dry:
            await asyncio.sleep(DELAY)

    # ---- main entry
    async def run(self) -> None:
        try:
            await self.do_roles()
            await self.do_everyone()
            await self.do_structure()
        except Exception as exc:  # never crash silently
            log.exception("Unexpected error during build")
            self.fail("Unexpected error, build stopped", exc)

    # ---- roles
    async def do_roles(self) -> None:
        for spec in self.cfg.get("roles", []):
            name = spec["name"].strip()
            key = name.lower()
            label = f"Role '{name}'"
            existing = discord.utils.find(lambda r: r.name.lower() == key and not r.is_default(), self.guild.roles)
            perms = discord.Permissions(**{norm_perm(p): True for p in spec.get("permissions", []) or []})
            kwargs = dict(name=name, permissions=perms,
                          colour=parse_color(spec.get("color", spec.get("colour"))),
                          hoist=bool(spec.get("hoist", False)),
                          mentionable=bool(spec.get("mentionable", False)))
            try:
                if existing:
                    self.role_map[key] = existing
                    if self.update:
                        if existing.position >= self.guild.me.top_role.position or existing.managed:
                            self.fail(label, RuntimeError("cannot edit: role is above the bot or managed by an integration"))
                        else:
                            if not self.dry:
                                await existing.edit(reason=REASON, **kwargs)
                                await self.pause()
                            self.touched.add(key)
                            self.ok("updated", label)
                    else:
                        self.ok("skipped", label)
                else:
                    if not self.dry:
                        self.role_map[key] = await self.guild.create_role(reason=REASON, **kwargs)
                        await self.pause()
                    self.touched.add(key)
                    self.ok("created", label)
            except discord.HTTPException as exc:
                self.fail(label, exc)
            await self.tick(label)
        await self.order_roles()

    async def order_roles(self) -> None:
        """First role in the JSON = highest role (just under the bot's own role)."""
        wanted = [self.role_map[s["name"].strip().lower()] for s in self.cfg.get("roles", [])
                  if s["name"].strip().lower() in self.role_map and s["name"].strip().lower() in self.touched]
        if not wanted:
            return
        if self.dry:
            self.lines.append(f"[WOULD ARRANGE] {len(wanted)} role(s) in the order given in the file")
            return
        top = self.guild.me.top_role
        positions: dict[discord.Role, int] = {}
        pos = top.position - 1
        for role in wanted:
            if role.position >= top.position or role.managed:
                continue
            positions[role] = max(pos, 1)
            pos -= 1
        if not positions:
            return
        try:
            if hasattr(self.guild, "edit_role_positions"):
                await self.guild.edit_role_positions(positions=positions, reason=REASON)
            else:  # older discord.py
                for role, p in positions.items():
                    await role.edit(position=p, reason=REASON)
                    await self.pause()
            self.lines.append(f"[ARRANGED] {len(positions)} role(s) ordered")
        except discord.HTTPException as exc:
            self.fail("Arranging role order", exc)

    async def do_everyone(self) -> None:
        if "everyone" not in self.cfg:
            return
        label = "@everyone permissions"
        perms = discord.Permissions(**{norm_perm(p): True for p in self.cfg["everyone"].get("permissions", []) or []})
        try:
            if not self.dry:
                await self.guild.default_role.edit(permissions=perms, reason=REASON)
                await self.pause()
            self.ok("updated", label)
        except discord.HTTPException as exc:
            self.fail(label, exc)
        await self.tick(label)

    # ---- permission overwrites
    def resolve(self, name: str) -> Optional[discord.Role]:
        key = name.strip().lower()
        if key in EVERYONE_ALIASES:
            return self.guild.default_role
        if key in self.role_map:
            return self.role_map[key]
        return discord.utils.find(lambda r: r.name.lower() == key, self.guild.roles)

    def build_overwrites(self, spec: dict, base: Optional[dict] = None) -> dict:
        result: dict[Any, discord.PermissionOverwrite] = {
            t: discord.PermissionOverwrite.from_pair(*o.pair()) for t, o in (base or {}).items()}

        def get(target):
            return result.setdefault(target, discord.PermissionOverwrite())

        if spec.get("private"):
            get(self.guild.default_role).view_channel = False
            for rn in spec.get("allowed_roles", []) or []:
                role = self.resolve(rn)
                if role:
                    get(role).view_channel = True
        for rn, rules in (spec.get("permissions") or {}).items():
            target = self.resolve(rn)
            if target is None:
                self.lines.append(f"[WARNING] role '{rn}' not found; its permissions were skipped")
                continue
            ow = get(target)
            for p in rules.get("allow", []) or []:
                setattr(ow, norm_perm(p), True)
            for p in rules.get("deny", []) or []:
                setattr(ow, norm_perm(p), False)
            for p in rules.get("inherit", []) or []:
                setattr(ow, norm_perm(p), None)
        return result

    # ---- categories and channels
    async def do_structure(self) -> None:
        for idx, spec in enumerate(self.cfg.get("categories", [])):
            cat, base = await self.ensure_category(spec, idx)
            parent_name = spec["name"].strip()
            for cidx, ch in enumerate(spec.get("channels", [])):
                await self.ensure_channel(ch, cat, base, cidx, parent_name)
        for cidx, ch in enumerate(self.cfg.get("channels", [])):
            await self.ensure_channel(ch, None, {}, cidx, None)

    async def ensure_category(self, spec: dict, index: int):
        name = spec["name"].strip()
        label = f"Category '{name}'"
        has_access = bool(spec.get("private") or spec.get("permissions"))
        existing = discord.utils.find(lambda c: c.name.lower() == name.lower(), self.guild.categories)
        cat, base = existing, (existing.overwrites if existing else {})
        try:
            if existing:
                if self.update:
                    if not self.dry:
                        kw: dict[str, Any] = {"position": index}
                        if has_access:
                            base = self.build_overwrites(spec)
                            kw["overwrites"] = base
                        await existing.edit(reason=REASON, **kw)
                        await self.pause()
                    self.ok("updated", label)
                else:
                    self.ok("skipped", label)
            else:
                if not self.dry:
                    base = self.build_overwrites(spec)
                    kw = {"overwrites": base} if base else {}
                    cat = await self.guild.create_category(name, reason=REASON, **kw)
                    await self.pause()
                self.ok("created", label)
        except discord.HTTPException as exc:
            self.fail(label, exc)
        await self.tick(label)
        return cat, base

    async def ensure_channel(self, spec: dict, category, base: dict, index: int, parent_name: Optional[str]) -> None:
        kind = str(spec.get("type", "text")).lower()
        name = norm_channel_name(spec["name"], kind)
        where = f" in '{parent_name}'" if parent_name else ""
        label = f"{kind.capitalize()} channel '{name}'{where}"
        if category is not None:
            pool = list(category.channels)
        elif parent_name is None:
            pool = [c for c in self.guild.channels
                    if c.category is None and not isinstance(c, discord.CategoryChannel)]
        else:
            pool = []  # parent category does not exist yet (preview) or failed
        existing = discord.utils.find(lambda c: kind_of(c) == kind and c.name.lower() == name.lower(), pool)
        has_access = bool(spec.get("private") or spec.get("permissions"))
        try:
            if parent_name and category is None and not self.dry:
                self.fail(label, RuntimeError("skipped because its category could not be created"))
            elif existing:
                if self.update:
                    if not self.dry:
                        kw = self.edit_kwargs(kind, spec)
                        kw["position"] = index
                        if has_access:
                            kw["overwrites"] = self.build_overwrites(spec, base)
                        await existing.edit(reason=REASON, **kw)
                        await self.pause()
                    self.ok("updated", label)
                else:
                    self.ok("skipped", label)
            else:
                if not self.dry:
                    kw = self.create_kwargs(kind, spec)
                    if category is not None:
                        kw["category"] = category
                    ow = self.build_overwrites(spec, base)
                    if ow:
                        kw["overwrites"] = ow
                    await self.create_channel(kind, name, kw)
                    await self.pause()
                self.ok("created", label)
        except discord.HTTPException as exc:
            hint = ""
            if kind in ("announcement", "stage", "forum"):
                hint = " (this channel type needs the Community feature enabled on the server)"
            self.fail(label + hint, exc)
        await self.tick(label)

    @staticmethod
    def create_kwargs(kind: str, spec: dict) -> dict:
        kw: dict[str, Any] = {"reason": REASON}
        if kind in ("text", "announcement", "forum", "stage") and spec.get("topic"):
            kw["topic"] = spec["topic"]
        if kind == "stage" and "topic" not in kw:
            kw["topic"] = spec["name"].strip()
        if kind in ("text", "announcement", "forum"):
            if "nsfw" in spec:
                kw["nsfw"] = spec["nsfw"]
            if "slowmode" in spec:
                kw["slowmode_delay"] = spec["slowmode"]
        if kind in ("voice", "stage"):
            if "bitrate" in spec:
                kw["bitrate"] = spec["bitrate"]
        if kind == "voice" and "user_limit" in spec:
            kw["user_limit"] = spec["user_limit"]
        return kw

    @staticmethod
    def edit_kwargs(kind: str, spec: dict) -> dict:
        kw: dict[str, Any] = {}
        if kind in ("text", "announcement", "forum", "stage") and "topic" in spec:
            kw["topic"] = spec["topic"]
        if kind in ("text", "announcement", "forum"):
            if "nsfw" in spec:
                kw["nsfw"] = spec["nsfw"]
            if "slowmode" in spec:
                kw["slowmode_delay"] = spec["slowmode"]
        if kind == "voice":
            if "user_limit" in spec:
                kw["user_limit"] = spec["user_limit"]
            if "bitrate" in spec:
                kw["bitrate"] = spec["bitrate"]
        return kw

    async def create_channel(self, kind: str, name: str, kw: dict) -> None:
        g = self.guild
        if kind == "text":
            await g.create_text_channel(name, **kw)
        elif kind == "announcement":
            await g.create_text_channel(name, news=True, **kw)
        elif kind == "voice":
            await g.create_voice_channel(name, **kw)
        elif kind == "stage":
            await g.create_stage_channel(name, **kw)
        elif kind == "forum":
            await g.create_forum(name, **kw)

    def summary(self) -> str:
        s = self.stats
        c = "Would create" if self.dry else "Created"
        u = "Would update" if self.dry else "Updated"
        return (f"{c}: **{s['created']}**  |  {u}: **{s['updated']}**  |  "
                f"Already existed (skipped): **{s['skipped']}**  |  Failed: **{s['failed']}**")


def text_file(lines: list[str], filename: str) -> discord.File:
    return discord.File(io.BytesIO("\n".join(lines).encode("utf-8")), filename=filename)


# --------------------------------------------------------------------------
# The bot
# --------------------------------------------------------------------------
class DarcoBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())  # no privileged intents needed
        self.tree = app_commands.CommandTree(self)
        self.building: set[int] = set()
        self.started = time.time()

    async def setup_hook(self) -> None:
        await start_web_server()
        dev_guild = os.getenv("DEV_GUILD_ID")
        if dev_guild and dev_guild.isdigit():  # optional: instant command sync for one server
            g = discord.Object(int(dev_guild))
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
        await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id %s) in %d server(s)", self.user, self.user.id, len(self.guilds))


bot = DarcoBot()


async def start_web_server() -> None:
    """Tiny web page so hosts that require an open port (and uptime pingers) are happy."""
    port = os.getenv("PORT")
    if not port:
        return
    from aiohttp import web

    async def health(_request):
        return web.Response(text=f"{BOT_NAME} is running.")

    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()
    log.info("Health web server listening on port %s", port)


async def require_admin(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if interaction.guild is None:
        await interaction.response.send_message("This command only works inside a server.", ephemeral=True)
        return False
    if not (isinstance(user, discord.Member) and user.guild_permissions.administrator):
        await interaction.response.send_message("Only server administrators can use this command.", ephemeral=True)
        return False
    return True


async def reply(interaction: discord.Interaction, content: str,
                file: Optional[discord.File] = None, view: Optional[discord.ui.View] = None) -> None:
    await interaction.edit_original_response(content=clip(content), attachments=[file] if file else [], view=view)


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    log.exception("Command error", exc_info=error)
    msg = "Something went wrong. Please try again, and check the bot's logs if it keeps happening."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


# ---- /ping --------------------------------------------------------------
@bot.tree.command(name="ping", description="Check that the bot is alive.")
async def ping_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(f"Pong! Latency: {round(bot.latency * 1000)} ms", ephemeral=True)


# ---- /help --------------------------------------------------------------
@bot.tree.command(name="help", description="How to use DARCO SERVER BUILDER.")
async def help_cmd(interaction: discord.Interaction) -> None:
    e = discord.Embed(title=f"{BOT_NAME} - help", colour=discord.Colour.blurple(),
                      description="I build a whole server (roles, categories, channels, permissions) from a JSON file.")
    e.add_field(name="/setup file:<your.json> [dry_run] [update_existing]", inline=False,
                value=("Upload your JSON file. I check it, show a preview, and build only after you press **Confirm**.\n"
                       "`dry_run: True` = preview only.\n"
                       "`update_existing: True` = also change roles/channels that already exist (default: leave them alone)."))
    e.add_field(name="/backup", value="Exports this server's roles, categories and channels as a JSON file.", inline=False)
    e.add_field(name="/status", value="Shows bot health and whether it has the permissions it needs.", inline=False)
    e.add_field(name="/ping", value="Quick check that the bot is online.", inline=False)
    e.add_field(name="Before you run /setup", inline=False,
                value=("1. Give the bot **Manage Roles** and **Manage Channels** (or Administrator while setting up).\n"
                       "2. In Server Settings > Roles, drag the bot's role to the **top**.\n"
                       "3. Only administrators can run setup. Nothing is ever deleted."))
    await interaction.response.send_message(embed=e, ephemeral=True)


# ---- /status ------------------------------------------------------------
@bot.tree.command(name="status", description="Show bot status and server information.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def status_cmd(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction):
        return
    g = interaction.guild
    me = g.me
    p = me.guild_permissions
    up = int(time.time() - bot.started)
    h, rem = divmod(up, 3600)
    m, s = divmod(rem, 60)
    yes = lambda b: "Yes" if b else "**No**"
    e = discord.Embed(title=f"{BOT_NAME} - status", colour=discord.Colour.green())
    e.add_field(name="Bot", inline=False,
                value=f"Latency: {round(bot.latency * 1000)} ms\nUptime: {h}h {m}m {s}s\nServers: {len(bot.guilds)}")
    e.add_field(name="This server", inline=False,
                value=(f"Name: {g.name}\nMembers: {g.member_count}\nRoles: {len(g.roles)}/250\n"
                       f"Categories: {len(g.categories)}\nChannels: {len(g.channels)}/500"))
    e.add_field(name="Bot permissions here", inline=False,
                value=(f"Manage Roles: {yes(p.manage_roles)}\nManage Channels: {yes(p.manage_channels)}\n"
                       f"Administrator: {yes(p.administrator)}\n"
                       f"Bot's top role: {me.top_role.name} (position {me.top_role.position} of {len(g.roles) - 1})"))
    if bot.building and g.id in bot.building:
        e.set_footer(text="A build is running right now.")
    await interaction.response.send_message(embed=e, ephemeral=True)


# ---- /backup ------------------------------------------------------------
def overwrites_to_dict(channel: discord.abc.GuildChannel) -> dict:
    out: dict[str, dict] = {}
    for target, ow in channel.overwrites.items():
        if not isinstance(target, discord.Role):
            continue  # member-specific overwrites are not exported
        allow, deny = ow.pair()
        a, d = perm_names(allow), perm_names(deny)
        if a or d:
            out["@everyone" if target.is_default() else target.name] = {"allow": a, "deny": d}
    return out


def channel_to_dict(ch: discord.abc.GuildChannel) -> Optional[dict]:
    kind = kind_of(ch)
    if kind is None:
        return None
    d: dict[str, Any] = {"name": ch.name, "type": kind}
    topic = getattr(ch, "topic", None)
    if topic:
        d["topic"] = topic
    if kind in ("text", "announcement", "forum"):
        if getattr(ch, "nsfw", False):
            d["nsfw"] = True
        if getattr(ch, "slowmode_delay", 0):
            d["slowmode"] = ch.slowmode_delay
    if kind == "voice":
        if ch.user_limit:
            d["user_limit"] = ch.user_limit
    ow = overwrites_to_dict(ch)
    if ow:
        d["permissions"] = ow
    return d


@bot.tree.command(name="backup", description="Export this server's structure to a JSON file.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def backup_cmd(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    skipped = 0
    roles = []
    for r in sorted(g.roles, key=lambda r: r.position, reverse=True):  # highest first
        if r.is_default() or r.managed:
            continue
        roles.append({"name": r.name, "color": f"#{r.colour.value:06X}", "permissions": perm_names(r.permissions),
                      "hoist": r.hoist, "mentionable": r.mentionable})
    categories = []
    for cat in sorted(g.categories, key=lambda c: c.position):
        entry: dict[str, Any] = {"name": cat.name}
        ow = overwrites_to_dict(cat)
        if ow:
            entry["permissions"] = ow
        chans = []
        for ch in cat.channels:
            d = channel_to_dict(ch)
            if d:
                chans.append(d)
            else:
                skipped += 1
        entry["channels"] = chans
        categories.append(entry)
    loose = []
    for ch in sorted((c for c in g.channels if c.category is None and not isinstance(c, discord.CategoryChannel)),
                     key=lambda c: c.position):
        d = channel_to_dict(ch)
        if d:
            loose.append(d)
        else:
            skipped += 1
    data = {
        "version": 1,
        "server_name": g.name,
        "_notes": ("Exported by DARCO SERVER BUILDER. Not included: member-specific permissions, bot/integration roles, "
                   "threads, and unsupported channel types. Role order is highest first."),
        "everyone": {"permissions": perm_names(g.default_role.permissions)},
        "roles": roles,
        "categories": categories,
        "channels": loose,
    }
    raw = json.dumps(data, indent=2, ensure_ascii=False)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", g.name)[:40] or "server"
    file = discord.File(io.BytesIO(raw.encode("utf-8")), filename=f"darco_backup_{safe}.json")
    note = f"Backup of **{g.name}**: {len(roles)} roles, {len(categories)} categories."
    if skipped:
        note += f" ({skipped} unsupported channel(s) were skipped.)"
    await interaction.edit_original_response(content=note, attachments=[file])


# ---- /setup -------------------------------------------------------------
class ConfirmView(discord.ui.View):
    def __init__(self, owner_id: int, guild: discord.Guild, cfg: dict, update_existing: bool,
                 origin: discord.Interaction) -> None:
        super().__init__(timeout=600)
        self.owner_id, self.guild, self.cfg = owner_id, guild, cfg
        self.update, self.origin = update_existing, origin

    async def on_timeout(self) -> None:
        try:
            await self.origin.edit_original_response(content="Preview expired. Run /setup again.", view=None)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Confirm and build", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the person who ran /setup can confirm.", ephemeral=True)
            return
        if self.guild.id in bot.building:
            await interaction.response.send_message("A build is already running in this server.", ephemeral=True)
            return
        bot.building.add(self.guild.id)
        self.stop()
        await interaction.response.edit_message(content="Starting the build...", view=None, attachments=[])

        async def progress(done: int, total: int, label: str) -> None:
            try:
                await interaction.edit_original_response(content=f"Building... {done}/{total} steps done. Last: {label}")
            except discord.HTTPException:
                pass

        builder = Builder(self.guild, self.cfg, self.update, dry_run=False, progress=progress)
        try:
            await builder.run()
        finally:
            bot.building.discard(self.guild.id)

        head = "**Build finished with problems.**" if builder.stats["failed"] else "**Build finished successfully.**"
        text = f"{head}\n{builder.summary()}"
        if builder.errors:
            text += "\n\nProblems:\n" + "\n".join(f"- {e}" for e in builder.errors[:8])
            if len(builder.errors) > 8:
                text += f"\n...and {len(builder.errors) - 8} more (see report)."
        report = text_file(builder.lines, "build_report.txt")
        try:
            await interaction.edit_original_response(content=clip(text), attachments=[report])
        except discord.HTTPException:  # the 15-minute interaction window may have closed
            if interaction.channel:
                report = text_file(builder.lines, "build_report.txt")
                await interaction.channel.send(f"<@{self.owner_id}> {clip(text)}", file=report)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the person who ran /setup can cancel.", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing was changed.", view=None, attachments=[])


@bot.tree.command(name="setup", description="Build this server from an uploaded JSON file.")
@app_commands.describe(file="Your JSON configuration file",
                       dry_run="Preview only - change nothing",
                       update_existing="Also update roles/channels that already exist (default: leave them alone)")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def setup_cmd(interaction: discord.Interaction, file: discord.Attachment,
                    dry_run: bool = False, update_existing: bool = False) -> None:
    if not await require_admin(interaction):
        return
    guild = interaction.guild
    if guild.id in bot.building:
        await interaction.response.send_message("A build is already running in this server. Please wait.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)

    # 1. read the file
    if file.size > MAX_JSON_BYTES:
        await reply(interaction, "That file is too big (limit 1 MB). Please upload a smaller JSON file.")
        return
    try:
        cfg = json.loads((await file.read()).decode("utf-8-sig"))
    except UnicodeDecodeError:
        await reply(interaction, "I could not read that file as text. Save it as a UTF-8 .json file and try again.")
        return
    except json.JSONDecodeError as exc:
        await reply(interaction, f"The file is not valid JSON.\nProblem: **{exc.msg}** at line {exc.lineno}, column {exc.colno}.\n"
                                 "Tip: paste the file into https://jsonlint.com to find the mistake.")
        return

    # 2. validate everything before touching the server
    existing_roles = {r.name.lower() for r in guild.roles}
    errors, warnings = validate_config(cfg, existing_roles)
    if not errors:
        pre_err, pre_warn = preflight(guild, cfg, update_existing)
        errors += pre_err
        warnings += pre_warn
    if errors:
        lines = ["ERRORS (fix these, then run /setup again):"] + [f"- {e}" for e in errors]
        if warnings:
            lines += ["", "WARNINGS:"] + [f"- {w}" for w in warnings]
        shown = "\n".join(f"- {e}" for e in errors[:8])
        more = f"\n...and {len(errors) - 8} more (see attached report)." if len(errors) > 8 else ""
        await reply(interaction, f"**The file has {len(errors)} problem(s). Nothing was changed.**\n{shown}{more}",
                    file=text_file(lines, "validation_report.txt"))
        return

    # 3. preview (dry run) of what would happen
    preview = Builder(guild, cfg, update_existing, dry_run=True)
    await preview.run()
    plan = ["PREVIEW - nothing has been changed yet", ""] + preview.lines
    if warnings:
        plan += ["", "WARNINGS:"] + [f"- {w}" for w in warnings]
    text = f"**Preview - nothing has been changed yet.**\n{preview.summary()}"
    if warnings:
        text += "\n\nWarnings:\n" + "\n".join(f"- {w}" for w in warnings[:5])
    if dry_run:
        await reply(interaction, text + "\n\n(Dry run only. Run /setup again without dry_run to build.)",
                    file=text_file(plan, "preview.txt"))
        return
    text += "\n\nRead the attached preview, then press **Confirm and build**."
    view = ConfirmView(interaction.user.id, guild, cfg, update_existing, interaction)
    await reply(interaction, text, file=text_file(plan, "preview.txt"), view=view)


# --------------------------------------------------------------------------
def main() -> None:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set. Add it as an environment variable (see README.md, Step 6).")
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()

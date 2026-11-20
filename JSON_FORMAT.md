# DARCO SERVER BUILDER - JSON format

This is the complete format that `/setup` understands. **Give this whole file to ChatGPT** together with a
description of your server, and ask it to produce a JSON file that follows this format exactly.

## Prompt you can paste into ChatGPT

> Create a Discord server configuration as ONE valid JSON file using exactly the format in the document below.
> Output only the JSON (no comments, no trailing commas). Use only the permission names listed in the document.
> Every role that is referenced in a `permissions` or `allowed_roles` field must also be defined in `roles`
> (or be `@everyone`). My server design is: <describe your categories, channels, roles, colors, permissions>.

## Top level

```json
{
  "version": 1,
  "server_name": "Optional label (not applied)",
  "everyone":   { "permissions": ["view_channel", "send_messages"] },
  "roles":      [ ],
  "categories": [ ],
  "channels":   [ ]
}
```

All parts are optional. Keys starting with `_` (for example `"_comment"`) are ignored, so you can leave notes.

| Key | Meaning |
|---|---|
| `everyone` | Sets the server-wide permissions of @everyone. **Replaces** the current @everyone permission list. Leave the key out to leave @everyone untouched. |
| `roles` | Roles to create. **Order = hierarchy: first role in the list is the highest.** |
| `categories` | Categories, in the order they should appear, each with its own channels. |
| `channels` | Channels that are NOT inside any category (rare). |

## Role

```json
{ "name": "Moderator", "color": "#3498DB", "hoist": true, "mentionable": true,
  "permissions": ["manage_messages", "kick_members"] }
```

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Exact role name, 1-100 characters. Must be unique (not case-sensitive). |
| `color` | no | HEX like `"#FF5733"`. |
| `hoist` | no | `true` = show members of this role separately in the member list. Default `false`. |
| `mentionable` | no | `true` = anyone can @mention the role. Default `false`. |
| `permissions` | no | List of permission names (see below). Anything not listed is NOT granted. |

`administrator` is only given when you write it here. Never add it unless you really want it.

## Category

```json
{ "name": "STAFF", "private": true, "allowed_roles": ["Admin", "Moderator"],
  "permissions": { "Moderator": { "allow": ["send_messages"] } },
  "channels": [ ] }
```

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Category name. |
| `private` | no | `true` = hidden from @everyone (denies `view_channel`). |
| `allowed_roles` | no | With `private`, these roles can see it (allows `view_channel`). |
| `permissions` | no | Permission overwrites, see below. |
| `channels` | no | Channels inside this category. New channels inherit the category's permissions, and can add more. |

## Channel

```json
{ "name": "general", "type": "text", "topic": "Chat here", "slowmode": 5, "nsfw": false }
{ "name": "Game Room", "type": "voice", "user_limit": 10, "bitrate": 64000 }
```

| Field | Applies to | Notes |
|---|---|---|
| `name` | all | Required. Text channel names are turned into lowercase-with-dashes by Discord. |
| `type` | all | `text` (default), `voice`, `stage`, `forum`, `announcement`. Stage, forum and announcement need the **Community** feature enabled in the server. |
| `topic` | text, announcement, forum, stage | Up to 1024 characters. |
| `nsfw` | text, announcement, forum | `true` / `false`. |
| `slowmode` | text, announcement, forum | Seconds, 0-21600. |
| `user_limit` | voice | 0-99 (0 = unlimited). |
| `bitrate` | voice, stage | 8000-384000 (the server's boost level may limit it). |
| `private`, `allowed_roles`, `permissions` | all | Same as for categories. |

The order of channels in the list is the order they are created, which is the order you see in Discord.

## Permission overwrites (`permissions` in a category or channel)

```json
"permissions": {
  "@everyone": { "deny":  ["send_messages"] },
  "Member":    { "allow": ["send_messages", "add_reactions"] },
  "Muted":     { "deny":  ["send_messages", "speak"], "inherit": ["view_channel"] }
}
```

* The key is a role name (or `"@everyone"`). The role must be in `roles` or already exist in the server.
* `allow` = explicitly allowed (green check), `deny` = explicitly denied (red X),
  `inherit` = reset to "neutral" (grey slash). Permissions you do not mention stay neutral.
* A permission cannot be in both `allow` and `deny`.

## Valid permission names

Write them in lowercase with underscores (spaces or dashes are also accepted).

General: `view_channel`, `manage_channels`, `manage_roles`, `manage_guild`, `manage_webhooks`, `manage_expressions`,
`view_audit_log`, `view_guild_insights`, `create_instant_invite`, `change_nickname`, `manage_nicknames`,
`kick_members`, `ban_members`, `moderate_members`, `administrator`

Text: `send_messages`, `send_messages_in_threads`, `create_public_threads`, `create_private_threads`,
`embed_links`, `attach_files`, `add_reactions`, `use_external_emojis`, `use_external_stickers`,
`mention_everyone`, `manage_messages`, `manage_threads`, `read_message_history`, `send_tts_messages`,
`use_application_commands`, `send_voice_messages`, `create_polls`

Voice: `connect`, `speak`, `stream`, `use_voice_activation`, `priority_speaker`, `mute_members`, `deafen_members`,
`move_members`, `use_soundboard`, `use_external_sounds`, `request_to_speak`, `manage_events`, `create_events`

(The bot checks every name against the list in your installed discord.py version and tells you about any it does not know.)

## Behaviour to know about

* Existing roles/categories/channels with the same name are **skipped** (never duplicated, never deleted),
  unless you run `/setup` with `update_existing: True`.
* Create roles first (the bot does that automatically) - you do not need to worry about order inside the file.
* The bot can only give a role permissions that the bot itself has, and can only place roles below its own role.

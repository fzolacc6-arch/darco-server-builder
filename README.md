# DARCO SERVER BUILDER

A Discord bot that builds your whole server - **roles, categories, channels and permissions** - from one JSON file.

**Workflow:** deploy the bot -> invite it to your server -> upload your JSON with `/setup` -> the server builds itself.

| Command | What it does |
|---|---|
| `/setup` | Upload a JSON file. The bot validates it, shows a preview, and builds after you press **Confirm**. |
| `/ping` | Is the bot alive? |
| `/help` | Short instructions inside Discord. |
| `/status` | Bot health, server numbers, and whether the bot has the permissions it needs. |
| `/backup` | Exports the server's current structure as JSON (same format as `/setup`). |

**Safety:** admin-only commands - nothing is ever deleted - full validation first - preview + confirm button -
Administrator is only granted if your JSON says so - the token is never in the code.

---

## Files in this project

| File | Purpose |
|---|---|
| `bot.py` | The whole bot. |
| `requirements.txt` | Python libraries to install. |
| `.env.example` | Template for your secret token (for running on your own computer). |
| `Procfile` | Start command for hosts that read a Procfile. |
| `JSON_FORMAT.md` | The JSON format. **Give this to ChatGPT.** |
| `config_examples/` | Two sample JSON files you can test with. |

---

## STEP 1 - Create the Discord application (the bot account)

1. Go to https://discord.com/developers/applications and log in.
2. Click **New Application**, name it `DARCO SERVER BUILDER`, accept the terms, click **Create**.
3. Left menu -> **Bot**.
4. Click **Reset Token** (or **Copy**), confirm, and **copy the token**. Paste it somewhere private for now.
   - **The token is a password. Never post it in Discord, GitHub or send it to anyone.**
   - If it ever leaks: come back here and click Reset Token.
5. On the same page, under **Privileged Gateway Intents**: leave all of them **OFF**. This bot does not need them.
6. (Recommended) Turn **Public Bot** OFF so only you can add it to servers.

## STEP 2 - Invite the bot to your server

1. Left menu -> **General Information** -> copy the **Application ID**.
2. Build this link, replacing `YOUR_APPLICATION_ID`:

   `https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot%20applications.commands&permissions=8`

   * `permissions=8` = Administrator. This is the **easiest and most reliable** choice for building a server,
     because a bot can only give out permissions it has itself. You may remove it afterwards (see Step 8).
   * If you prefer minimal permissions, use `permissions=268436496` (Manage Roles + Manage Channels + View Channels).
     Then the bot cannot grant roles any permission it does not own.
3. Open the link, choose your server, click **Authorize**. You need the *Manage Server* permission to do this.
4. In Discord: **Server Settings -> Roles**, and **drag the bot's role to the very top** (just under you/the owner).
   *This is required.* A bot can only create and arrange roles **below** its own role.

## STEP 3 - Test it on your own computer (optional but recommended)

1. Install Python 3.10 or newer from https://www.python.org/downloads/ (tick **"Add Python to PATH"** on Windows).
2. Unzip this project. Open a terminal / Command Prompt **inside the project folder**.
3. Run:
   ```
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to a new file named `.env`, open it in Notepad, and replace `paste-your-bot-token-here`
   with your token. (Optional: add `DEV_GUILD_ID=` with your server's ID to make commands appear instantly.
   To get the ID: Discord Settings -> Advanced -> Developer Mode ON, then right-click your server -> Copy Server ID.)
5. Start the bot:
   ```
   python bot.py
   ```
   You should see `Logged in as DARCO SERVER BUILDER...`. In Discord type `/ping`.
   (First time, global slash commands can take a few minutes - up to an hour in rare cases - to show up.)

Running it on your own PC is the only truly free way to be sure it works - but it is only online while the PC is on.

## STEP 4 - Put the project on GitHub (needed by most free hosts)

1. Create a free account at https://github.com.
2. Click **New repository**, name it `darco-server-builder`, choose **Private**, click **Create**.
3. Click **uploading an existing file**, drag in all project files (`bot.py`, `requirements.txt`, `Procfile`,
   `README.md`, `JSON_FORMAT.md`, the `config_examples` folder, `.gitignore`), and **Commit**.
4. **Do NOT upload your `.env` file** (it contains your token). `.gitignore` already protects it if you use git.

## STEP 5 - Deploy on free hosting

### Honest warning about free hosting

* **No free host guarantees 24/7 uptime.** Free plans sleep, restart, change their rules, or disappear. Check each
  host's current free-plan terms before relying on it.
* Many "free web services" put the app to sleep when nobody visits its web address. For example, Render's free web
  services spin down after about 15 minutes without incoming web traffic - and a sleeping bot is **offline in Discord**.
* **Good news:** you do not need the bot online all the time. You only need it online for the few minutes it takes to run
  `/setup`. Wake it, run `/setup`, done. After the server is built the bot can be offline (or removed).
* This bot includes a tiny built-in web page (it activates automatically when the host sets a `PORT` variable) so it passes
  "web service" health checks and can be pinged by a free uptime monitor. Pinging helps keep it awake **but is not guaranteed**
  and some hosts may restrict it.

### Example: Render (free web service)

1. Create a free account at https://render.com and connect your GitHub.
2. **New +** -> **Web Service** -> pick your `darco-server-builder` repository.
3. Settings:
   * **Language / Runtime:** Python 3
   * **Build Command:** `pip install -r requirements.txt`
   * **Start Command:** `python bot.py`
   * **Instance Type:** Free
4. **Environment Variables** -> **Add**: key `DISCORD_TOKEN`, value = your token. (Optional: `DEV_GUILD_ID`.)
5. Click **Create Web Service**. Wait until the log says `Logged in as DARCO SERVER BUILDER`.
6. (Optional) To reduce sleeping: create a free account at https://uptimerobot.com, add an **HTTP(s) monitor** for your
   `https://your-service.onrender.com` address, interval 5 minutes.

### Other places that can run it

Any host that can run a Python program and set environment variables works with the same settings:
**Start command:** `python bot.py`, **Build/install command:** `pip install -r requirements.txt`,
**Environment variable:** `DISCORD_TOKEN`. (A Raspberry Pi, an old PC, or a cheap VPS also works.)

### Environment variables

| Name | Required | Meaning |
|---|---|---|
| `DISCORD_TOKEN` | **Yes** | Your bot token from Step 1. |
| `DEV_GUILD_ID` | No | Your server's ID, so slash commands update instantly there. |
| `PORT` | No | Set automatically by most hosts; starts the health web page. |

## STEP 6 - Get your JSON file

1. Open `JSON_FORMAT.md` and give its full text to ChatGPT.
2. Describe your server (categories, channels, roles, colors, who can see what). Use the prompt at the top of that file.
3. Save ChatGPT's answer as a file ending in `.json` (for example `my_server.json`). Use Notepad / TextEdit in plain-text
   mode, and make sure the name really ends in `.json`, not `.json.txt`.
4. You can test with `config_examples/example_server.json` first. Try a **dry run** on a test server.

## STEP 7 - Build your server with `/setup`

1. In your server type `/setup`, press Enter/Tab, and attach your `.json` file in the **file** box.
2. Options:
   * `dry_run: True` - preview only; nothing is changed. **Do this first.**
   * `update_existing: True` - also update roles/channels that already exist with the same name.
     (Default is False: existing things are left alone.)
3. The bot validates the file. If something is wrong it tells you exactly where (for example
   `categories[2] channels[1] 'rules': type 'video' is invalid`) and changes nothing.
4. If the file is valid you get a **preview** (attached `preview.txt`) and a **Confirm and build** button.
5. Press **Confirm**. You see live progress, then a final report with an attached `build_report.txt`.
6. Anything that failed is listed with the reason (missing permission, role too low, Community feature needed...).
   Fix it and run `/setup` again - already-created items are skipped, so it is safe to repeat.

## STEP 8 - After the build

* Assign the new roles to people yourself.
* If you invited the bot with Administrator, you can now remove that: Server Settings -> Roles -> the bot's role ->
  turn off Administrator (or kick the bot; the server stays as built).
* Use `/backup` any time to download your server structure as JSON.

---

## How things are handled (for the curious)

* **Rate limits:** discord.py automatically waits when Discord says "slow down"; the bot also pauses ~0.6 s between changes.
  A big server (100+ channels) takes a few minutes.
* **Role order:** roles are placed directly under the bot's own role in the exact order of the JSON (first = highest).
* **Permissions:** category permissions are copied to its channels; a channel's own overwrites are added on top.
  `allow` / `deny` / `inherit` are supported. Member-specific overwrites are not supported (roles only).
* **Never deletes** channels or roles. **Never grants Administrator** unless the JSON lists `"administrator"`.
* The bot refuses to run when the bot lacks a permission it would need to give out, and says which one.

## Troubleshooting

| Problem | Fix |
|---|---|
| Slash commands do not appear | Wait a few minutes; make sure you invited with the link that includes `applications.commands`; set `DEV_GUILD_ID`. Re-invite if needed. |
| `DISCORD_TOKEN is not set` | Add the environment variable (or `.env` file locally). |
| `LoginFailure` / improper token | Token was copied wrongly or was reset. Reset it again and re-paste. |
| `Forbidden (403)` in the report | Bot role is too low or lacks permission. Drag the bot's role to the top; give it Administrator while building. |
| "bot does not have 'xyz' itself" | The bot cannot grant a permission it does not own. Give the bot Administrator for setup. |
| "Community feature needed" | Announcement/stage/forum channels need Server Settings -> Enable Community. |
| "not valid JSON ... line X" | Paste the file into https://jsonlint.com and fix the marked line (usually a missing comma or quote). |
| Bot offline after a while | Free host went to sleep. Wake/redeploy it, or run it on your own PC for setup day. |

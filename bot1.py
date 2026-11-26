import os
import sys
import asyncio
import logging
import traceback
import importlib.metadata as md
import discord
from discord import app_commands
from dotenv import load_dotenv
from google import genai
from google.genai import types
import yt_dlp

logging.getLogger("google_genai").setLevel(logging.WARNING)    # hides "AFC is enabled" spam
logging.getLogger("discord.player").setLevel(logging.WARNING)  # hides ffmpeg terminate spam


def ver(name):
    try:
        return md.version(name)
    except Exception:
        return "NOT INSTALLED"


davey_ver = ver("davey")
print("Python:", sys.version.split()[0], "| discord.py:", ver("discord.py"),
      "| davey:", davey_ver, "| PyNaCl:", ver("PyNaCl"), "| yt-dlp:", ver("yt-dlp"))
if davey_ver == "NOT INSTALLED" or not davey_ver.startswith("0.1"):
    print("WARNING: music will be silent unless davey is 0.1.x. "
          "Run with: py -3.12 bot.py  (after: py -3.12 -m pip install davey==0.1.7)")

# Keys are read from your .env file (never typed into the code)
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not DISCORD_TOKEN or not GEMINI_API_KEY:
    raise SystemExit("Missing key! Check your .env file.")

gemini = genai.Client(api_key=GEMINI_API_KEY)

# ==================== EDIT THIS ====================
# The text the bot shows when someone runs /introduction.
# Replace "add text here" with your own text. You can use several lines
# by keeping the triple quotes.
INTRO_TEXT = """add text here"""
# ===================================================

# Models are tried in this order. If one fails or takes over 30 seconds,
# the next one is used.
MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
]

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

history = {}      # user id -> conversation so far
locks = {}        # user id -> lock (one message at a time)
handled = set()   # message ids already answered


# ---------------- INTRODUCTION ----------------
@tree.command(name="introduction", description="Introduces the bot")
async def introduction(interaction: discord.Interaction):
    # Discord allows 2000 characters per message, so split long text
    chunks = [INTRO_TEXT[i:i + 1900] for i in range(0, len(INTRO_TEXT), 1900)] or ["..."]
    await interaction.response.send_message(chunks[0])
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk)


# ---------------- MUSIC ----------------
YDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch",
    "js_runtimes": {"deno": {}},
}
FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn -loglevel warning"

queues = {}    # guild id -> list of songs waiting
current = {}   # guild id -> song that is playing right now
play_gen = {}  # guild id -> counter; a replaced song's "finished" callback is ignored


def extract(query):
    """Look up a song. Runs in a background thread so the bot doesn't freeze."""
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return {
            "title": info.get("title", "Unknown"),
            "page": info.get("webpage_url") or query,
            "url": info["url"],
            "duration": info.get("duration"),  # seconds (None for live streams)
        }


def parse_time(text):
    """Turn '37', '0:37', '1:30' or '1:02:15' into seconds. Returns None if invalid."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return seconds


def fmt_time(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def bump_gen(guild_id):
    """Make any running song's 'finished' callback stop auto-advancing."""
    play_gen[guild_id] = play_gen.get(guild_id, 0) + 1
    return play_gen[guild_id]


class SwappableSource(discord.AudioSource):
    """Wraps the audio so we can jump to another time without stopping the player."""

    def __init__(self, inner):
        self.inner = inner

    def read(self):
        inner = self.inner
        try:
            data = inner.read()
        except Exception:
            data = b""  # the old stream was closed mid-read (happens during a jump)
        # If we swapped streams while reading, read from the new one instead
        if not data and inner is not self.inner:
            return self.inner.read()
        return data

    def is_opus(self):
        return False

    def swap(self, new_inner):
        old = self.inner
        self.inner = new_inner
        old.cleanup()

    def cleanup(self):
        self.inner.cleanup()


def make_source(url, start=0):
    """Start an ffmpeg stream, optionally from a time in seconds."""
    before = FFMPEG_BEFORE
    if start:
        before = f"-ss {int(start)} " + before
    return discord.FFmpegPCMAudio(
        url, stderr=sys.stderr, before_options=before, options=FFMPEG_OPTIONS
    )


async def start_song(guild, song, start=0):
    """Start playing one song (optionally from a time). Returns True on success."""
    vc = guild.voice_client
    if vc is None or not vc.is_connected():
        return False

    try:
        fresh = await asyncio.to_thread(extract, song["page"])
        print("Playing:", fresh["title"], f"(from {fmt_time(start)})" if start else "")
        source = SwappableSource(make_source(fresh["url"], start))
        gen = bump_gen(guild.id)

        def after_play(err):
            if err:
                print("Playback error:", repr(err))
            # Only move to the next song if this song wasn't replaced or stopped
            if play_gen.get(guild.id) == gen:
                asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)

        current[guild.id] = {
            "title": fresh["title"],
            "page": fresh["page"],
            "url": fresh["url"],
            "duration": fresh.get("duration"),
            "source": source,
        }
        vc.play(source, after=after_play)
        return True
    except Exception as e:
        print("Music error:", repr(e))
        traceback.print_exc()
        return False


async def play_next(guild):
    """Play the next song in the queue (skipping any that fail)."""
    if guild.voice_client is None:
        return
    queue = queues.get(guild.id, [])
    while queue:
        song = queue.pop(0)
        if await start_song(guild, song, song.get("start", 0)):
            return


async def get_voice(interaction):
    """Join (or reuse) the user's voice channel. Returns the voice client or None."""
    channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client

    # Clean up a broken leftover connection
    if vc is not None and not vc.is_connected():
        await vc.disconnect(force=True)
        vc = None

    try:
        if vc is None:
            vc = await channel.connect()
        elif vc.channel != channel:
            await vc.move_to(channel)
        return vc
    except Exception as e:
        print("Voice connect error:", repr(e))
        await interaction.followup.send(f"Couldn't join voice: `{e}`")
        return None


@tree.command(name="music", description="Play a song in your voice channel")
@app_commands.describe(query="Song name or link")
async def music(interaction: discord.Interaction, query: str):
    if interaction.guild is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("Join a voice channel first!", ephemeral=True)
        return

    await interaction.response.defer()

    try:
        song = await asyncio.to_thread(extract, query)
    except Exception as e:
        print("yt-dlp error:", repr(e))
        await interaction.followup.send("Couldn't find or load that song.")
        return

    vc = await get_voice(interaction)
    if vc is None:
        return

    queues.setdefault(interaction.guild.id, []).append(song)

    if vc.is_playing() or vc.is_paused():
        await interaction.followup.send(f"Added to queue: **{song['title']}**")
    else:
        await interaction.followup.send(f"Now playing: **{song['title']}**")
        await play_next(interaction.guild)


# ----- /skip next  and  /skip to -----
skip_group = app_commands.Group(name="skip", description="Skip songs or jump to a time")


@skip_group.command(name="next", description="Skip to the next song")
async def skip_next(interaction: discord.Interaction):
    vc = interaction.guild.voice_client if interaction.guild else None
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()  # the song's callback starts the next one
        await interaction.response.send_message("Skipped.")
    else:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)


@skip_group.command(name="to", description="Jump to a time in the current song")
@app_commands.describe(time="Time to jump to")
async def skip_to(interaction: discord.Interaction, time: str):
    vc = interaction.guild.voice_client if interaction.guild else None
    if not vc or not (vc.is_playing() or vc.is_paused()):
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        return

    song = current.get(interaction.guild.id)
    if not song or "source" not in song:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        return

    seconds = parse_time(time)
    duration = song.get("duration")

    # Unreadable time, or past the end of the song
    if seconds is None or (duration and seconds >= duration):
        await interaction.response.send_message("Invalid", ephemeral=True)
        return

    try:
        print("Seeking to", fmt_time(seconds))
        song["source"].swap(make_source(song["url"], seconds))
    except Exception as e:
        print("Seek error:", repr(e))
        traceback.print_exc()
        await interaction.response.send_message(
            "Couldn't jump to that time. Check the terminal for the reason.", ephemeral=True)
        return

    await interaction.response.send_message(f"Jumping to **{fmt_time(seconds)}**...")


tree.add_command(skip_group)


@tree.command(name="stop", description="Stop the music and leave the voice channel")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client if interaction.guild else None
    if vc:
        bump_gen(interaction.guild.id)  # no auto-advance after this
        queues[interaction.guild.id] = []
        current.pop(interaction.guild.id, None)
        await vc.disconnect(force=True)
        await interaction.response.send_message("Stopped and left the channel.")
    else:
        await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)


@tree.command(name="beep", description="Play a 5 second test tone")
async def beep(interaction: discord.Interaction):
    if interaction.guild is None or not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("Join a voice channel first!", ephemeral=True)
        return

    await interaction.response.defer()
    vc = await get_voice(interaction)
    if vc is None:
        return

    bump_gen(interaction.guild.id)  # the beep shouldn't trigger the next queued song
    if vc.is_playing():
        vc.stop()
        await asyncio.sleep(0.3)

    vc.play(discord.FFmpegPCMAudio(
        "sine=frequency=440:duration=5",
        before_options="-f lavfi",
        options="-vn -af volume=4",
    ))
    await interaction.followup.send("Playing a 5 second beep. Can you hear it?")


# ---------------- GEMINI CHAT ----------------
async def ask_gemini(user_id, text):
    """Try each model in order. Returns (reply, model_used) or (None, None)."""
    contents = history.setdefault(user_id, [])
    contents.append(types.Content(role="user", parts=[types.Part(text=text)]))

    for model in MODELS:
        try:
            print(f"Trying {model}...")
            response = await asyncio.wait_for(
                gemini.aio.models.generate_content(model=model, contents=contents),
                timeout=30,  # give up on this model after 30 seconds
            )
            reply = response.text
            if not reply:
                raise ValueError("empty reply")

            contents.append(types.Content(role="model", parts=[types.Part(text=reply)]))
            # Keep only the last 20 messages so memory stays small
            while len(contents) > 20:
                del contents[:2]
            return reply, model
        except Exception as e:
            print(f"[{model}] failed: {repr(e)}")

    # Every model failed: remove the question so history stays clean
    contents.pop()
    return None, None


synced = False


@bot.event
async def on_ready():
    global synced
    if not synced:
        await tree.sync()  # registers /introduction, /music, /skip, /stop, /beep
        synced = True
    print(f"Logged in as {bot.user}")


@bot.event
async def on_message(message):
    # Ignore bots (including itself)
    if message.author.bot:
        return

    # In servers, only reply when @mentioned. In DMs, always reply.
    is_dm = message.guild is None
    if not is_dm and bot.user not in message.mentions:
        return

    # Skip messages we've already handled
    if message.id in handled:
        return
    handled.add(message.id)
    if len(handled) > 1000:
        handled.clear()

    # Remove the @mention so Gemini only sees the question
    text = message.content
    text = text.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
    if not text:
        await message.reply("Hi! Ask me something after the mention.")
        return

    user_id = message.author.id

    # Reset command: clears this user's memory
    if text.lower() in ("!reset", "reset"):
        history.pop(user_id, None)
        await message.reply("Memory cleared. Starting fresh!")
        return

    # One message at a time per user
    lock = locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        async with message.channel.typing():
            reply, model_used = await ask_gemini(user_id, text)

        if reply is None:
            await message.reply("Sorry, all my AI models failed. You may have hit the rate limit, so try again in a minute.")
            return

        print(f"Answered using {model_used}")

        watermark = f"\n-# Replied using Gemini ({model_used})"

        # Leave room for the watermark under Discord's 2000 character limit
        chunks = [reply[i:i + 1900] for i in range(0, len(reply), 1900)]
        chunks[-1] += watermark

        for chunk in chunks:
            await message.reply(chunk)


bot.run(DISCORD_TOKEN)
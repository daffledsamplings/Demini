

Demini is an open source multi-purpose Discord bot which combines conversational AI (powered by Google Gemini) with voice channel music playback via discord.py, davey (DAVE audio), yt-dlp and FFmpeg. You can mention Demini in a server or send a DM to chat, or use slash commands to stream audio and test out the voice playback.

Features

Gemini Conversational Chat: Chat by @mentioning Demini in a server or messaging it directly in DMs, with per-user conversation history, automatic model fallback (gemini-3.8-flash -> gemini-3.5-flash-lite) and !reset / reset to clear memory.

Voice Channel Music & Audio: Queue and play tracks with /music, jump to the next song with /skip next, seek to a timestamp using /skip to, leave and clear the queue with /stop and test your voice with /beep.

Bot Introduction: Display custom bot details via /introduction (edit INTRO_TEXT in bot.py)

Prerequisites

Python 3.12 (recommended for davey==0.1.7 voice audio support)

FFmpeg installed and available on your system PATH

Deno installed and available on your system PATH (used by yt-dlp via js_runtimes)

Setup

1. Get a Gemini API Key

Go to Google AI Studio and create an API key.

Keep your key a secret and add it to your local .env file.

2. Create a Discord Application & Bot

Head to the Discord Developer Portal and create a new application.

Open the Bot tab, set up your bot profile and copy the Bot Token.

Under Privileged Gateway Intents, enable Message Content Intent so that Demini can read @mentions, DMs and !reset commands.

3. Install & Run Locally

Clone the repository:

Bash

git clone https://github.com/<your-username>/demini.git

cd demini

Install Python dependencies (using a virtual environment or Python 3.12):

Bash

py -3.12 -m pip install -r requirements.txt

Customise INTRO_TEXT in bot.py if you want to.

Copy .env.example to .env and fill in your DISCORD_TOKEN and GEMINI_API_KEY:

Bash

copy .env.example .env

(On macOS/Linux use cp .env.example .env)

Start Demini:

Bash

py -3.12 bot.py

4. Invite Demini to Your Server

In the Discord Developer Portal, under OAuth2 > URL Generator, select the bot and applications.commands scopes, enable the permissions to send messages, read message history and connect/speak in voice channels and then open the generated invite link.

Commands

/introduction — Shows the configured bot introduction message.

/music <query> — Searches for or loads a song/link and plays or queues it in your current voice channel.

/skip next — Skips to the next track in the queue.

/skip to <time> — Jumps to a timestamp (<time> e.g. 0:37 or 1:15) in the currently playing track.

/stop — Stops playback, clears the queue and disconnects from the voice channel.

/beep — Plays a 5 second 440hz test tone to check voice audio.

@Demini <message> or DM — chats with Gemini (!reset or reset clears your chat history).

Contributing

Issues and PRS are welcome. If you're looking at a major change or a new feature then please open an issue first to discuss it.

Extra notes : You can change the name of your bot to any other name you'd like. 

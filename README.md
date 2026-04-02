# Boom Master 🎙️

An AI A&R agent deployed as a Telegram bot for the Audiera Agent-Native Challenge.

Try it live: @boommaster_agent_bot

## What Boom Master Does

Boom Master thinks like a music executive. It scouts emotional themes, commissions real songs using official Audiera skills, writes an A&R pitch, and earns $BEAT on BSC.

## Demo Song
https://ai.audiera.fi/music/3440

## Full Loop
1. /create - AI suggests emotional narrative themes
2. Pick a style (Hip-Hop, Soul, R&B, Pop...)
3. Pick a mood (Heartbreak, Euphoric, Nostalgic...)
4. Boom Master generates lyrics via Audiera Lyrics Skill
5. You approve or edit the lyrics
6. Song produced via Audiera Music Skill
7. Live Audiera link delivered in Telegram
8. $BEAT earned on BSC

## Stack
- Agent framework: OpenClaw
- Skills: Audiera Music Skill + Audiera Lyrics Skill
- LLM: OpenRouter free tier for A&R intelligence
- Interface: Telegram Bot (python-telegram-bot 22.7)
- Chain: BSC
- Wallet: 0x88c773d7c09d7889bd5e47dc588a390fa05989da

## Skills Used
- https://github.com/audiera/music-skill
- https://github.com/audiera/lyrics-skill

## Setup

Step 1 - Install Termux from F-Droid and run:
pkg update && pkg upgrade -y
pkg install proot-distro -y
proot-distro install ubuntu
proot-distro login ubuntu

Step 2 - Set up Ubuntu:
apt update && apt upgrade -y
apt install curl git python3 python3-pip -y
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt install -y nodejs

Step 3 - Install OpenClaw:
npm install -g openclaw@latest
openclaw onboard --install-daemon

Step 4 - Clone Audiera skills:
mkdir -p ~/Projects/skills
git clone https://github.com/audiera/music-skill.git ~/Projects/skills/music-skill
git clone https://github.com/audiera/lyrics-skill.git ~/Projects/skills/lyrics-skill

Step 5 - Install bot dependencies:
pip3 install python-telegram-bot==22.7 requests --break-system-packages

Step 6 - Set environment variables:
export TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
export AUDIERA_API_KEY="your_audiera_api_key"
export OPENROUTER_API_KEY="your_openrouter_api_key"

Step 7 - Start OpenClaw gateway (session 1):
openclaw gateway

Step 8 - Run the bot (session 2):
python3 bot.py

## Contest
Built for the Audiera Agent-Native Challenge.
Create -> Participate -> Earn
#AudieraAI #BEAT #BinanceAI

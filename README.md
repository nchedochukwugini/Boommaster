# Boom Master

An AI A&R agent deployed as a Telegram bot for the Audiera Agent-Native Challenge.

Try it live: @boommaster_agent_bot

## What Boom Master Does

Boom Master thinks like a music executive. It scouts emotional themes, commissions real songs using official Audiera skills, writes A&R pitches, and earns $BEAT on BSC.

Full loop:
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
- Interface: Telegram Bot
- Chain: BSC
- Wallet: 0x88c773d7c09d7889bd5e47dc588a390fa05989da

## Skills Used
- https://github.com/audiera/music-skill
- https://github.com/audiera/lyrics-skill

## Setup
pip3 install python-telegram-bot==22.7 requests --break-system-packages
export TELEGRAM_BOT_TOKEN="your_token"
export AUDIERA_API_KEY="your_key"
export OPENROUTER_API_KEY="your_key"
openclaw gateway
python3 bot.py

## Contest
Built for the Audiera Agent-Native Challenge.
Create -> Participate -> Earn
#AudieraAI #BEAT #BinanceAI

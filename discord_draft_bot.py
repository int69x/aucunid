import os
import re
import json
import time
import asyncio
from typing import List, Dict, Any
import aiohttp
import sqlite3
import discord
from discord.ext import commands
from riotwatcher import LolWatcher
import openai
from discord import app_commands

# --- CONFIGURATION ---
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
REGION = os.getenv("RIOT_REGION", "euw")
CACHE_TTL = 300  # seconds
DATABASE = os.getenv("CACHE_DB", "cache.db")

# Initialize Riot & OpenAI
watcher = LolWatcher(RIOT_API_KEY)
openai.api_key = OPENAI_API_KEY

# Discord bot setup with intents and app commands
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Initialize SQLite cache
conn = sqlite3.connect(DATABASE)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS champ_cache (
    summoner TEXT PRIMARY KEY,
    timestamp REAL,
    data TEXT
)
""")
conn.commit()

async def fetch_html(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, headers={"User-Agent": "DiscordDraftBot/2.0"}, timeout=10) as resp:
        resp.raise_for_status()
        return await resp.text()

async def get_champion_stats_from_opgg(summoner: str) -> List[Dict[str, Any]]:
    # Check SQLite cache
    now = time.time()
    c.execute("SELECT timestamp,data FROM champ_cache WHERE summoner=?", (summoner,))
    row = c.fetchone()
    if row and now - row[0] < CACHE_TTL:
        return json.loads(row[1])
    url = f"https://{REGION}.op.gg/summoners/{REGION}/{summoner}"
    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, url)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    names = soup.select(".ChampionName")
    ratios = soup.select(".ChampionRatio")
    champs = []
    for name_tag, ratio_tag in zip(names, ratios):
        try:
            winrate = float(ratio_tag.text.strip().replace('%',''))
            champs.append({"name": name_tag.text.strip(), "winrate": winrate})
        except ValueError:
            continue
    top5 = champs[:5]
    # Store in cache
    c.execute("REPLACE INTO champ_cache (summoner,timestamp,data) VALUES (?,?,?)",
              (summoner, now, json.dumps(top5)))
    conn.commit()
    return top5

@app_commands.command(name="opgg", description="Analyse la team ennemie via op.gg et recommande des bans")
@app_commands.describe(link="Lien multi op.gg (5 invocateurs)")
async def slash_opgg(interaction: discord.Interaction, link: str):
    await interaction.response.defer(thinking=True)
    # Parse summoners
    match = re.search(r"multi/query=([^/?]+)", link)
    if not match:
        await interaction.followup.send("❌ Lien invalide.")
        return
    summoners = match.group(1).split(',')
    if len(summoners) != 5:
        await interaction.followup.send("❌ Il faut exactement 5 invocateurs.")
        return
    # Fetch concurrently
    tasks = [get_champion_stats_from_opgg(s) for s in summoners]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    embed = discord.Embed(title="Analyse OPGG & Bans Suggestion", color=discord.Color.blue())
    summary_lines = []
    for name, res in zip(summoners, results):
        if isinstance(res, Exception) or not res:
            embed.add_field(name=name, value="⚠️ Pas de données", inline=False)
            summary_lines.append(f"{name}: Aucune donnée")
        else:
            champs_str = ", ".join(f"{c['name']} ({c['winrate']}%)" for c in res)
            embed.add_field(name=name, value=champs_str, inline=False)
            summary_lines.append(f"{name}: {champs_str}")
    # Build AI prompt
    prompt = "Stats adverses:\n" + "\n".join(summary_lines)
    prompt += "\nDonne 3 champions à bannir et un court argumentaire pour chaque."  
    # Call AI
    try:
        ai_resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role":"system","content":"Assistant expert en draft LoL."},
                {"role":"user","content":prompt}
            ],
            temperature=0.7,
            max_tokens=600
        )
        analysis = ai_resp.choices[0].message.content.strip()
        embed.add_field(name="Recommandations (IA)", value=analysis, inline=False)
    except Exception:
        # Fallback
        flat = [c for r in results if isinstance(r,list) for c in r[:3]]
        counter = {}
        for c in flat:
            counter[c['name']] = counter.get(c['name'],0) + c['winrate']
        top3 = sorted(counter, key=counter.get, reverse=True)[:3]
        embed.add_field(name="Recommandations (fallback)", value=", ".join(top3), inline=False)
    await interaction.followup.send(embed=embed)

@bot.event
async def on_ready():
    bot.tree.add_command(slash_opgg)
    await bot.tree.sync()
    print(f"Connecté en tant que {bot.user} (ID: {bot.user.id})")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

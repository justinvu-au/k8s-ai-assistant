import os
import logging
import discord
from dotenv import load_dotenv
import claude_agent

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    log.info(f"K8s AI Assistant logged in as {client.user} — listening for mentions...")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    if client.user not in message.mentions:
        return

    question = message.content
    for mention in message.mentions:
        question = question.replace(f"<@{mention.id}>", "").strip()

    log.info(f"Received question: {question}")
    thinking_msg = await message.channel.send("🔍 Checking the cluster...")

    try:
        answer = claude_agent.ask(question)
    except Exception as e:
        log.error(f"Error answering question: {e}")
        answer = f"Something went wrong: {e}"

    # Discord has a 2000 character limit per message
    if len(answer) > 1900:
        answer = answer[:1900] + "\n... (truncated)"

    await thinking_msg.edit(content=answer)


if __name__ == "__main__":
    client.run(os.getenv("DISCORD_BOT_TOKEN"))
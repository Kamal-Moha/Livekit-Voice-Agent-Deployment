# agent.py

import os
import yaml

def load_prompt(filename):
  """Load a prompt from a YAML file."""
  prompt_path = os.path.join(filename)

  try:
    with open(prompt_path, 'r') as file:
        prompt_data = yaml.safe_load(file)
        return prompt_data.get('instructions', '')
  except (FileNotFoundError, yaml.YAMLError) as e:
    print(f"Error loading prompt file {filename}: {e}")
    return ""


import logging
import aiohttp
from typing import Annotated, Optional, Literal
from pathlib import Path
from dotenv import load_dotenv
from livekit.agents import JobContext, WorkerOptions, cli, llm, get_job_context
from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, AgentSession, RunContext
from livekit.plugins import (
    google,
    noise_cancellation
    )

from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions


logger = logging.getLogger("function-calling")
logger.setLevel(logging.INFO)

# load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')
load_dotenv()

import nest_asyncio
nest_asyncio.apply()

class FunctionAgent(Agent):
    def __init__(self, username, auth_key) -> None:
        self.username = username
        self.auth_key = auth_key
        super().__init__(
            instructions=load_prompt('support_agent.yaml'),
            llm=google.beta.realtime.RealtimeModel(
            model= "gemini-live-2.5-flash-preview",
            voice="Puck",
            temperature=0.8,
            ),
        )

    @function_tool
    async def list_passes(
        self, context: RunContext,
        transit_provider: Literal['DDOT', 'SMART', 'Regional']
    ):


        """
        Called when the user asks you or wants to know of the available passes.
        This tool takes 'transit_provider', encourage the user to give the name of the transit provider. If not given, then list available passes from each transit provider.

        Args:
            transit_provider: The transit provider to list passes from. For example 'DDOT', 'SMART', 'Regional'
        """

        logger.info(f"getting available passes for {transit_provider}")

        headers = {
            'accept': '*/*',
            'accept-language': 'en-US,en;q=0.9',
            'authorization': f"Bearer {self.auth_key}",
            'content-type': 'application/json'
        }

        params = {
            'limit': '100',
            'offset': '0',
            'show_all': 'true',
        }

        async with aiohttp.ClientSession(f"{os.environ['MYRIDE_WALLET_API']}") as session:
            async with session.get(f"/api/passes?msp%5B%5D={transit_provider}", headers=headers, params=params) as response:
                if response.status == 200:
                    passes_data = await response.text()
                    logger.info(passes_data)
                    return passes_data
                else:
                    raise Exception(
                        f"Failed to list available passes, status code: {response.status}"
                    )

    @function_tool
    async def check_balances(self, context: RunContext):
        """
        Called when you need to know the available balances in user's wallets.
        Check both the user's personal wallet and the subsidy wallet.
        """

        logger.info(f"Checking user's wallets")

        headers = {
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9',
        'authorization': f"Bearer {self.auth_key}",
        'content-type': 'application/json'
        }

        params = {
            'limit': '100',
            'offset': '0',
            'show_all': 'true',
        }

        async with aiohttp.ClientSession(f"{os.environ['MYRIDE_WALLET_API']}") as session:
            balances = []
            for i in ['subsidy', 'personal']:
                async with session.get(f"api/rider/{i}-wallet", headers=headers, params=params) as response:
                    if response.status == 200:
                        bal = await response.text()
                        logger.info(bal)
                        balances.append(bal)
                    else:
                        raise Exception(
                            f"Failed to get the user's subsidy wallet: {response.status}"
                        )
            logger.info(balances)
            return balances

    @function_tool
    async def purchase_passes(self, 
        context: RunContext,
        pass_id: str, 
        type_of_wallet: Literal['Subsidy', 'Personal'], 
        quantity: int = 1
    ):

        """
        Called when the user wants to purchase a pass
        This tool requires to have the 'pass_id' of the pass.
        The user will give you the pass name and the transit_provider name.
        Then you have to get the pass_id yourself by checking the list_passes tool and passing the required transit_provider name.
        Use the check_balances tool to know the amount in the user's wallets. And more importantly understand the type of wallet to use for the purchase.
        Check if user has sufficient balance to make the purchase
        Tell the user how much funds they have in each wallet
        Request the user to tell you which type of wallet to use for the purchase. But if one of the wallets has zero balance, then inform the user and use the correct wallet.
        Confirm with the user and inform the user the action you are gonna take

        Args:
            pass_id: The id of the desired pass/fare.
            type_of_wallet: The type of wallet to use for the purchase. For example 'Subsidy' or 'Personal'
            quantity: The quantity of passes to purchase.
            
        """
        logger.info(f"Purchasing pass: {pass_id}")

        headers = {
        'accept': '*/*',
        'authorization': f"Bearer {self.auth_key}",
        'Content-Type': 'application/json'
        }        

        data = {
            "wallet_type": type_of_wallet,
            "pass_ids": [
                {
                "pass_id": pass_id,
                "quantity": quantity
                }
            ]
        }
        logger.info(data)

        async with aiohttp.ClientSession(f"{os.environ['MYRIDE_WALLET_API']}") as session:
            async with session.post("/api/rider/passes", json=data, headers=headers) as response:
                logger.info(f"RESPONSE: {await response.json()}") # Await the json() coroutine
                if response.status == 200:
                    spec_purchase_pass = await response.json()
                    logger.info(spec_purchase_pass)
                    return spec_purchase_pass
                else:
                    raise Exception(
                        f"Failed to purchase pass {pass_id}, status code: {response.status}"
                    )


    async def on_enter(self):
        self.session.generate_reply(
            instructions=f"""Greet {self.username}.
            Inform them that you're here to assist with anything about MyRideWallet app.
            Be conversational, knowledgeable, and remember past conversations.
            """
        )


async def entrypoint(ctx: JobContext):

    await ctx.connect()

    participant = await ctx.wait_for_participant()

    logger.info("Participant Info ***")

    logger.info(f"Identity: {participant.identity}")
    logger.info(f"Name: {participant.name}")
    logger.info(f"ATTRIBUTES: {participant.attributes}")

    auth_key = participant.attributes.get("auth_key")
    username = participant.attributes.get("username")

    session = AgentSession()

    await session.start(
        agent=FunctionAgent(username, auth_key),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        )
    )
    
if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        initialize_process_timeout=120
    ))
    
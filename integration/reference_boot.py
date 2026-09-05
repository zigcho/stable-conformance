"""Reference startup with explicit fixture memberships, not patched handlers."""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

import uvicorn
import app.api.init_api as api
import app.logging
import app.settings
import app.state.sessions

app.logging.configure_logging()
# The reference env parser is CSV, not JSON: SEASONAL_BGS=[] would mean
# ["[]"], and an empty string means [""]. Supply the actual typed fixture.
app.settings.SEASONAL_BGS = []
production_lifespan = api.asgi_app.router.lifespan_context


@asynccontextmanager
async def fixture_lifespan(application):
    async with production_lifespan(application):
        # Zigcho's permanent bot is a member of these public channels. Mirror
        # that fixture through the reference's real membership implementation.
        # Identity remains reference id 1, Zigcho id 3; no packets are rewritten.
        bot = app.state.sessions.bot
        for name in ("#osu", "#announce"):
            channel = app.state.sessions.channels[name]
            if channel is None or not bot.join_channel(channel):
                raise RuntimeError("could not establish reference bot fixture membership")
        yield


api.asgi_app.router.lifespan_context = fixture_lifespan
uvicorn.run(api.asgi_app, host=app.settings.APP_HOST, port=app.settings.APP_PORT,
            log_level="warning", server_header=False, date_header=False,
            headers=[("bancho-version", app.settings.VERSION)])

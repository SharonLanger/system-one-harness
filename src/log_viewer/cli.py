"""CLI lifecycle; diagnostics stay out of the shared application log."""
import sys
import webbrowser
from .http import ViewerServer
from .reader import LogReader
from .service import LogQueryService

def run(no_open=False):
    import log_utils
    server=ViewerServer(LogQueryService(LogReader(log_utils.LOG_ROOT)))
    print(server.url,flush=True)
    if not no_open:
        try:
            if not webbrowser.open(server.url): print('Browser did not open; use the local URL above.',file=sys.stderr)
        except Exception:
            print('Browser did not open; use the local URL above.',file=sys.stderr)
    try: server.serve_forever(poll_interval=.1)
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0

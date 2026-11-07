import os
from pathlib import Path

import reflex as rx
from toc_rag.config import get_settings


def _keep_data_out_of_hot_reload() -> None:
    """Stop `reflex run` from restarting the backend whenever the app saves a file.

    In dev mode the backend restarts when a file in any top-level folder of the project changes;
    only a few types (.json, .txt, ...) are ignored. Uploads (.docx) and extracted images (.png)
    are saved under the data folder, so without this every upload restarts the backend. The new
    worker then compiles the app on its first request, which rewrites the page's stylesheets: the
    visitor's first click freezes for seconds and the page flashes as the CSS is swapped.
    Reflex splits this setting on ":", so the folder is given relative to the project (a Windows
    absolute path contains a ":").
    """
    root = Path(__file__).resolve().parent
    try:
        relative = get_settings().data_dir.resolve().relative_to(root)
    except ValueError:
        return  # the data folder is outside the project, so it is not watched
    if not relative.parts:
        return
    top = relative.parts[0]
    (root / top).mkdir(parents=True, exist_ok=True)  # Reflex compares paths with samefile, which needs it to exist
    existing = [p for p in os.environ.get("REFLEX_HOT_RELOAD_EXCLUDE_PATHS", "").split(":") if p]
    if top not in existing:
        os.environ["REFLEX_HOT_RELOAD_EXCLUDE_PATHS"] = ":".join([*existing, top])


_keep_data_out_of_hot_reload()


def _public_address() -> dict[str, str]:
    """The address the page uses to reach the backend, when deployed.

    In production the frontend is built with this address in it, so it must be the app's public
    address, not localhost. Set API_URL to it on any host; Reflex reads that variable itself. On
    Railway, if API_URL is not set, it is taken from the public domain Railway provides.
    """
    if os.getenv("API_URL") or os.getenv("REFLEX_API_URL"):
        return {}
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    return {"api_url": f"https://{domain}", "deploy_url": f"https://{domain}"} if domain else {}


config = rx.Config(
    app_name="toc_app",
    plugins=[
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(appearance="light", accent_color="gray", gray_color="sand", radius="large")
        )
    ],
    disable_plugins=[rx.plugins.SitemapPlugin],
    **_public_address(),
)

import reflex as rx

config = rx.Config(
    app_name="pageindex_app",
    plugins=[
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(appearance="light", accent_color="gray", gray_color="sand", radius="large")
        )
    ],
    disable_plugins=[rx.plugins.SitemapPlugin],
)

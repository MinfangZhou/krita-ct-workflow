"""
CT Workflow - Krita Docker Plugin
Canvas thumbnail preview with real-time effects (Blur/Desaturate/Invert)
"""

from krita import Krita, DockWidgetFactory, DockWidgetFactoryBase

from .docker import CTNavigatorDocker

# Unique Docker ID
DOCKER_ID = "ctNavigator"

# Register the Docker panel
def register_dockers():
    app = Krita.instance()
    if app:
        factory = DockWidgetFactory(
            DOCKER_ID,
            DockWidgetFactoryBase.DockRight,
            CTNavigatorDocker
        )
        app.addDockWidgetFactory(factory)

# Entry point
register_dockers()

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "langgraph_api.settings")

from django.core.asgi import get_asgi_application  # noqa: E402

application = get_asgi_application()

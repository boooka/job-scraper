import os

from django.contrib import admin
from django.urls import path

# The admin lives under a secret, env-configured path instead of the well-known
# /admin so scanners and bots can't find it. Root and every other path 404, and
# there is deliberately NO redirect to the admin (that would leak the path).
_admin_path = os.environ.get("DJANGO_ADMIN_PATH", "admin").strip("/")

urlpatterns = [
    path(f"{_admin_path}/", admin.site.urls),
]

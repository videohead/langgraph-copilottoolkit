from django.urls import path
from . import views

urlpatterns = [
    path("api/health/", views.health),
    path("api/graphs/", views.list_graphs),
    path("api/agents/<str:graph_name>/", views.run_agent),
]

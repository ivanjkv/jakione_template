from django.urls import path
from . import views

urlpatterns = [
    path('', views.livescores, name='livescores'),
    path('fixtures/', views.fixtures, name='fixtures'),
    path('deadlines/', views.deadlines, name='deadlines'),
    path('upload_predictions/', views.upload_predictions, name='upload_predictions'),
    path('match/<str:matchid>', views.match, name='match'),
    path('user/<str:userid>', views.user, name='user'),
    path('privacy', views.privacy, name='privacy-policy'),
    path('refresh', views.refresh, name='refresh'),
]

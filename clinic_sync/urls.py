"""
URL configuration for clinic_sync project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from sync_core import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', views.institution_list, name='institution-list'),
    path('institutions/<uuid:institution_id>/', views.institution_detail, name='institution-detail'),
    path(
        'institutions/<uuid:institution_id>/facilities/<uuid:facility_id>/',
        views.facility_detail,
        name='facility-detail',
    ),
    path(
        'institutions/<uuid:institution_id>/facilities/<uuid:facility_id>/data/',
        views.facility_data_browser,
        name='facility-data-browser',
    ),
    path(
        'institutions/<uuid:institution_id>/facilities/<uuid:facility_id>/data/<str:table_name>/',
        views.facility_data_table,
        name='facility-data-table',
    ),
    path('health', views.health, name='api-health'),
    path('api/devices/enroll', views.enroll_device_api, name='api-device-enroll'),
    path('api/devices/authenticate', views.authenticate_device_api, name='api-device-authenticate'),
    path('api/devices/me', views.device_me_api, name='api-device-me'),
    path('api/sync/push', views.sync_push_api, name='api-sync-push'),
    path('api/sync/pull', views.sync_pull_api, name='api-sync-pull'),
    path('api/repository/records', views.repository_records_api, name='api-repository-records'),
]

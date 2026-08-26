from django import forms

from .models import Facility, Institution


class InstitutionForm(forms.ModelForm):
    class Meta:
        model = Institution
        fields = ["name", "code", "address", "timezone"]


class FacilityForm(forms.ModelForm):
    class Meta:
        model = Facility
        fields = ["name", "code", "address", "timezone", "sync_enabled"]


class DeviceEnrollmentForm(forms.Form):
    device_name = forms.CharField(max_length=160)
    location_name = forms.CharField(max_length=160, required=False)
    expires_in_hours = forms.TypedChoiceField(
        choices=[(1, "1 hour"), (8, "8 hours"), (24, "24 hours"), (72, "3 days")],
        coerce=int,
        initial=24,
    )

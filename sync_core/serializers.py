from rest_framework import serializers


class EnrollDeviceSerializer(serializers.Serializer):
    token = serializers.CharField(min_length=32, max_length=512, trim_whitespace=True)
    installationId = serializers.UUIDField()
    deviceName = serializers.CharField(min_length=1, max_length=160, trim_whitespace=True, required=False)
    platform = serializers.CharField(min_length=1, max_length=80, trim_whitespace=True)
    appVersion = serializers.CharField(min_length=1, max_length=40, trim_whitespace=True)


class AuthenticateDeviceSerializer(serializers.Serializer):
    clientKey = serializers.CharField(min_length=1, max_length=80, trim_whitespace=True)
    clientSecret = serializers.CharField(min_length=32, max_length=512, trim_whitespace=True)


class RepositoryRecordSerializer(serializers.Serializer):
    tableName = serializers.CharField(min_length=1, max_length=100, trim_whitespace=True)
    recordId = serializers.CharField(min_length=1, max_length=160, trim_whitespace=True)
    payload = serializers.DictField()
    version = serializers.IntegerField(min_value=1)
    deleted = serializers.BooleanField(default=False)
    sourceUpdatedAt = serializers.DateTimeField(required=False, allow_null=True)


class RepositoryBatchSerializer(serializers.Serializer):
    records = RepositoryRecordSerializer(many=True, allow_empty=False, max_length=500)


class SyncEventSerializer(serializers.Serializer):
    eventId = serializers.UUIDField()
    entityType = serializers.CharField(min_length=1, max_length=100, trim_whitespace=True)
    entityPublicId = serializers.CharField(min_length=1, max_length=160, trim_whitespace=True)
    operation = serializers.ChoiceField(choices=["create", "update", "delete"])
    baseVersion = serializers.IntegerField(min_value=0)
    entityVersion = serializers.IntegerField(min_value=1)
    payload = serializers.DictField()

    def validate(self, attrs):
        if attrs["entityVersion"] != attrs["baseVersion"] + 1:
            raise serializers.ValidationError("entityVersion must equal baseVersion + 1.")
        return attrs


class SyncPushSerializer(serializers.Serializer):
    events = SyncEventSerializer(many=True, allow_empty=False, max_length=200)


class SyncPullSerializer(serializers.Serializer):
    after = serializers.IntegerField(min_value=0, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=500, default=200)

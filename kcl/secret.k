"""
Secrets for homerun2-sound-catcher (Redis password, /play API token, S3 credentials)
"""

import base64
import labels

config = labels.config

# Secret - Redis Password
secretRedis = {
    apiVersion: "v1"
    kind: "Secret"
    metadata: {
        name: "{}-redis".format(config.name)
        namespace: config.namespace
        labels: labels.commonLabels
    }
    type: "Opaque"
    data: {
        password: base64.encode(config.redisPassword)
    }
} if config.redisPassword else None

# Secret - /play API token
secretApi = {
    apiVersion: "v1"
    kind: "Secret"
    metadata: {
        name: "{}-api".format(config.name)
        namespace: config.namespace
        labels: labels.commonLabels
    }
    type: "Opaque"
    data: {
        token: base64.encode(config.apiToken)
    }
} if config.apiToken else None

# Secret - S3 credentials for the sound bucket (read-only user)
secretS3 = {
    apiVersion: "v1"
    kind: "Secret"
    metadata: {
        name: "{}-s3".format(config.name)
        namespace: config.namespace
        labels: labels.commonLabels
    }
    type: "Opaque"
    data: {
        accessKey: base64.encode(config.s3AccessKey)
        secretKey: base64.encode(config.s3SecretKey)
    }
} if config.s3AccessKey else None

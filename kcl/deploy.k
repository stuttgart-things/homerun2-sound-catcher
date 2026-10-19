"""
Deployment for homerun2-sound-catcher — browser sink only

There is no speaker in a pod: SOUND_SINKS=browser, and the player page
behind the HTTPRoute plays the announcements.
"""

import labels

config = labels.config

deployment = {
    apiVersion: "apps/v1"
    kind: "Deployment"
    metadata: {
        name: config.name
        namespace: config.namespace
        labels: labels.commonLabels
    }
    spec: {
        replicas: config.replicas
        selector: {
            matchLabels: labels.selectorLabels
        }
        # Recreate, not RollingUpdate: never two pods at once. They would
        # split the consumer group's messages, and each page would follow
        # only one pod's history.
        strategy: {
            type: "Recreate"
        }
        template: {
            metadata: {
                labels: labels.commonLabels
            }
            spec: {
                serviceAccountName: config.name
                containers: [
                    {
                        name: config.name
                        image: config.image
                        imagePullPolicy: config.imagePullPolicy
                        ports: [
                            {
                                name: "http"
                                containerPort: config.containerPort
                                protocol: "TCP"
                            }
                        ]
                        env: [
                            {
                                name: "REDIS_ADDR"
                                value: config.redisAddr
                            }
                            {
                                name: "REDIS_PORT"
                                value: str(config.redisPort)
                            }
                            {
                                # secretKeyRef with optional: True, as in
                                # led-catcher: out-of-band Secret providers can
                                # fill it, and without one Redis is unauthenticated.
                                name: "REDIS_PASSWORD"
                                valueFrom: {
                                    secretKeyRef: {
                                        name: "{}-redis".format(config.name)
                                        key: "password"
                                        optional: True
                                    }
                                }
                            }
                            {
                                name: "REDIS_STREAMS"
                                value: config.redisStreams
                            }
                            {
                                name: "CONSUMER_GROUP"
                                value: config.consumerGroup
                            }
                            {
                                name: "SOUND_MODE"
                                value: config.soundMode
                            }
                            {
                                name: "SOUND_SINKS"
                                value: "browser"
                            }
                            {
                                # Optional: without the Secret, /play and mute
                                # are not registered.
                                name: "SOUND_API_TOKEN"
                                valueFrom: {
                                    secretKeyRef: {
                                        name: "{}-api".format(config.name)
                                        key: "token"
                                        optional: True
                                    }
                                }
                            }
                            {
                                name: "HISTORY_SIZE"
                                value: str(config.historySize)
                            }
                            {
                                name: "HEALTH_PORT"
                                value: str(config.healthPort)
                            }
                            {
                                name: "BASE_PATH"
                                value: config.basePath
                            }
                            {
                                name: "PROFILE_PATH"
                                value: config.profilePath
                            }
                            # Sounds from S3: an empty bucket turns the sync off.
                            {
                                name: "SOUNDS_S3_BUCKET"
                                value: config.s3Bucket
                            }
                            {
                                name: "SOUNDS_S3_ENDPOINT"
                                value: config.s3Endpoint
                            }
                            {
                                name: "SOUNDS_S3_PREFIX"
                                value: config.s3Prefix
                            }
                            {
                                name: "SOUNDS_S3_REGION"
                                value: config.s3Region
                            }
                            {
                                name: "SOUNDS_S3_INTERVAL"
                                value: config.s3Interval
                            }
                            {
                                name: "SOUNDS_S3_ANONYMOUS"
                                value: "true" if config.s3Anonymous else "false"
                            }
                            {
                                # Optional, like REDIS_PASSWORD: no Secret, no
                                # credentials (anonymous or a public bucket).
                                name: "AWS_ACCESS_KEY_ID"
                                valueFrom: {
                                    secretKeyRef: {
                                        name: "{}-s3".format(config.name)
                                        key: "accessKey"
                                        optional: True
                                    }
                                }
                            }
                            {
                                # Optional, like REDIS_PASSWORD: no Secret, no
                                # credentials (anonymous or a public bucket).
                                name: "AWS_SECRET_ACCESS_KEY"
                                valueFrom: {
                                    secretKeyRef: {
                                        name: "{}-s3".format(config.name)
                                        key: "secretKey"
                                        optional: True
                                    }
                                }
                            }
                            {
                                # The root filesystem is read-only; synthesized
                                # speech goes to the /tmp emptyDir.
                                name: "TTS_CACHE_DIR"
                                value: "/tmp/sound-catcher"
                            }
                        ]
                        envFrom: [
                            {
                                configMapRef: {
                                    name: "{}-config".format(config.name)
                                }
                            }
                        ]
                        resources: {
                            requests: {
                                cpu: config.cpuRequest
                                memory: config.memoryRequest
                            }
                            limits: {
                                cpu: config.cpuLimit
                                memory: config.memoryLimit
                            }
                        }
                        livenessProbe: {
                            httpGet: {
                                path: "/healthz"
                                port: "http"
                            }
                            # Loading the Piper voice takes a moment.
                            initialDelaySeconds: 15
                            periodSeconds: 10
                            timeoutSeconds: 5
                            failureThreshold: 3
                        }
                        readinessProbe: {
                            httpGet: {
                                path: "/healthz"
                                port: "http"
                            }
                            initialDelaySeconds: 5
                            periodSeconds: 5
                            timeoutSeconds: 3
                            failureThreshold: 3
                        }
                        securityContext: {
                            readOnlyRootFilesystem: True
                            runAsNonRoot: True
                            allowPrivilegeEscalation: False
                            capabilities: {
                                drop: ["ALL"]
                            }
                        }
                        volumeMounts: [
                            {
                                name: "tmp"
                                mountPath: "/tmp"
                            }
                            {
                                name: "profile"
                                mountPath: "/config"
                                readOnly: True
                            }
                        ]
                    }
                ]
                securityContext: {
                    runAsNonRoot: True
                    runAsUser: 65532
                    runAsGroup: 65532
                    fsGroup: 65532
                    seccompProfile: {
                        type: "RuntimeDefault"
                    }
                }
                volumes: [
                    {
                        name: "tmp"
                        emptyDir: {
                            sizeLimit: "256Mi"
                        }
                    }
                    {
                        name: "profile"
                        configMap: {
                            name: "{}-profile".format(config.name)
                        }
                    }
                ]
            }
        }
    }
}

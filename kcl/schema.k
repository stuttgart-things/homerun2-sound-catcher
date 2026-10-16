"""
Schema definition for homerun2-sound-catcher deployment configuration

In the cluster there is no speaker: the catcher runs with the browser sink
only, and the player page (behind the HTTPRoute) is where it is heard.
"""

schema SoundCatcher:
    """Configuration for homerun2-sound-catcher Kubernetes deployment"""

    # Metadata
    name: str = "homerun2-sound-catcher"
    namespace: str = "homerun2"

    # Image
    image: str = "ghcr.io/stuttgart-things/homerun2-sound-catcher:latest"
    imagePullPolicy: str = "IfNotPresent"

    # One replica, always: the history the page shows lives in the pod, and
    # two pods in one consumer group would each get half the messages.
    replicas: int = 1
    # Piper and its voice need a few hundred MB while speaking.
    cpuRequest: str = "100m"
    cpuLimit: str = "1000m"
    memoryRequest: str = "192Mi"
    memoryLimit: str = "512Mi"

    # Redis
    redisAddr: str = "redis-stack.homerun2.svc.cluster.local"
    redisPort: int | str = "6379"
    redisStreams: str = "messages"

    # Consumer group
    consumerGroup: str = "homerun2-sound-catcher"

    # full: consume Redis. standalone: no Redis, only POST /play.
    soundMode: str = "full"

    # Announcements kept and shown on the player page
    historySize: int | str = "20"

    # Health endpoint
    healthPort: int | str = "8080"

    # Service
    serviceType: str = "ClusterIP"
    servicePort: int = 80
    containerPort: int = 8080

    # HTTPRoute (Gateway API)
    httpRouteEnabled: bool = False
    httpRouteParentRefName: str = ""
    httpRouteParentRefNamespace: str = ""
    httpRouteHostname: str = ""
    httpRouteAnnotations: {str:str} = {}
    # Request timeout for /api/events (SSE), e.g. "0s" for none. Empty: the
    # gateway's default, and no timeouts field (not every gateway has it).
    httpRouteSseTimeout: str = ""

    # Secrets
    redisPassword: str = ""

    # Bearer token for POST /play and mute. Empty: no Secret, and the write
    # endpoints are not registered.
    apiToken: str = ""

    # Sounds from an S3 bucket (MinIO, Ceph, AWS). Empty bucket: no sync,
    # only the clips bundled in the image.
    s3Bucket: str = ""
    s3Endpoint: str = ""
    s3Prefix: str = ""
    s3Region: str = "us-east-1"
    s3Interval: str = "5m"
    # Public bucket, no credentials.
    s3Anonymous: bool = False
    # Read-only credentials. Set: the -s3 Secret is created. Empty: the pod
    # still reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from a Secret of
    # that name if something else (ESO) provides it.
    s3AccessKey: str = ""
    s3SecretKey: str = ""

    # Profile path inside container
    profilePath: str = "/config/profile.yaml"

    # Profile data (sound rules YAML content)
    profileData: str = """\
quietHours:
  from: "18:00"
  to: "08:00"
  weekends: true
  timezone: Europe/Berlin
  allow: [critical]
rules:
  critical:
    severity: [critical]
    sound: alarm.wav
    say: "Kritisch! {{ system }}: {{ title }}"
  errors:
    severity: [error]
    sound: chime.wav
    say: "Fehler auf {{ system }}: {{ title }}"
    volume: 85
  warnings:
    severity: [warning]
    sound: ding.wav
    say: "Warnung von {{ system }}: {{ title }}"
    volume: 70
  success:
    severity: [success]
    sound: success.wav
    volume: 60
  quiet:
    severity: [info, debug]
    silent: true
"""

    # Extra environment variables
    extraEnvVars: {str:str} = {}

    # Labels and annotations
    labels: {str:str} = {}
    annotations: {str:str} = {}

    check:
        replicas in [0, 1], "replicas must be 0 or 1: the history lives in the pod"
        servicePort >= 1 and servicePort <= 65535, "servicePort must be valid"
        containerPort >= 1 and containerPort <= 65535, "containerPort must be valid"
        name != "", "name must not be empty"
        namespace != "", "namespace must not be empty"
        soundMode in ["full", "standalone"], "soundMode must be full or standalone"
        bool(s3AccessKey) == bool(s3SecretKey), "s3AccessKey and s3SecretKey go together"

"""
Common labels for homerun2-sound-catcher resources
"""

import schema

# Get command-line options
_image = option("config.image")
_namespace = option("config.namespace")
_redisAddr = option("config.redisAddr")
_redisPort = option("config.redisPort")
_redisStreams = option("config.redisStreams")
_consumerGroup = option("config.consumerGroup")
_redisPassword = option("config.redisPassword")
_apiToken = option("config.apiToken")
_healthPort = option("config.healthPort")
_soundMode = option("config.soundMode")
_historySize = option("config.historySize")
_httpRouteEnabled = option("config.httpRouteEnabled")
_httpRouteParentRefName = option("config.httpRouteParentRefName")
_httpRouteParentRefNamespace = option("config.httpRouteParentRefNamespace")
_httpRouteHostname = option("config.httpRouteHostname")
_httpRouteAnnotations = option("config.httpRouteAnnotations") or {}
_httpRouteSseTimeout = option("config.httpRouteSseTimeout")
_extraEnvVars = option("config.extraEnvVars") or {}

# Config instance with command-line overrides
config: schema.SoundCatcher = schema.SoundCatcher {
    if _image:
        image = _image
    if _namespace:
        namespace = _namespace
    if _redisAddr:
        redisAddr = _redisAddr
    if _redisPort:
        redisPort = _redisPort
    if _redisStreams:
        redisStreams = _redisStreams
    if _consumerGroup:
        consumerGroup = _consumerGroup
    if _redisPassword:
        redisPassword = _redisPassword
    if _apiToken:
        apiToken = _apiToken
    if _healthPort:
        healthPort = _healthPort
    if _soundMode:
        soundMode = _soundMode
    if _historySize:
        historySize = _historySize
    if _httpRouteEnabled:
        httpRouteEnabled = _httpRouteEnabled in [True, "True", "true"]
    if _httpRouteParentRefName:
        httpRouteParentRefName = _httpRouteParentRefName
    if _httpRouteParentRefNamespace:
        httpRouteParentRefNamespace = _httpRouteParentRefNamespace
    if _httpRouteHostname:
        httpRouteHostname = _httpRouteHostname
    if _httpRouteAnnotations:
        httpRouteAnnotations = _httpRouteAnnotations
    if _httpRouteSseTimeout:
        httpRouteSseTimeout = _httpRouteSseTimeout
    if _extraEnvVars:
        extraEnvVars = _extraEnvVars
}

# Common labels applied to all resources
commonLabels = {
    "app.kubernetes.io/name": config.name
    "app.kubernetes.io/instance": config.name
    "app.kubernetes.io/component": "sound-catcher"
    "app.kubernetes.io/part-of": "homerun2"
    **config.labels
}

# Selector labels for pods
selectorLabels = {
    "app.kubernetes.io/name": config.name
    "app.kubernetes.io/instance": config.name
}

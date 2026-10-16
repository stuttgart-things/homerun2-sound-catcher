"""
Profile ConfigMap for homerun2-sound-catcher
Contains the sound rules YAML mounted at /config/profile.yaml
"""

import labels

config = labels.config

profileConfigMap = {
    apiVersion: "v1"
    kind: "ConfigMap"
    metadata: {
        name: "{}-profile".format(config.name)
        namespace: config.namespace
        labels: labels.commonLabels
    }
    data: {
        "profile.yaml": config.profileData
    }
}
